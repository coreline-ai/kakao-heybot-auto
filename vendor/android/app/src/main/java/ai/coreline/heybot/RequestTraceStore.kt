package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.security.MessageDigest

enum class RequestTraceKind {
    UNKNOWN,
    WAKE_WORD,
    GENERAL_CONVERSATION,
    VISION_FOLLOW_UP,
    AUDIO_FOLLOW_UP,
    LOCAL_COMMAND,
    IMAGE,
    VIDEO,
    YOUTUBE_DOWNLOAD,
    PEN_BRUSH,
    VISION,
    AUDIO,
    DIAGNOSTICS
}

enum class RequestTraceStage {
    RECEIVED,
    CLASSIFIED,
    MODE_DISABLED,
    POLICY_ALLOWED,
    POLICY_DENIED,
    ADMITTED,
    DUPLICATE,
    RATE_LIMITED,
    QUEUE_FULL,
    PROVIDER_STARTED,
    PROVIDER_SUCCEEDED,
    PROVIDER_FAILED,
    SAFETY_PASSED,
    SAFETY_BLOCKED,
    ENQUEUED,
    DISPATCHED,
    DISPATCH_FAILED,
    DB_CONFIRMED,
    DB_CONFIRMED_LATE,
    UNCONFIRMED,
    FINISHED
}

data class RequestTrace(
    val traceId: String,
    val logId: Long,
    val chatId: Long,
    val kind: RequestTraceKind,
    val stage: RequestTraceStage,
    val reasonCode: String?,
    val engine: String?,
    val startedAtMillis: Long,
    val updatedAtMillis: Long,
    /** First terminal failure/policy reason. Delivery tracking may advance the
     * stage afterwards, but it must never erase the cause of the request. */
    val rootReasonCode: String? = null
)

object RequestTraceIds {
    fun from(chatId: Long, logId: Long): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest("$chatId:$logId".toByteArray(Charsets.UTF_8))
            .take(4)
            .joinToString("") { "%02X".format(it) }
        return "T-$digest"
    }
}

/**
 * Bounded metadata-only request history. Persistence errors never interrupt a
 * user feature; diagnostics continue in memory and expose only stable codes.
 */
