package ai.coreline.heybot

import android.system.Os
import android.util.AtomicFile
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

data class ConversationKey(
    val chatId: Long,
    val userId: Long
)

data class ConversationTurn(
    val userMessage: String,
    val assistantMessage: String,
    val updatedAtMillis: Long
)

data class ConversationMemoryStats(
    val conversations: Int,
    val turns: Int,
    val lastPersistAtMillis: Long?,
    val lastPersistSucceeded: Boolean?
)

interface ConversationMemoryStore {
    suspend fun initialize()
    suspend fun history(key: ConversationKey, nowMillis: Long): List<ConversationTurn>
    suspend fun append(key: ConversationKey, turn: ConversationTurn): Boolean
    suspend fun clear(key: ConversationKey): Boolean
    suspend fun clearUser(userId: Long): Boolean
    suspend fun clearAll(): Boolean
    suspend fun stats(nowMillis: Long): ConversationMemoryStats
}

interface ConversationMemoryBackend {
    fun read(): ByteArray?
    fun write(bytes: ByteArray)
    fun quarantine(nowMillis: Long)
}

class InMemoryConversationMemoryStore(
    private val maxTurnsPerConversation: Int,
    private val ttlMillis: Long
) : ConversationMemoryStore {
    private val mutex = Mutex()
    private val conversations = mutableMapOf<ConversationKey, ArrayDeque<ConversationTurn>>()

    override suspend fun initialize() = Unit

    override suspend fun history(
        key: ConversationKey,
        nowMillis: Long
    ): List<ConversationTurn> = mutex.withLock {
        pruneExpired(nowMillis)
        conversations[key]?.toList().orEmpty()
    }

    override suspend fun append(key: ConversationKey, turn: ConversationTurn): Boolean =
        mutex.withLock {
            pruneExpired(turn.updatedAtMillis)
            val turns = conversations.getOrPut(key) { ArrayDeque() }
            turns.addLast(turn)
            while (turns.size > maxTurnsPerConversation) turns.removeFirst()
            true
        }

    override suspend fun clear(key: ConversationKey): Boolean = mutex.withLock {
        conversations.remove(key)
        true
    }

    override suspend fun clearUser(userId: Long): Boolean = mutex.withLock {
        conversations.keys.removeAll { it.userId == userId }
        true
    }

    override suspend fun clearAll(): Boolean = mutex.withLock {
        conversations.clear()
        true
    }

    override suspend fun stats(nowMillis: Long): ConversationMemoryStats = mutex.withLock {
        pruneExpired(nowMillis)
        ConversationMemoryStats(
            conversations = conversations.size,
            turns = conversations.values.sumOf { it.size },
            lastPersistAtMillis = null,
            lastPersistSucceeded = null
        )
    }

    private fun pruneExpired(nowMillis: Long) {
        val iterator = conversations.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            while (
                entry.value.isNotEmpty() &&
                nowMillis - entry.value.first().updatedAtMillis > ttlMillis
            ) {
                entry.value.removeFirst()
            }
            if (entry.value.isEmpty()) iterator.remove()
        }
    }
}

/**
 * Android's AtomicFile keeps the last complete file when a process is stopped
 * during a write. File and directory modes are defense in depth; the service
 * still has to be launched by root to own /data/local/private.
 */
class AndroidAtomicFileBackend(
    private val file: File
) : ConversationMemoryBackend {
    private val atomicFile = AtomicFile(file)

    override fun read(): ByteArray? {
        if (!file.exists() && !File("${file.path}.bak").exists()) return null
        return atomicFile.openRead().use { it.readBytes() }
    }

    override fun write(bytes: ByteArray) {
        file.parentFile?.let { parent ->
            check(parent.exists() || parent.mkdirs()) { "Memory directory is unavailable" }
            runCatching { Os.chmod(parent.absolutePath, DIRECTORY_MODE) }
        }

        val output = atomicFile.startWrite()
        try {
            output.write(bytes)
            output.fd.sync()
            atomicFile.finishWrite(output)
            runCatching { Os.chmod(file.absolutePath, FILE_MODE) }
        } catch (failure: Throwable) {
            atomicFile.failWrite(output)
            throw failure
        }
    }

    override fun quarantine(nowMillis: Long) {
        val suffix = ".corrupt-$nowMillis"
        listOf(file, File("${file.path}.bak")).forEach { candidate ->
            if (candidate.exists()) {
                candidate.renameTo(File("${candidate.path}$suffix"))
            }
        }
    }

    private companion object {
        const val DIRECTORY_MODE = 448 // 0700
        const val FILE_MODE = 384 // 0600
    }
}

