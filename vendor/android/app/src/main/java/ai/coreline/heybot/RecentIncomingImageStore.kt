package ai.coreline.heybot

/** Small best-effort cache. Durable source selection falls back to KakaoTalk DB. */
class RecentIncomingImageStore(
    private val retentionMillis: Long = DEFAULT_RETENTION_MILLIS,
    private val maxPerUser: Int = DEFAULT_MAX_PER_USER,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    private val lock = Any()
    private val entries = mutableMapOf<Key, ArrayDeque<Entry>>()

    fun put(attachment: IncomingImageAttachment) = synchronized(lock) {
        pruneLocked()
        if (attachment.expiresAtMillis <= nowMillis()) return@synchronized
        val key = Key(attachment.chatId, attachment.userId)
        val queue = entries.getOrPut(key) { ArrayDeque() }
        if (queue.any { it.attachment.sourceLogId == attachment.sourceLogId }) return@synchronized
        queue.addFirst(Entry(attachment, nowMillis()))
        while (queue.size > maxPerUser) queue.removeLast()
    }

    fun findExact(chatId: Long, sourceLogId: Long): IncomingImageAttachment? = synchronized(lock) {
        pruneLocked()
        entries.asSequence()
            .filter { it.key.chatId == chatId }
            .flatMap { it.value.asSequence() }
            .firstOrNull { it.attachment.sourceLogId == sourceLogId }
            ?.attachment
    }

    fun findRecent(
        chatId: Long,
        userId: Long,
        notBeforeMillis: Long
    ): IncomingImageAttachment? = synchronized(lock) {
        pruneLocked()
        entries[Key(chatId, userId)]
            ?.firstOrNull { it.observedAtMillis >= notBeforeMillis }
            ?.attachment
    }

    /**
     * Selects only from the current room.  The caller must still enforce the
     * room capability; this cache never authorizes a room by itself.
     */
    fun findLatestInRoom(chatId: Long, notBeforeMillis: Long): IncomingImageAttachment? = synchronized(lock) {
        pruneLocked()
        entries.asSequence()
            .filter { it.key.chatId == chatId }
            .flatMap { it.value.asSequence() }
            .filter { it.observedAtMillis >= notBeforeMillis }
            .maxWithOrNull(
                compareBy<Entry> { it.observedAtMillis }.thenBy { it.attachment.sourceLogId }
            )
            ?.attachment
    }

    fun clear() = synchronized(lock) { entries.clear() }

    private fun pruneLocked() {
        val now = nowMillis()
        val iterator = entries.iterator()
        while (iterator.hasNext()) {
            val queue = iterator.next().value
            queue.removeAll {
                now - it.observedAtMillis > retentionMillis || it.attachment.expiresAtMillis <= now
            }
            if (queue.isEmpty()) iterator.remove()
        }
    }

    private data class Key(val chatId: Long, val userId: Long)
    private data class Entry(
        val attachment: IncomingImageAttachment,
        val observedAtMillis: Long
    )

    companion object {
        const val DEFAULT_RETENTION_MILLIS = 10 * 60 * 1_000L
        const val DEFAULT_MAX_PER_USER = 2
    }
}
