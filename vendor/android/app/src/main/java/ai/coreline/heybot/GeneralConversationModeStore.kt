package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

data class GeneralConversationModeSnapshot(val epoch: Long)

data class GeneralConversationModeStatus(
    val enabled: Boolean,
    val epoch: Long,
    val persistenceConfigured: Boolean,
    val lastPersistSucceeded: Boolean?
)

/**
 * Global switch for wake-word-free conversation.
 *
 * Production supplies an atomic root-only backend so an administrator's
 * explicit ON/OFF intent survives process replacement. Missing, corrupt,
 * oversized, or unreadable state always restores as OFF. `close()` invalidates
 * only this process' in-flight work and intentionally does not rewrite the
 * persisted operator intent.
 */
class GeneralConversationModeStore(
    private val backend: ConversationMemoryBackend? = null,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val json: Json = Json {
        ignoreUnknownKeys = false
        encodeDefaults = true
    }
) {
    private val lock = Any()
    private var enabled = false
    private var epoch = 0L
    private var lastPersistSucceeded: Boolean? = null

    init {
        restore()
    }

    fun start(): GeneralConversationModeStatus = synchronized(lock) {
        if (!enabled) {
            if (!persist(enabled = true, quarantineOnFailure = false)) {
                return@synchronized statusLocked()
            }
            enabled = true
            epoch += 1L
        }
        statusLocked()
    }

    fun stop(): GeneralConversationModeStatus = synchronized(lock) {
        if (enabled) {
            enabled = false
            epoch += 1L
        }
        persist(enabled = false, quarantineOnFailure = true)
        statusLocked()
    }

    fun status(): GeneralConversationModeStatus = synchronized(lock) { statusLocked() }

    fun snapshotIfEnabled(): GeneralConversationModeSnapshot? = synchronized(lock) {
        if (enabled) GeneralConversationModeSnapshot(epoch) else null
    }

    fun isCurrent(snapshot: GeneralConversationModeSnapshot): Boolean = synchronized(lock) {
        enabled && epoch == snapshot.epoch
    }

    /** Runs an immediate reply send only while the same mode epoch remains active. */
    fun dispatchIfCurrent(snapshot: GeneralConversationModeSnapshot, send: () -> Boolean): Boolean = synchronized(lock) {
        if (!enabled || epoch != snapshot.epoch) return false
        send()
    }

    fun close() {
        synchronized(lock) {
            if (enabled) {
                enabled = false
                epoch += 1L
            }
        }
    }

    private fun restore() = synchronized(lock) {
        val source = backend ?: return@synchronized
        val readResult = runCatching { source.read() }
        if (readResult.isFailure) {
            log(
                "General conversation mode restore failed: " +
                    readResult.exceptionOrNull()?.let { it::class.simpleName }
            )
            quarantine("read-failed")
            return@synchronized
        }
        val bytes = readResult.getOrNull() ?: return@synchronized

        if (bytes.size > MAX_STATE_BYTES) {
            quarantine("oversized")
            return@synchronized
        }

        val document = runCatching {
            json.decodeFromString<PersistedGeneralConversationMode>(
                bytes.toString(Charsets.UTF_8)
            )
        }.getOrElse {
            quarantine("invalid")
            return@synchronized
        }

        if (document.version != CURRENT_VERSION) {
            quarantine("unsupported-version")
            return@synchronized
        }
        if (document.updatedAtMillis < 0L) {
            quarantine("invalid-metadata")
            return@synchronized
        }

        enabled = document.enabled
        lastPersistSucceeded = true
        log("General conversation mode restored enabled=$enabled")
    }

    private fun persist(enabled: Boolean, quarantineOnFailure: Boolean): Boolean {
        val destination = backend ?: return true
        val bytes = json.encodeToString(
            PersistedGeneralConversationMode.serializer(),
            PersistedGeneralConversationMode(
                version = CURRENT_VERSION,
                enabled = enabled,
                updatedAtMillis = nowMillis()
            )
        ).toByteArray(Charsets.UTF_8)
        val succeeded = runCatching { destination.write(bytes) }
            .onFailure {
                log("General conversation mode persist failed: ${it::class.simpleName}")
                if (quarantineOnFailure) {
                    runCatching { destination.quarantine(nowMillis()) }
                        .onFailure { quarantineFailure ->
                            log(
                                "General conversation mode quarantine failed: " +
                                    quarantineFailure::class.simpleName
                            )
                        }
                }
            }
            .isSuccess
        lastPersistSucceeded = succeeded
        return succeeded
    }

    private fun quarantine(reason: String) {
        enabled = false
        lastPersistSucceeded = false
        log("General conversation mode state quarantined: $reason")
        runCatching { backend?.quarantine(nowMillis()) }
            .onFailure {
                log("General conversation mode quarantine failed: ${it::class.simpleName}")
            }
    }

    private fun statusLocked() = GeneralConversationModeStatus(
        enabled = enabled,
        epoch = epoch,
        persistenceConfigured = backend != null,
        lastPersistSucceeded = lastPersistSucceeded
    )

    private companion object {
        const val CURRENT_VERSION = 1
        const val MAX_STATE_BYTES = 4 * 1024
    }
}

@Serializable
private data class PersistedGeneralConversationMode(
    val version: Int,
    val enabled: Boolean,
    val updatedAtMillis: Long
)