/**
 * All mutations and atomic writes are serialized by one mutex. The persisted
 * document never contains API credentials and stores IDs as decimal strings.
 */
class AtomicJsonConversationMemoryStore(
    private val backend: ConversationMemoryBackend,
    private val maxTurnsPerConversation: Int,
    private val ttlMillis: Long,
    private val maxBytes: Int,
    private val maxConversations: Int,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }
) : ConversationMemoryStore {
    private val mutex = Mutex()
    private val conversations = linkedMapOf<ConversationKey, ArrayDeque<ConversationTurn>>()
    private var initialized = false
    private var lastPersistAtMillis: Long? = null
    private var lastPersistSucceeded: Boolean? = null

    override suspend fun initialize() = mutex.withLock {
        if (initialized) return@withLock
        initialized = true

        val bytes = runCatching { backend.read() }
            .onFailure { log("Conversation memory load failed: ${it::class.simpleName}") }
            .getOrNull()
            ?: return@withLock

        if (bytes.size > maxBytes) {
            quarantine("oversized")
            return@withLock
        }

        val document = runCatching {
            json.decodeFromString<PersistedMemoryDocument>(bytes.toString(Charsets.UTF_8))
        }.getOrElse {
            quarantine("invalid")
            return@withLock
        }

        if (document.version != CURRENT_VERSION) {
            quarantine("unsupported-version")
            return@withLock
        }

        val loaded = document.conversations
            .mapNotNull(::decodeConversation)
            .sortedByDescending { (_, turns) -> turns.maxOfOrNull { it.updatedAtMillis } ?: 0L }
            .take(maxConversations)

        loaded.forEach { (key, turns) ->
            conversations[key] = ArrayDeque(turns.takeLast(maxTurnsPerConversation))
        }
        lastPersistAtMillis = document.updatedAtMillis
        lastPersistSucceeded = true
    }

    override suspend fun history(
        key: ConversationKey,
        nowMillis: Long
    ): List<ConversationTurn> = mutex.withLock {
        ensureInitialized()
        val changed = pruneExpired(nowMillis)
        if (changed) persist(nowMillis)
        conversations[key]?.toList().orEmpty()
    }

    override suspend fun append(key: ConversationKey, turn: ConversationTurn): Boolean =
        mutex.withLock {
            ensureInitialized()
            pruneExpired(turn.updatedAtMillis)
            val turns = conversations.getOrPut(key) { ArrayDeque() }
            turns.addLast(turn)
            while (turns.size > maxTurnsPerConversation) turns.removeFirst()
            trimConversationCount()
            persist(turn.updatedAtMillis)
        }

    override suspend fun clear(key: ConversationKey): Boolean = mutex.withLock {
        ensureInitialized()
        conversations.remove(key)
        persist(System.currentTimeMillis())
    }

    override suspend fun clearUser(userId: Long): Boolean = mutex.withLock {
        ensureInitialized()
        conversations.keys.removeAll { it.userId == userId }
        persist(System.currentTimeMillis())
    }

    override suspend fun clearAll(): Boolean = mutex.withLock {
        ensureInitialized()
        conversations.clear()
        persist(System.currentTimeMillis())
    }

    override suspend fun stats(nowMillis: Long): ConversationMemoryStats = mutex.withLock {
        ensureInitialized()
        val changed = pruneExpired(nowMillis)
        if (changed) persist(nowMillis)
        ConversationMemoryStats(
            conversations = conversations.size,
            turns = conversations.values.sumOf { it.size },
            lastPersistAtMillis = lastPersistAtMillis,
            lastPersistSucceeded = lastPersistSucceeded
        )
    }

    private fun ensureInitialized() {
        check(initialized) { "Conversation memory must be initialized before use" }
    }

    private fun decodeConversation(
        persisted: PersistedConversation
    ): Pair<ConversationKey, List<ConversationTurn>>? {
        val chatId = persisted.chatId.toLongOrNull()
        val userId = persisted.userId.toLongOrNull()
        if (chatId == null || chatId <= 0L || userId == null || userId <= 0L) return null

        val turns = persisted.turns.mapNotNull { turn ->
            if (
                turn.userMessage.isBlank() ||
                turn.assistantMessage.isBlank() ||
                turn.updatedAtMillis < 0L
            ) {
                null
            } else {
                ConversationTurn(
                    userMessage = turn.userMessage,
                    assistantMessage = turn.assistantMessage,
                    updatedAtMillis = turn.updatedAtMillis
                )
            }
        }
        return ConversationKey(chatId, userId) to turns
    }

    private fun pruneExpired(nowMillis: Long): Boolean {
        var changed = false
        val iterator = conversations.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            while (
                entry.value.isNotEmpty() &&
                nowMillis - entry.value.first().updatedAtMillis > ttlMillis
            ) {
                entry.value.removeFirst()
                changed = true
            }
            while (entry.value.size > maxTurnsPerConversation) {
                entry.value.removeFirst()
                changed = true
            }
            if (entry.value.isEmpty()) {
                iterator.remove()
                changed = true
            }
        }
        return changed
    }

    private fun trimConversationCount() {
        while (conversations.size > maxConversations) {
            val oldest = conversations.minByOrNull { (_, turns) ->
                turns.maxOfOrNull { it.updatedAtMillis } ?: Long.MIN_VALUE
            }?.key ?: return
            conversations.remove(oldest)
        }
    }

    private fun persist(nowMillis: Long): Boolean {
        var bytes = encodeDocument(nowMillis)
        var evicted = 0
        while (bytes.size > maxBytes && conversations.isNotEmpty()) {
            val oldest = conversations.minByOrNull { (_, turns) ->
                turns.maxOfOrNull { it.updatedAtMillis } ?: Long.MIN_VALUE
            }?.key ?: break
            conversations.remove(oldest)
            evicted += 1
            bytes = encodeDocument(nowMillis)
        }
        if (evicted > 0) {
            log("Conversation memory evicted $evicted old conversations to stay within size limit")
        }

        return runCatching { backend.write(bytes) }
            .fold(
                onSuccess = {
                    lastPersistAtMillis = nowMillis
                    lastPersistSucceeded = true
                    true
                },
                onFailure = {
                    lastPersistSucceeded = false
                    log("Conversation memory persist failed: ${it::class.simpleName}")
                    false
                }
            )
    }

    private fun encodeDocument(nowMillis: Long): ByteArray {
        val document = PersistedMemoryDocument(
            version = CURRENT_VERSION,
            updatedAtMillis = nowMillis,
            conversations = conversations.map { (key, turns) ->
                PersistedConversation(
                    chatId = key.chatId.toString(),
                    userId = key.userId.toString(),
                    turns = turns.map {
                        PersistedTurn(
                            userMessage = it.userMessage.take(MAX_PERSISTED_MESSAGE_LENGTH),
                            assistantMessage = it.assistantMessage.take(MAX_PERSISTED_MESSAGE_LENGTH),
                            updatedAtMillis = it.updatedAtMillis
                        )
                    }
                )
            }
        )
        return json.encodeToString(document).toByteArray(Charsets.UTF_8)
    }

    private fun quarantine(reason: String) {
        conversations.clear()
        runCatching { backend.quarantine(System.currentTimeMillis()) }
        lastPersistSucceeded = false
        log("Conversation memory quarantined: $reason")
    }

    private companion object {
        const val CURRENT_VERSION = 1
        const val MAX_PERSISTED_MESSAGE_LENGTH = 4_096
    }
}

@Serializable
private data class PersistedMemoryDocument(
    val version: Int,
    val updatedAtMillis: Long,
    val conversations: List<PersistedConversation>
)

@Serializable
private data class PersistedConversation(
    val chatId: String,
    val userId: String,
    val turns: List<PersistedTurn>
)

@Serializable
private data class PersistedTurn(
    val userMessage: String,
    val assistantMessage: String,
    val updatedAtMillis: Long
)
