package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Post-summary context has a deliberately separate lifecycle from transcript
 * jobs. It retains only safety-filtered summary text and DB-confirmed output
 * IDs; source media, transcript text and signed URLs are never persisted.
 */
data class AudioConversationContext(
    val schemaVersion: Int = SCHEMA_VERSION,
    val chatId: Long,
    val ownerUserId: Long,
    val jobId: String,
    val sourceLogId: Long,
    val resultLogIds: List<Long>,
    val profile: AudioSummaryProfile,
    val safeSummary: String,
    val evidenceIds: List<String>,
    val capabilityRevision: Long,
    val createdAtMillis: Long,
    val expiresAtMillis: Long
) {
    companion object { const val SCHEMA_VERSION = 1 }
}

data class AudioConversationContextStats(
    val ready: Boolean,
    val contexts: Int,
    val lastPersistSucceeded: Boolean?
)

class AudioConversationContextStore(
    private val backend: ConversationMemoryBackend? = null,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val maxPerOwner: Int = DEFAULT_MAX_PER_OWNER,
    private val maxContexts: Int = DEFAULT_MAX_CONTEXTS,
    private val maxBytes: Int = DEFAULT_MAX_BYTES,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
) {
    private val contexts = mutableListOf<AudioConversationContext>()
    private var persistenceAvailable = true
    private var lastPersistSucceeded: Boolean? = null

    init {
        require(ttlMillis > 0L)
        require(maxPerOwner in 1..20)
        require(maxContexts in maxPerOwner..2_000)
        require(maxBytes in 4_096..10 * 1024 * 1024)
        load()
    }

    @Synchronized
    fun put(context: AudioConversationContext): Boolean {
        if (!persistenceAvailable || !isValid(context)) return false
        val now = nowMillis()
        val normalized = context.copy(
            safeSummary = context.safeSummary.trim(),
            evidenceIds = context.evidenceIds.distinct(),
            resultLogIds = context.resultLogIds.distinct(),
            expiresAtMillis = minOf(context.expiresAtMillis, context.createdAtMillis + ttlMillis)
        )
        if (!isValid(normalized) || normalized.expiresAtMillis <= now) return false
        val before = contexts.toList()
        prune(now)
        contexts.removeAll { it.chatId == normalized.chatId && it.jobId == normalized.jobId }
        contexts += normalized
        trim()
        return persistOrRollback(before, now)
    }

    @Synchronized
    fun findOwned(chatId: Long, userId: Long, revision: Long, now: Long = nowMillis()): AudioConversationContext? {
        if (!persistenceAvailable) return null
        prune(now)
        return contexts.asReversed().firstOrNull {
            it.chatId == chatId && it.ownerUserId == userId && it.capabilityRevision == revision && it.expiresAtMillis > now
        }
    }

    @Synchronized
    fun findExact(chatId: Long, resultLogId: Long, revision: Long, now: Long = nowMillis()): AudioConversationContext? {
        if (!persistenceAvailable || resultLogId <= 0L) return null
        prune(now)
        return contexts.asReversed().firstOrNull {
            it.chatId == chatId && resultLogId in it.resultLogIds && it.capabilityRevision == revision && it.expiresAtMillis > now
        }
    }

    @Synchronized
    fun findRecentInRoom(chatId: Long, revision: Long, now: Long = nowMillis()): List<AudioConversationContext> {
        if (!persistenceAvailable) return emptyList()
        prune(now)
        return contexts.asReversed().filter {
            it.chatId == chatId && it.capabilityRevision == revision && it.expiresAtMillis > now
        }
    }

    @Synchronized
    fun removeJob(chatId: Long, jobId: String): Boolean = mutateAndPersist {
        removeAll { it.chatId == chatId && it.jobId == jobId }
    }

    @Synchronized
    fun clear(chatId: Long, userId: Long): Boolean = mutateAndPersist {
        removeAll { it.chatId == chatId && it.ownerUserId == userId }
    }

    @Synchronized
    fun clearUser(userId: Long): Boolean = mutateAndPersist { removeAll { it.ownerUserId == userId } }

    @Synchronized
    fun clearAll(): Boolean = mutateAndPersist { clear() }

    @Synchronized
    fun stats(now: Long = nowMillis()): AudioConversationContextStats {
        prune(now)
        return AudioConversationContextStats(persistenceAvailable, if (persistenceAvailable) contexts.size else 0, lastPersistSucceeded)
    }

    private fun mutateAndPersist(mutation: MutableList<AudioConversationContext>.() -> Unit): Boolean {
        if (!persistenceAvailable) return false
        val before = contexts.toList()
        contexts.mutation()
        return persistOrRollback(before, nowMillis())
    }

    private fun persistOrRollback(before: List<AudioConversationContext>, now: Long): Boolean {
        val target = backend ?: return true
        val encoded = encode(now)
        if (encoded == null) {
            contexts.clear(); contexts.addAll(before); lastPersistSucceeded = false
            return false
        }
        return runCatching { target.write(encoded) }.fold(
            onSuccess = { lastPersistSucceeded = true; true },
            onFailure = {
                contexts.clear(); contexts.addAll(before)
                persistenceAvailable = false; lastPersistSucceeded = false
                log("Audio context persist failed: ${it::class.simpleName}")
                false
            }
        )
    }

    private fun load() {
        val bytes = runCatching { backend?.read() }.onFailure {
            persistenceAvailable = false; lastPersistSucceeded = false
            log("Audio context load failed: ${it::class.simpleName}")
        }.getOrNull() ?: return
        if (bytes.size > maxBytes) return quarantine("oversized")
        val document = runCatching {
            json.decodeFromString<PersistedAudioContextDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse { return quarantine("invalid") }
        if (document.schemaVersion != AudioConversationContext.SCHEMA_VERSION) return quarantine("unsupported-version")
        contexts += document.contexts.mapNotNull(::decode).filter(::isValid)
        prune(nowMillis()); trim(); lastPersistSucceeded = true
    }

    private fun quarantine(reason: String) {
        runCatching { backend?.quarantine(nowMillis()) }
        contexts.clear(); persistenceAvailable = false; lastPersistSucceeded = false
        log("Audio context quarantined: $reason")
    }

    private fun trim() {
        contexts.sortBy { it.createdAtMillis }
        contexts.groupBy { it.chatId to it.ownerUserId }.values.forEach { owned ->
            owned.dropLast(maxPerOwner).forEach(contexts::remove)
        }
        while (contexts.size > maxContexts) contexts.removeAt(0)
        while (contexts.isNotEmpty() && encode(nowMillis()) == null) contexts.removeAt(0)
    }

    private fun prune(now: Long) { contexts.removeAll { it.expiresAtMillis <= now } }

    private fun encode(now: Long): ByteArray? = json.encodeToString(
        PersistedAudioContextDocument(
            AudioConversationContext.SCHEMA_VERSION, now, contexts.map(::encode)
        )
    ).toByteArray(Charsets.UTF_8).takeIf { it.size <= maxBytes }

    private fun isValid(value: AudioConversationContext): Boolean =
        value.schemaVersion == AudioConversationContext.SCHEMA_VERSION &&
            value.chatId > 0L && value.ownerUserId > 0L && value.sourceLogId > 0L &&
            value.jobId.isNotBlank() && value.jobId.length <= MAX_JOB_ID_CHARS &&
            value.resultLogIds.size in 1..MultipartTextDelivery.MAX_PARTS && value.resultLogIds.all { it > 0L } &&
            value.safeSummary.isNotBlank() && value.safeSummary.length <= MAX_SUMMARY_CHARS &&
            value.evidenceIds.size <= MAX_EVIDENCE_IDS && value.evidenceIds.all { it.matches(SEGMENT_ID) } &&
            value.capabilityRevision >= 0L && value.createdAtMillis >= 0L && value.expiresAtMillis > value.createdAtMillis

    private fun encode(value: AudioConversationContext) = PersistedAudioContext(
        value.chatId.toString(), value.ownerUserId.toString(), value.jobId, value.sourceLogId.toString(),
        value.resultLogIds.map(Long::toString), value.profile.pattern.name, value.profile.view.name,
        value.safeSummary, value.evidenceIds, value.capabilityRevision, value.createdAtMillis, value.expiresAtMillis
    )

    private fun decode(value: PersistedAudioContext): AudioConversationContext? {
        val pattern = runCatching { AudioSummaryPattern.valueOf(value.pattern) }.getOrNull() ?: return null
        val view = runCatching { AudioSummaryView.valueOf(value.view) }.getOrNull() ?: return null
        val ids = value.resultLogIds.map(String::toLongOrNull)
        if (ids.any { it == null }) return null
        return AudioConversationContext(
            chatId = value.chatId.toLongOrNull() ?: return null,
            ownerUserId = value.ownerUserId.toLongOrNull() ?: return null,
            jobId = value.jobId,
            sourceLogId = value.sourceLogId.toLongOrNull() ?: return null,
            resultLogIds = ids.filterNotNull(),
            profile = AudioSummaryProfile(pattern, view),
            safeSummary = value.safeSummary,
            evidenceIds = value.evidenceIds,
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
        const val MAX_SUMMARY_CHARS = 3_200
        private const val MAX_EVIDENCE_IDS = 64
        private const val MAX_JOB_ID_CHARS = 80
        private val SEGMENT_ID = Regex("S[0-9]{4}")
    }
}

@Serializable private data class PersistedAudioContextDocument(
    val schemaVersion: Int,
    val updatedAtMillis: Long,
    val contexts: List<PersistedAudioContext>
)

@Serializable private data class PersistedAudioContext(
    val chatId: String,
    val ownerUserId: String,
    val jobId: String,
    val sourceLogId: String,
    val resultLogIds: List<String>,
    val pattern: String,
    val view: String,
    val safeSummary: String,
    val evidenceIds: List<String>,
    val capabilityRevision: Long,
    val createdAtMillis: Long,
    val expiresAtMillis: Long
)
