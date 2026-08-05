package ai.coreline.heybot

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class AudioDeliveryPart(
    val text: String,
    val confirmedLogId: Long? = null,
    val attempts: Int = 0
)

/** Persisted output state: every Kakao text part is committed independently. */
data class AudioDeliveryState(
    val safeSummary: String,
    val evidenceIds: List<String>,
    val parts: List<AudioDeliveryPart>
)

data class LocalAudioJob(
    val jobId: String,
    val requestId: String,
    val chatId: Long,
    val userId: Long,
    val sourceLogId: Long,
    val status: String,
    val profile: AudioSummaryProfile,
    val engine: ConversationEngine,
    val roomCapabilityRevision: Long,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long,
    val delivery: AudioDeliveryState? = null
)

interface AudioAnalysisStateStore {
    suspend fun initialize()
    suspend fun upsert(job: LocalAudioJob): Boolean
    suspend fun latest(chatId: Long, userId: Long): LocalAudioJob?
    suspend fun pending(): List<LocalAudioJob>
    suspend fun remove(jobId: String): Boolean
}

class InMemoryAudioAnalysisStateStore : AudioAnalysisStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalAudioJob>()
    override suspend fun initialize() = Unit
    override suspend fun upsert(job: LocalAudioJob): Boolean = mutex.withLock {
        jobs[job.jobId] = job
        true
    }
    override suspend fun latest(chatId: Long, userId: Long): LocalAudioJob? = mutex.withLock {
        jobs.values.filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }
    override suspend fun pending(): List<LocalAudioJob> = mutex.withLock {
        jobs.values.filter { it.status in AUDIO_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }
    override suspend fun remove(jobId: String): Boolean = mutex.withLock { jobs.remove(jobId) != null }
}

