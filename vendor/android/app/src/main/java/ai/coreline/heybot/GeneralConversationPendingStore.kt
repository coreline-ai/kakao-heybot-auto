package ai.coreline.heybot

/**
 * Keeps only short-lived, unfinished general-conversation utterances.
 *
 * Unlike [ConversationMemoryStore], this store is never persisted. It exists
 * solely to let a following message complete a model `WAIT` decision without
 * mixing users or retaining raw chat text across an Iris restart.
 */
class GeneralConversationPendingStore(
    private val maxMessagesPerConversation: Int = DEFAULT_MAX_MESSAGES,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS
) {
    private val lock = Any()
    private val pending = mutableMapOf<ConversationKey, ArrayDeque<PendingMessage>>()

    fun messages(key: ConversationKey, nowMillis: Long): List<String> = synchronized(lock) {
        pruneExpired(nowMillis)
        pending[key]?.map(PendingMessage::text).orEmpty()
    }

    fun append(key: ConversationKey, message: String, nowMillis: Long): Unit = synchronized(lock) {
        pruneExpired(nowMillis)
        val normalized = message.trim()
        if (normalized.isEmpty()) return@synchronized

        val messages = pending.getOrPut(key) { ArrayDeque() }
        messages.addLast(PendingMessage(normalized, nowMillis))
        while (messages.size > maxMessagesPerConversation) messages.removeFirst()
    }

    fun clear(key: ConversationKey): Unit = synchronized(lock) {
        pending.remove(key)
    }

    fun clearUser(userId: Long): Unit = synchronized(lock) {
        pending.keys.removeAll { it.userId == userId }
    }

    fun clearAll(): Unit = synchronized(lock) {
        pending.clear()
    }

    private fun pruneExpired(nowMillis: Long) {
        val iterator = pending.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            while (
                entry.value.isNotEmpty() &&
                    nowMillis - entry.value.first().createdAtMillis > ttlMillis
            ) {
                entry.value.removeFirst()
            }
            if (entry.value.isEmpty()) iterator.remove()
        }
    }

    private data class PendingMessage(
        val text: String,
        val createdAtMillis: Long
    )

    private companion object {
        const val DEFAULT_MAX_MESSAGES = 2
        const val DEFAULT_TTL_MILLIS = 2 * 60 * 1000L
    }
}
