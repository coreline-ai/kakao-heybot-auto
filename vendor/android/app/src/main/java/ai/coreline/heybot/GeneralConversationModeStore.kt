package ai.coreline.heybot

data class GeneralConversationModeSnapshot(val epoch: Long)

data class GeneralConversationModeStatus(
    val enabled: Boolean,
    val epoch: Long
)

/**
 * Process-local global switch for wake-word-free conversation.
 *
 * It intentionally starts disabled and is never persisted: after an Iris
 * restart, an authorized operator must enable it again from the control room.
 */
class GeneralConversationModeStore {
    private val lock = Any()
    private var enabled = false
    private var epoch = 0L

    fun start(): GeneralConversationModeStatus = synchronized(lock) {
        if (!enabled) {
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
        stop()
    }

    private fun statusLocked() = GeneralConversationModeStatus(enabled = enabled, epoch = epoch)
}