class AtomicJsonAudioAnalysisStateStore(
    private val backend: ConversationMemoryBackend,
    private val maxEntries: Int = 100,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
) : AudioAnalysisStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalAudioJob>()
    private var initialized = false

    override suspend fun initialize() = mutex.withLock {
        if (initialized) return@withLock
        initialized = true
        val bytes = runCatching { backend.read() }.getOrNull() ?: return@withLock
        val document = runCatching {
            json.decodeFromString<PersistedAudioDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("Audio job state quarantined: invalid")
            return@withLock
        }
        if (document.version !in 1..VERSION) return@withLock
        document.jobs.mapNotNull(::decode).sortedBy { it.updatedAtMillis }
            .takeLast(maxEntries).forEach { jobs[it.jobId] = it }
    }

    override suspend fun upsert(job: LocalAudioJob): Boolean = mutex.withLock {
        check(initialized)
        jobs[job.jobId] = job
        while (jobs.size > maxEntries) {
            jobs.remove(jobs.minByOrNull { it.value.updatedAtMillis }?.key ?: break)
        }
        persist()
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalAudioJob? = mutex.withLock {
        check(initialized)
        jobs.values.filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalAudioJob> = mutex.withLock {
        check(initialized)
        jobs.values.filter { it.status in AUDIO_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun remove(jobId: String): Boolean = mutex.withLock {
        check(initialized)
        val changed = jobs.remove(jobId) != null
        if (!changed) true else persist()
    }

    private fun decode(value: PersistedAudioJob): LocalAudioJob? {
        val chatId = value.chatId.toLongOrNull()?.takeIf { it > 0L } ?: return null
        val userId = value.userId.toLongOrNull()?.takeIf { it > 0L } ?: return null
        val sourceLogId = value.sourceLogId.toLongOrNull()?.takeIf { it > 0L } ?: return null
        val pattern = runCatching { AudioSummaryPattern.valueOf(value.pattern) }.getOrNull() ?: return null
        val view = runCatching { AudioSummaryView.valueOf(value.view) }.getOrNull() ?: return null
        val engine = runCatching { ConversationEngine.valueOf(value.engine) }.getOrNull() ?: return null
        if (value.jobId.isBlank() || value.requestId.isBlank()) return null
        return LocalAudioJob(
            value.jobId, value.requestId, chatId, userId, sourceLogId, value.status,
            AudioSummaryProfile(pattern, view), engine, value.roomCapabilityRevision,
            value.createdAtMillis, value.deadlineAtMillis, value.updatedAtMillis,
            value.delivery?.let(::decodeDelivery)
        )
    }

    private fun persist(): Boolean = runCatching {
        val document = PersistedAudioDocument(
            VERSION,
            jobs.values.map {
                PersistedAudioJob(
                    it.jobId, it.requestId, it.chatId.toString(), it.userId.toString(),
                    it.sourceLogId.toString(), it.status, it.profile.pattern.name,
                    it.profile.view.name, it.engine.name, it.roomCapabilityRevision,
                    it.createdAtMillis, it.deadlineAtMillis, it.updatedAtMillis,
                    it.delivery?.let(::encodeDelivery)
                )
            }
        )
        backend.write(json.encodeToString(document).toByteArray(Charsets.UTF_8))
    }.fold(
        onSuccess = { true },
        onFailure = { log("Audio job state persist failed: ${it::class.simpleName}"); false }
    )

    private fun encodeDelivery(value: AudioDeliveryState) = PersistedAudioDelivery(
        safeSummary = value.safeSummary.take(MAX_SAFE_SUMMARY_CHARS),
        evidenceIds = value.evidenceIds.filter { it.matches(SEGMENT_ID) }.distinct().take(MAX_EVIDENCE_IDS),
        parts = value.parts.take(MultipartTextDelivery.MAX_PARTS).map {
            PersistedAudioDeliveryPart(
                text = it.text.take(MultipartTextDelivery.MAX_PART_CHARS),
                confirmedLogId = it.confirmedLogId?.takeIf { id -> id >= 0L }?.toString(),
                attempts = it.attempts.coerceIn(0, MAX_DELIVERY_ATTEMPTS)
            )
        }
    )

    private fun decodeDelivery(value: PersistedAudioDelivery): AudioDeliveryState? {
        if (value.safeSummary.isBlank() || value.safeSummary.length > MAX_SAFE_SUMMARY_CHARS ||
            value.parts.isEmpty() || value.parts.size > MultipartTextDelivery.MAX_PARTS ||
            value.evidenceIds.size > MAX_EVIDENCE_IDS ||
            value.evidenceIds.any { !it.matches(SEGMENT_ID) }
        ) return null
        val parts = buildList {
            value.parts.forEach { part ->
                if (part.text.isBlank() || part.text.length > MultipartTextDelivery.MAX_PART_CHARS ||
                    part.attempts !in 0..MAX_DELIVERY_ATTEMPTS
                ) return null
                val confirmedLogId = part.confirmedLogId?.toLongOrNull()?.takeIf { it >= 0L }
                if (part.confirmedLogId != null && confirmedLogId == null) return null
                add(AudioDeliveryPart(part.text, confirmedLogId, part.attempts))
            }
        }
        return AudioDeliveryState(value.safeSummary, value.evidenceIds.distinct(), parts)
    }

    private companion object {
        const val VERSION = 2
        const val MAX_SAFE_SUMMARY_CHARS = 3_200
        const val MAX_EVIDENCE_IDS = 64
        const val MAX_DELIVERY_ATTEMPTS = 9
        val SEGMENT_ID = Regex("S[0-9]{4}")
    }
}

@Serializable private data class PersistedAudioDocument(
    val version: Int,
    val jobs: List<PersistedAudioJob>
)
@Serializable private data class PersistedAudioJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val userId: String,
    val sourceLogId: String,
    val status: String,
    val pattern: String,
    val view: String,
    val engine: String,
    val roomCapabilityRevision: Long,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long,
    val delivery: PersistedAudioDelivery? = null
)

@Serializable private data class PersistedAudioDelivery(
    val safeSummary: String,
    val evidenceIds: List<String>,
    val parts: List<PersistedAudioDeliveryPart>
)

@Serializable private data class PersistedAudioDeliveryPart(
    val text: String,
    val confirmedLogId: String? = null,
    val attempts: Int = 0
)

private val AUDIO_PENDING_STATUSES = setOf(
    "queued", "fetching", "validating", "normalizing", "transcribing", "summarizing", "delivery_pending"
)
