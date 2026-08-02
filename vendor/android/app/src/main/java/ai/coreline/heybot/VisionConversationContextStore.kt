package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class VisionConversationContext(
    val schemaVersion: Int = SCHEMA_VERSION,
    val chatId: Long,
    val ownerUserId: Long,
    val sourceLogId: Long,
    val resultLogId: Long,
    val task: VisionTask,
    val safeAnswer: String,
    val uncertainty: String,
    val capabilityRevision: Long,
    val createdAtMillis: Long,
    val expiresAtMillis: Long
) {
    companion object {
        const val SCHEMA_VERSION = 1
    }
}

data class VisionConversationContextStats(
    val ready: Boolean,
    val contexts: Int,
    val lastPersistSucceeded: Boolean?
)

/**
 * Bounded image-analysis context. Only safety-filtered text and numeric
 * correlation metadata are retained; image bytes and signed URLs never enter
 * this store.
 */
class VisionConversationContextStore(
    private val backend: ConversationMemoryBackend? = null,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val maxPerOwner: Int = DEFAULT_MAX_PER_OWNER,
    private val maxContexts: Int = DEFAULT_MAX_CONTEXTS,
    private val maxBytes: Int = DEFAULT_MAX_BYTES,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
) {
    private val contexts = mutableListOf<VisionConversationContext>()
    private var persistenceAvailable = true
    private var lastPersistSucceeded: Boolean? = null

    init {
        require(ttlMillis > 0L)
        require(maxPerOwner in 1..MAX_PER_OWNER_LIMIT)
        require(maxContexts in maxPerOwner..MAX_CONTEXTS_LIMIT)
        require(maxBytes in MIN_BYTES..MAX_BYTES_LIMIT)
        load()
    }

    @Synchronized
    fun put(context: VisionConversationContext): Boolean {
        if (!persistenceAvailable || !isValid(context)) return false
        val now = nowMillis()
        val normalized = context.copy(
            safeAnswer = context.safeAnswer.trim(),
            uncertainty = context.uncertainty.trim(),
            expiresAtMillis = minOf(context.expiresAtMillis, context.createdAtMillis + ttlMillis)
        )
        if (!isValid(normalized) || normalized.expiresAtMillis <= now) return false

        val before = contexts.toList()
        prune(now)
        contexts.removeAll { it.chatId == normalized.chatId && it.resultLogId == normalized.resultLogId }
        contexts += normalized
        trim()
        return persistOrRollback(before, now)
    }

    @Synchronized
    fun findOwned(
        chatId: Long,
        userId: Long,
        capabilityRevision: Long,
        now: Long = nowMillis()
    ): VisionConversationContext? {
        if (!persistenceAvailable) return null
        prune(now)
        return contexts.asReversed().firstOrNull {
            it.chatId == chatId &&
                it.ownerUserId == userId &&
                it.capabilityRevision == capabilityRevision &&
                it.expiresAtMillis > now
        }
    }

    @Synchronized
    fun findExact(
        chatId: Long,
        resultLogId: Long,
        capabilityRevision: Long,
        now: Long = nowMillis()
    ): VisionConversationContext? {
        if (!persistenceAvailable || resultLogId <= 0L) return null
        prune(now)
        return contexts.asReversed().firstOrNull {
            it.chatId == chatId &&
                it.resultLogId == resultLogId &&
                it.capabilityRevision == capabilityRevision &&
                it.expiresAtMillis > now
        }
    }

    /**
     * Returns immutable newest-first candidates for semantic follow-up routing.
     * The caller still has to enforce owner/shared-window and message relevance.
     */
    @Synchronized
    fun findRecentInRoom(
        chatId: Long,
        capabilityRevision: Long,
        now: Long = nowMillis()
    ): List<VisionConversationContext> {
        if (!persistenceAvailable) return emptyList()
        prune(now)
        return contexts.asReversed().filter {
            it.chatId == chatId &&
                it.capabilityRevision == capabilityRevision &&
                it.expiresAtMillis > now
        }
    }

    @Synchronized
    fun clear(chatId: Long, userId: Long): Boolean = mutateAndPersist {
        removeAll { it.chatId == chatId && it.ownerUserId == userId }
    }

    @Synchronized
    fun clearUser(userId: Long): Boolean = mutateAndPersist {
        removeAll { it.ownerUserId == userId }
    }

    @Synchronized
    fun clearAll(): Boolean = mutateAndPersist { clear() }

    @Synchronized
    fun stats(now: Long = nowMillis()): VisionConversationContextStats {
        prune(now)
        return VisionConversationContextStats(
            ready = persistenceAvailable,
            contexts = if (persistenceAvailable) contexts.size else 0,
            lastPersistSucceeded = lastPersistSucceeded
        )
    }

    private fun mutateAndPersist(mutation: MutableList<VisionConversationContext>.() -> Unit): Boolean {
        if (!persistenceAvailable) return false
        val before = contexts.toList()
        contexts.mutation()
        return persistOrRollback(before, nowMillis())
    }

    private fun persistOrRollback(before: List<VisionConversationContext>, now: Long): Boolean {
        val target = backend ?: return true
        val encoded = encode(now)
        if (encoded == null) {
            contexts.clear()
            contexts.addAll(before)
            lastPersistSucceeded = false
            return false
        }
        return runCatching { target.write(encoded) }
            .fold(
                onSuccess = {
                    lastPersistSucceeded = true
                    true
                },
                onFailure = {
                    contexts.clear()
                    contexts.addAll(before)
                    persistenceAvailable = false
                    lastPersistSucceeded = false
                    log("Vision context persist failed: ${it::class.simpleName}")
                    false
                }
            )
    }

    private fun load() {
        val bytes = runCatching { backend?.read() }
            .onFailure {
                persistenceAvailable = false
                lastPersistSucceeded = false
                log("Vision context load failed: ${it::class.simpleName}")
            }
            .getOrNull() ?: return
        if (bytes.size > maxBytes) {
            quarantine("oversized")
            return
        }
        val document = runCatching {
            json.decodeFromString<PersistedVisionContextDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            quarantine("invalid")
            return
        }
        if (document.schemaVersion != VisionConversationContext.SCHEMA_VERSION) {
            quarantine("unsupported-version")
            return
        }
        contexts += document.contexts.mapNotNull(::decode).filter { isValid(it) }
        prune(nowMillis())
        trim()
        lastPersistSucceeded = true
    }

    private fun quarantine(reason: String) {
        runCatching { backend?.quarantine(nowMillis()) }
        contexts.clear()
        persistenceAvailable = false
        lastPersistSucceeded = false
        log("Vision context quarantined: $reason")
    }

    private fun trim() {
        contexts.sortBy { it.createdAtMillis }
        val owners = contexts.groupBy { it.chatId to it.ownerUserId }
        owners.values.forEach { owned ->
            owned.dropLast(maxPerOwner).forEach(contexts::remove)
        }
        while (contexts.size > maxContexts) contexts.removeAt(0)
        while (contexts.isNotEmpty() && encode(nowMillis()) == null) contexts.removeAt(0)
    }

    private fun prune(now: Long) {
        contexts.removeAll { it.expiresAtMillis <= now }
    }

    private fun encode(now: Long): ByteArray? {
        val bytes = json.encodeToString(
            PersistedVisionContextDocument(
                schemaVersion = VisionConversationContext.SCHEMA_VERSION,
                updatedAtMillis = now,
                contexts = contexts.map(::encode)
            )
        ).toByteArray(Charsets.UTF_8)
        return bytes.takeIf { it.size <= maxBytes }
    }

    private fun isValid(context: VisionConversationContext): Boolean =
        context.schemaVersion == VisionConversationContext.SCHEMA_VERSION &&
            context.chatId > 0L && context.ownerUserId > 0L &&
            context.sourceLogId > 0L && context.resultLogId > 0L &&
            context.safeAnswer.isNotBlank() && context.safeAnswer.length <= MAX_ANSWER_CHARS &&
            context.uncertainty.length <= MAX_UNCERTAINTY_CHARS &&
            context.capabilityRevision >= 0L && context.createdAtMillis >= 0L &&
            context.expiresAtMillis > context.createdAtMillis

    private fun encode(context: VisionConversationContext) = PersistedVisionContext(
        chatId = context.chatId.toString(),
        ownerUserId = context.ownerUserId.toString(),
        sourceLogId = context.sourceLogId.toString(),
        resultLogId = context.resultLogId.toString(),
        task = context.task.wireValue,
        safeAnswer = context.safeAnswer,
        uncertainty = context.uncertainty,
        capabilityRevision = context.capabilityRevision,
        createdAtMillis = context.createdAtMillis,
        expiresAtMillis = context.expiresAtMillis
    )

    private fun decode(value: PersistedVisionContext): VisionConversationContext? {
        return VisionConversationContext(
            chatId = value.chatId.toLongOrNull() ?: return null,
            ownerUserId = value.ownerUserId.toLongOrNull() ?: return null,
            sourceLogId = value.sourceLogId.toLongOrNull() ?: return null,
            resultLogId = value.resultLogId.toLongOrNull() ?: return null,
            task = VisionTask.fromWire(value.task) ?: return null,
            safeAnswer = value.safeAnswer,
            uncertainty = value.uncertainty,
            capabilityRevision = value.capabilityRevision,
            createdAtMillis = value.createdAtMillis,
            expiresAtMillis = value.expiresAtMillis
        )
    }

    companion object {
        const val DEFAULT_TTL_MILLIS = 30L * 60L * 1_000L
        const val DEFAULT_MAX_PER_OWNER = 3
        const val DEFAULT_MAX_CONTEXTS = 128
        const val DEFAULT_MAX_BYTES = 1024 * 1024
        const val MAX_ANSWER_CHARS = 480
        private const val MAX_UNCERTAINTY_CHARS = 80
        private const val MAX_PER_OWNER_LIMIT = 20
        private const val MAX_CONTEXTS_LIMIT = 2_000
        private const val MIN_BYTES = 4_096
        private const val MAX_BYTES_LIMIT = 10 * 1024 * 1024
    }
}

@Serializable
private data class PersistedVisionContextDocument(
    val schemaVersion: Int,
    val updatedAtMillis: Long,
    val contexts: List<PersistedVisionContext>
)

@Serializable
private data class PersistedVisionContext(
    val chatId: String,
    val ownerUserId: String,
    val sourceLogId: String,
    val resultLogId: String,
    val task: String,
    val safeAnswer: String,
    val uncertainty: String,
    val capabilityRevision: Long,
    val createdAtMillis: Long,
    val expiresAtMillis: Long
)