class RequestTraceStore(
    private val backend: ConversationMemoryBackend? = null,
    private val maxEntries: Int = DEFAULT_MAX_ENTRIES,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
) {
    private val traces = linkedMapOf<String, RequestTrace>()
    private var persistenceAvailable = backend != null

    init {
        require(maxEntries in 1..MAX_ENTRIES_LIMIT)
        require(ttlMillis > 0L)
        load()
    }

    @Synchronized
    fun received(incoming: GlmIncomingMessage, kind: RequestTraceKind = RequestTraceKind.UNKNOWN) {
        val now = nowMillis()
        prune(now)
        traces[incoming.traceId] = RequestTrace(
            traceId = incoming.traceId,
            logId = incoming.logId,
            chatId = incoming.chatId,
            kind = kind,
            stage = RequestTraceStage.RECEIVED,
            reasonCode = null,
            engine = null,
            startedAtMillis = now,
            updatedAtMillis = now,
            rootReasonCode = null
        )
        trim()
        persist(now)
    }

    /**
     * Registers the shared DB event once. ObserverHelper and individual
     * coordinators may both see the same event, so an existing trace must not
     * be reset to RECEIVED after a later stage was already recorded.
     */
    @Synchronized
    fun ensureReceived(
        incoming: GlmIncomingMessage,
        kind: RequestTraceKind = RequestTraceKind.UNKNOWN
    ) {
        val existing = traces[incoming.traceId]
        if (existing != null) {
            if (existing.kind == RequestTraceKind.UNKNOWN && kind != RequestTraceKind.UNKNOWN) {
                record(incoming.traceId, existing.stage, kind = kind)
            }
            return
        }
        received(incoming, kind)
    }

    @Synchronized
    fun record(
        traceId: String,
        stage: RequestTraceStage,
        kind: RequestTraceKind? = null,
        reasonCode: String? = null,
        engine: String? = null
    ) {
        val current = traces[traceId] ?: return
        val now = nowMillis()
        val sanitizedReason = reasonCode?.sanitizeCode()
        traces[traceId] = current.copy(
            kind = kind ?: current.kind,
            stage = stage,
            reasonCode = sanitizedReason,
            engine = engine?.sanitizeCode(),
            updatedAtMillis = now,
            rootReasonCode = current.rootReasonCode
                ?: sanitizedReason?.takeIf { stage in ROOT_REASON_STAGES }
        )
        prune(now)
        persist(now)
    }

    @Synchronized
    fun get(traceId: String): RequestTrace? {
        prune(nowMillis())
        return traces[traceId]
    }

    @Synchronized
    fun recent(chatId: Long, excludeDiagnostics: Boolean = true): RequestTrace? {
        prune(nowMillis())
        return traces.values.toList().asReversed().firstOrNull {
            it.chatId == chatId && (!excludeDiagnostics || it.kind != RequestTraceKind.DIAGNOSTICS)
        }
    }

    @Synchronized
    fun snapshot(): List<RequestTrace> {
        prune(nowMillis())
        return traces.values.toList()
    }

    @Synchronized
    fun isPersistenceAvailable(): Boolean = persistenceAvailable

    private fun load() {
        val bytes = runCatching { backend?.read() }
            .onFailure {
                persistenceAvailable = false
                log("Request trace load failed: ${it::class.simpleName}")
            }
            .getOrNull() ?: return
        val document = runCatching {
            if (bytes.size > MAX_BYTES) error("TRACE_OVERSIZED")
            json.decodeFromString<PersistedTraceDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            runCatching { backend?.quarantine(nowMillis()) }
            persistenceAvailable = false
            log("Request trace quarantined: invalid")
            return
        }
        if (document.schemaVersion !in MIN_SUPPORTED_SCHEMA_VERSION..SCHEMA_VERSION) {
            runCatching { backend?.quarantine(nowMillis()) }
            persistenceAvailable = false
            log("Request trace quarantined: unsupported-version")
            return
        }
        document.traces.mapNotNull(::decode).forEach { traces[it.traceId] = it }
        prune(nowMillis())
        trim()
    }

    private fun decode(value: PersistedRequestTrace): RequestTrace? {
        val logId = value.logId.toLongOrNull()?.takeIf { it >= 0L } ?: return null
        val chatId = value.chatId.toLongOrNull()?.takeIf { it > 0L } ?: return null
        val kind = runCatching { RequestTraceKind.valueOf(value.kind) }.getOrNull() ?: return null
        val stage = runCatching { RequestTraceStage.valueOf(value.stage) }.getOrNull() ?: return null
        if (!value.traceId.matches(TRACE_ID)) return null
        return RequestTrace(
            traceId = value.traceId,
            logId = logId,
            chatId = chatId,
            kind = kind,
            stage = stage,
            reasonCode = value.reasonCode?.sanitizeCode(),
            engine = value.engine?.sanitizeCode(),
            startedAtMillis = value.startedAtMillis.coerceAtLeast(0L),
            updatedAtMillis = value.updatedAtMillis.coerceAtLeast(0L),
            rootReasonCode = value.rootReasonCode?.sanitizeCode()
                ?: value.reasonCode?.sanitizeCode()?.takeIf { stage in ROOT_REASON_STAGES }
        )
    }

    private fun persist(now: Long) {
        val target = backend ?: return
        if (!persistenceAvailable) return
        val bytes = json.encodeToString(
            PersistedTraceDocument(
                schemaVersion = SCHEMA_VERSION,
                updatedAtMillis = now,
                traces = traces.values.map {
                    PersistedRequestTrace(
                        traceId = it.traceId,
                        logId = it.logId.toString(),
                        chatId = it.chatId.toString(),
                        kind = it.kind.name,
                        stage = it.stage.name,
                        reasonCode = it.reasonCode,
                        rootReasonCode = it.rootReasonCode,
                        engine = it.engine,
                        startedAtMillis = it.startedAtMillis,
                        updatedAtMillis = it.updatedAtMillis
                    )
                }
            )
        ).toByteArray(Charsets.UTF_8)
        if (bytes.size > MAX_BYTES) {
            persistenceAvailable = false
            log("Request trace persist disabled: oversized")
            return
        }
        runCatching { target.write(bytes) }.onFailure {
            persistenceAvailable = false
            log("Request trace persist failed: ${it::class.simpleName}")
        }
    }

    private fun prune(now: Long) {
        traces.entries.removeAll { now - it.value.updatedAtMillis > ttlMillis }
    }

    private fun trim() {
        while (traces.size > maxEntries) traces.remove(traces.keys.first())
    }

    private fun String.sanitizeCode(): String =
        uppercase().replace(Regex("[^A-Z0-9_.-]"), "_").take(MAX_CODE_LENGTH)

    companion object {
        const val DEFAULT_MAX_ENTRIES = 200
        const val DEFAULT_TTL_MILLIS = 24L * 60L * 60L * 1_000L
        private const val MAX_ENTRIES_LIMIT = 1_000
        private const val MAX_BYTES = 512 * 1_024
        private const val MAX_CODE_LENGTH = 64
        private const val MIN_SUPPORTED_SCHEMA_VERSION = 1
        private const val SCHEMA_VERSION = 2
        private val TRACE_ID = Regex("T-[0-9A-F]{8}")
        private val ROOT_REASON_STAGES = setOf(
            RequestTraceStage.MODE_DISABLED,
            RequestTraceStage.POLICY_DENIED,
            RequestTraceStage.DUPLICATE,
            RequestTraceStage.RATE_LIMITED,
            RequestTraceStage.QUEUE_FULL,
            RequestTraceStage.PROVIDER_FAILED,
            RequestTraceStage.SAFETY_BLOCKED,
            RequestTraceStage.DISPATCH_FAILED,
            RequestTraceStage.UNCONFIRMED
        )

        fun inMemory(
            nowMillis: () -> Long = System::currentTimeMillis
        ): RequestTraceStore = RequestTraceStore(nowMillis = nowMillis)
    }
}

@Serializable
private data class PersistedTraceDocument(
    val schemaVersion: Int,
    val updatedAtMillis: Long,
    val traces: List<PersistedRequestTrace>
)

@Serializable
private data class PersistedRequestTrace(
    val traceId: String,
    val logId: String,
    val chatId: String,
    val kind: String,
    val stage: String,
    val reasonCode: String? = null,
    val rootReasonCode: String? = null,
    val engine: String? = null,
    val startedAtMillis: Long,
    val updatedAtMillis: Long
)
