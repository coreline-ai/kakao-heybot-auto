package ai.coreline.heybot

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class LocalYoutubeDownloadJob(
    val jobId: String,
    val requestId: String,
    val chatId: Long,
    val userId: Long,
    val logId: Long,
    val status: String,
    val roomCapabilityRevision: Long = 0L,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long,
    val deliveryHandoffAtMillis: Long? = null,
    val deliveryConfirmationDeadlineAtMillis: Long? = null,
    val deliveryAttempt: Int = 0
)

interface YoutubeDownloadJobStateStore {
    suspend fun initialize()
    suspend fun upsert(job: LocalYoutubeDownloadJob): Boolean
    suspend fun latest(chatId: Long, userId: Long): LocalYoutubeDownloadJob?
    suspend fun pending(): List<LocalYoutubeDownloadJob>
    suspend fun countPending(chatId: Long): Int
}

class InMemoryYoutubeDownloadJobStateStore : YoutubeDownloadJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalYoutubeDownloadJob>()

    override suspend fun initialize() = Unit

    override suspend fun upsert(job: LocalYoutubeDownloadJob): Boolean = mutex.withLock {
        jobs[job.jobId] = job
        true
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalYoutubeDownloadJob? = mutex.withLock {
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalYoutubeDownloadJob> = mutex.withLock {
        jobs.values.filter { it.status in YOUTUBE_DOWNLOAD_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        jobs.values.count { it.chatId == chatId && it.status in YOUTUBE_DOWNLOAD_PENDING_STATUSES }
    }
}

class AtomicJsonYoutubeDownloadJobStateStore(
    private val backend: ConversationMemoryBackend,
    private val maxEntries: Int = 100,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }
) : YoutubeDownloadJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalYoutubeDownloadJob>()
    private var initialized = false

    override suspend fun initialize() = mutex.withLock {
        if (initialized) return@withLock
        initialized = true
        val bytes = runCatching { backend.read() }.getOrNull() ?: return@withLock
        val document = runCatching {
            json.decodeFromString<PersistedYoutubeDownloadJobDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("YoutubeDownload job state quarantined: invalid")
            return@withLock
        }
        if (document.version !in setOf(LEGACY_VERSION, VERSION)) {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("YoutubeDownload job state quarantined: unsupported-version")
            return@withLock
        }
        document.jobs.mapNotNull { decode(it, document.version) }
            .sortedByDescending { it.updatedAtMillis }
            .take(maxEntries)
            .reversed()
            .forEach { jobs[it.jobId] = it }
        // Persist the v1 -> v2 conversion before coordinators resume jobs.  A
        // process restart must not repeatedly reinterpret an old
        // `awaiting_unlock` job as a candidate to send again.
        if (document.version == LEGACY_VERSION) persist()
    }

    override suspend fun upsert(job: LocalYoutubeDownloadJob): Boolean = mutex.withLock {
        check(initialized)
        jobs[job.jobId] = job
        while (jobs.size > maxEntries) {
            val oldest = jobs.minByOrNull { it.value.updatedAtMillis }?.key ?: break
            jobs.remove(oldest)
        }
        persist()
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalYoutubeDownloadJob? = mutex.withLock {
        check(initialized)
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalYoutubeDownloadJob> = mutex.withLock {
        check(initialized)
        jobs.values.filter { it.status in YOUTUBE_DOWNLOAD_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        check(initialized)
        jobs.values.count { it.chatId == chatId && it.status in YOUTUBE_DOWNLOAD_PENDING_STATUSES }
    }

    private fun decode(
        value: PersistedYoutubeDownloadJob,
        documentVersion: Int
    ): LocalYoutubeDownloadJob? {
        val chatId = value.chatId.toLongOrNull()
        val userId = value.userId.toLongOrNull()
        val logId = value.logId.toLongOrNull()
        if (
            chatId == null || chatId <= 0L ||
            userId == null || userId <= 0L ||
            logId == null || logId <= 0L ||
            value.jobId.isBlank()
        ) return null
        val legacyProcessing = documentVersion == LEGACY_VERSION &&
            value.status in setOf("delivery_pending", "awaiting_unlock")
        val handoffAt = value.deliveryHandoffAtMillis ?: if (legacyProcessing) value.updatedAtMillis else null
        val confirmationDeadline = value.deliveryConfirmationDeadlineAtMillis ?: if (legacyProcessing) {
            KakaoVideoDeliveryPolicy.legacyConfirmationDeadlineMillis(value.updatedAtMillis)
        } else null
        return LocalYoutubeDownloadJob(
            jobId = value.jobId,
            requestId = value.requestId,
            chatId = chatId,
            userId = userId,
            logId = logId,
            status = if (legacyProcessing) "kakao_processing" else value.status,
            roomCapabilityRevision = value.roomCapabilityRevision,
            createdAtMillis = value.createdAtMillis,
            deadlineAtMillis = value.deadlineAtMillis,
            updatedAtMillis = value.updatedAtMillis,
            deliveryHandoffAtMillis = handoffAt,
            deliveryConfirmationDeadlineAtMillis = confirmationDeadline,
            // A legacy delivery_pending/awaiting_unlock record was already
            // handed to Kakao by definition.  Count it as the initial attempt
            // so a delayed record has only one explicit retry left.
            deliveryAttempt = if (legacyProcessing) value.deliveryAttempt.coerceAtLeast(1)
            else value.deliveryAttempt
        )
    }

    private fun persist(): Boolean {
        val document = PersistedYoutubeDownloadJobDocument(
            version = VERSION,
            jobs = jobs.values.map {
                PersistedYoutubeDownloadJob(
                    jobId = it.jobId,
                    requestId = it.requestId,
                    chatId = it.chatId.toString(),
                    userId = it.userId.toString(),
                    logId = it.logId.toString(),
                    status = it.status,
                    roomCapabilityRevision = it.roomCapabilityRevision,
                    createdAtMillis = it.createdAtMillis,
                    deadlineAtMillis = it.deadlineAtMillis,
                    updatedAtMillis = it.updatedAtMillis,
                    deliveryHandoffAtMillis = it.deliveryHandoffAtMillis,
                    deliveryConfirmationDeadlineAtMillis = it.deliveryConfirmationDeadlineAtMillis,
                    deliveryAttempt = it.deliveryAttempt
                )
            }
        )
        return runCatching {
            backend.write(json.encodeToString(document).toByteArray(Charsets.UTF_8))
        }.fold(
            onSuccess = { true },
            onFailure = {
                log("YoutubeDownload job state persist failed: ${it::class.simpleName}")
                false
            }
        )
    }

    private companion object {
        const val LEGACY_VERSION = 1
        const val VERSION = 2
    }
}

@Serializable
private data class PersistedYoutubeDownloadJobDocument(
    val version: Int,
    val jobs: List<PersistedYoutubeDownloadJob>
)

@Serializable
private data class PersistedYoutubeDownloadJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val status: String,
    val roomCapabilityRevision: Long = 0L,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long,
    val deliveryHandoffAtMillis: Long? = null,
    val deliveryConfirmationDeadlineAtMillis: Long? = null,
    val deliveryAttempt: Int = 0
)

private val YOUTUBE_DOWNLOAD_PENDING_STATUSES = setOf(
    "queued",
    "running",
    "succeeded",
    "kakao_handoff_pending",
    "delivery_pending",
    "awaiting_unlock",
    "kakao_processing"
)
