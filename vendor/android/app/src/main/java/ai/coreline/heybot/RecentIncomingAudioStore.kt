package ai.coreline.heybot

/** Bounded in-memory index; durable selection falls back to KakaoTalk DB. */
class RecentIncomingAudioStore(
    private val retentionMillis: Long = DEFAULT_RETENTION_MILLIS,
    private val maxPerRoom: Int = DEFAULT_MAX_PER_ROOM,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    private val lock = Any()
    private val entries = mutableMapOf<Long, ArrayDeque<Entry>>()

    fun put(attachment: IncomingAudioAttachment) = synchronized(lock) {
        pruneLocked()
        val now = nowMillis()
        if (attachment.expiresAtMillis <= now) return@synchronized
        val queue = entries.getOrPut(attachment.chatId) { ArrayDeque() }
        if (queue.any { it.attachment.sourceLogId == attachment.sourceLogId }) return@synchronized
        queue.addFirst(Entry(attachment, now))
        while (queue.size > maxPerRoom) queue.removeLast()
    }

    fun findExact(chatId: Long, sourceLogId: Long): IncomingAudioAttachment? = synchronized(lock) {
        pruneLocked()
        entries.asSequence()
            .filter { it.key == chatId }
            .flatMap { it.value.asSequence() }
            .firstOrNull { it.attachment.sourceLogId == sourceLogId }
            ?.attachment
    }

    fun findRecent(chatId: Long, notBeforeMillis: Long): IncomingAudioAttachment? =
        synchronized(lock) {
            pruneLocked()
            entries[chatId]
                ?.firstOrNull { it.observedAtMillis >= notBeforeMillis }
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

    private data class Entry(val attachment: IncomingAudioAttachment, val observedAtMillis: Long)

    companion object {
        const val DEFAULT_RETENTION_MILLIS = 30 * 60 * 1_000L
        const val DEFAULT_MAX_PER_ROOM = 30
    }
}
