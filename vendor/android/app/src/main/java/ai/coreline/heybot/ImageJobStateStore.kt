package ai.coreline.heybot

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class LocalImageJob(
    val jobId: String,
    val requestId: String,
    val chatId: Long,
    val userId: Long,
    val logId: Long,
    val status: String,
    val roomCapabilityRevision: Long = 0L,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long
)

interface ImageJobStateStore {
    suspend fun initialize()
    suspend fun upsert(job: LocalImageJob): Boolean
    suspend fun latest(chatId: Long, userId: Long): LocalImageJob?
    suspend fun pending(): List<LocalImageJob>
    suspend fun countPending(chatId: Long): Int
}

class InMemoryImageJobStateStore : ImageJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalImageJob>()

    override suspend fun initialize() = Unit

    override suspend fun upsert(job: LocalImageJob): Boolean = mutex.withLock {
        jobs[job.jobId] = job
        true
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalImageJob? = mutex.withLock {
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalImageJob> = mutex.withLock {
        jobs.values.filter { it.status in PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        jobs.values.count { it.chatId == chatId && it.status in PENDING_STATUSES }
    }
}

class AtomicJsonImageJobStateStore(
    private val backend: ConversationMemoryBackend,
    private val maxEntries: Int = 100,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }
) : ImageJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalImageJob>()
    private var initialized = false

    override suspend fun initialize() = mutex.withLock {
        if (initialized) return@withLock
        initialized = true
        val bytes = runCatching { backend.read() }.getOrNull() ?: return@withLock
        val document = runCatching {
            json.decodeFromString<PersistedImageJobDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("Image job state quarantined: invalid")
            return@withLock
        }
        if (document.version != VERSION) {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("Image job state quarantined: unsupported-version")
            return@withLock
        }
        document.jobs.mapNotNull(::decode)
            .sortedByDescending { it.updatedAtMillis }
            .take(maxEntries)
            .reversed()
            .forEach { jobs[it.jobId] = it }
    }

    override suspend fun upsert(job: LocalImageJob): Boolean = mutex.withLock {
        check(initialized)
        jobs[job.jobId] = job
        while (jobs.size > maxEntries) {
            val oldest = jobs.minByOrNull { it.value.updatedAtMillis }?.key ?: break
            jobs.remove(oldest)
        }
        persist()
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalImageJob? = mutex.withLock {
        check(initialized)
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalImageJob> = mutex.withLock {
        check(initialized)
        jobs.values.filter { it.status in PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        check(initialized)
        jobs.values.count { it.chatId == chatId && it.status in PENDING_STATUSES }
    }

    private fun decode(value: PersistedImageJob): LocalImageJob? {
        val chatId = value.chatId.toLongOrNull()
        val userId = value.userId.toLongOrNull()
        val logId = value.logId.toLongOrNull()
        if (
            chatId == null || chatId <= 0L ||
            userId == null || userId <= 0L ||
            logId == null || logId <= 0L ||
            value.jobId.isBlank()
        ) return null
        return LocalImageJob(
            jobId = value.jobId,
            requestId = value.requestId,
            chatId = chatId,
            userId = userId,
            logId = logId,
            status = value.status,
            roomCapabilityRevision = value.roomCapabilityRevision,
            createdAtMillis = value.createdAtMillis,
            deadlineAtMillis = value.deadlineAtMillis,
            updatedAtMillis = value.updatedAtMillis
        )
    }

    private fun persist(): Boolean {
        val document = PersistedImageJobDocument(
            version = VERSION,
            jobs = jobs.values.map {
                PersistedImageJob(
                    jobId = it.jobId,
                    requestId = it.requestId,
                    chatId = it.chatId.toString(),
                    userId = it.userId.toString(),
                    logId = it.logId.toString(),
                    status = it.status,
                    roomCapabilityRevision = it.roomCapabilityRevision,
                    createdAtMillis = it.createdAtMillis,
                    deadlineAtMillis = it.deadlineAtMillis,
                    updatedAtMillis = it.updatedAtMillis
                )
            }
        )
        return runCatching {
            backend.write(json.encodeToString(document).toByteArray(Charsets.UTF_8))
        }.fold(
            onSuccess = { true },
            onFailure = {
                log("Image job state persist failed: ${it::class.simpleName}")
                false
            }
        )
    }

    private companion object {
        const val VERSION = 1
    }
}

@Serializable
private data class PersistedImageJobDocument(
    val version: Int,
    val jobs: List<PersistedImageJob>
)

@Serializable
private data class PersistedImageJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val status: String,
    val roomCapabilityRevision: Long = 0L,
    val createdAtMillis: Long,
    val deadlineAtMillis: Long,
    val updatedAtMillis: Long
)

private val PENDING_STATUSES = setOf(
    "queued",
    "running",
    "succeeded",
    "delivery_pending",
    "awaiting_unlock"
)
