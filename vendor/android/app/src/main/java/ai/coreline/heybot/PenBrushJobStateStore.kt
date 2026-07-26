package ai.coreline.heybot

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class LocalPenBrushJob(
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

interface PenBrushJobStateStore {
    suspend fun initialize()
    suspend fun upsert(job: LocalPenBrushJob): Boolean
    suspend fun latest(chatId: Long, userId: Long): LocalPenBrushJob?
    suspend fun pending(): List<LocalPenBrushJob>
    suspend fun countPending(chatId: Long): Int
}

class InMemoryPenBrushJobStateStore : PenBrushJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalPenBrushJob>()

    override suspend fun initialize() = Unit

    override suspend fun upsert(job: LocalPenBrushJob): Boolean = mutex.withLock {
        jobs[job.jobId] = job
        true
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalPenBrushJob? = mutex.withLock {
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalPenBrushJob> = mutex.withLock {
        jobs.values.filter { it.status in PEN_BRUSH_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        jobs.values.count { it.chatId == chatId && it.status in PEN_BRUSH_PENDING_STATUSES }
    }
}

class AtomicJsonPenBrushJobStateStore(
    private val backend: ConversationMemoryBackend,
    private val maxEntries: Int = 100,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }
) : PenBrushJobStateStore {
    private val mutex = Mutex()
    private val jobs = linkedMapOf<String, LocalPenBrushJob>()
    private var initialized = false

    override suspend fun initialize() = mutex.withLock {
        if (initialized) return@withLock
        initialized = true
        val bytes = runCatching { backend.read() }.getOrNull() ?: return@withLock
        val document = runCatching {
            json.decodeFromString<PersistedPenBrushJobDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("PenBrush job state quarantined: invalid")
            return@withLock
        }
        if (document.version != VERSION) {
            runCatching { backend.quarantine(System.currentTimeMillis()) }
            log("PenBrush job state quarantined: unsupported-version")
            return@withLock
        }
        document.jobs.mapNotNull(::decode)
            .sortedByDescending { it.updatedAtMillis }
            .take(maxEntries)
            .reversed()
            .forEach { jobs[it.jobId] = it }
    }

    override suspend fun upsert(job: LocalPenBrushJob): Boolean = mutex.withLock {
        check(initialized)
        jobs[job.jobId] = job
        while (jobs.size > maxEntries) {
            val oldest = jobs.minByOrNull { it.value.updatedAtMillis }?.key ?: break
            jobs.remove(oldest)
        }
        persist()
    }

    override suspend fun latest(chatId: Long, userId: Long): LocalPenBrushJob? = mutex.withLock {
        check(initialized)
        jobs.values
            .filter { it.chatId == chatId && it.userId == userId }
            .maxByOrNull { it.updatedAtMillis }
    }

    override suspend fun pending(): List<LocalPenBrushJob> = mutex.withLock {
        check(initialized)
        jobs.values.filter { it.status in PEN_BRUSH_PENDING_STATUSES }.sortedBy { it.createdAtMillis }
    }

    override suspend fun countPending(chatId: Long): Int = mutex.withLock {
        check(initialized)
        jobs.values.count { it.chatId == chatId && it.status in PEN_BRUSH_PENDING_STATUSES }
    }

    private fun decode(value: PersistedPenBrushJob): LocalPenBrushJob? {
        val chatId = value.chatId.toLongOrNull()
        val userId = value.userId.toLongOrNull()
        val logId = value.logId.toLongOrNull()
        if (
            chatId == null || chatId <= 0L ||
            userId == null || userId <= 0L ||
            logId == null || logId <= 0L ||
            value.jobId.isBlank()
        ) return null
        return LocalPenBrushJob(
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
        val document = PersistedPenBrushJobDocument(
            version = VERSION,
            jobs = jobs.values.map {
                PersistedPenBrushJob(
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
                log("PenBrush job state persist failed: ${it::class.simpleName}")
                false
            }
        )
    }

    private companion object {
        const val VERSION = 1
    }
}

@Serializable
private data class PersistedPenBrushJobDocument(
    val version: Int,
    val jobs: List<PersistedPenBrushJob>
)

@Serializable
private data class PersistedPenBrushJob(
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

private val PEN_BRUSH_PENDING_STATUSES = setOf(
    "queued",
    "running",
    "succeeded",
    "delivery_pending",
    "awaiting_unlock"
)
