package ai.coreline.heybot

import java.security.MessageDigest
import java.util.ArrayDeque
import java.util.Locale

sealed interface AdmissionResult {
    data object Accepted : AdmissionResult
    data object DuplicateLog : AdmissionResult
    data object DuplicateMessage : AdmissionResult
    data class RoomRateLimited(val retryAfterMillis: Long) : AdmissionResult
    data class UserRateLimited(val retryAfterMillis: Long) : AdmissionResult
}

/**
 * Short-lived admission state intentionally remains in memory. Its purpose is
 * to protect the GLM queue and external API, not to provide durable accounting.
 */
class RequestAdmissionController(
    private val roomWindowMillis: Long,
    private val roomMaxRequests: Int,
    private val userWindowMillis: Long,
    private val userMaxRequests: Int,
    private val duplicateWindowMillis: Long,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val maxRecentLogIds: Int = DEFAULT_MAX_RECENT_LOG_IDS,
    private val maxDuplicateKeys: Int = DEFAULT_MAX_DUPLICATE_KEYS,
    private val maxRateLimitKeys: Int = DEFAULT_MAX_RATE_LIMIT_KEYS
) {
    private val lock = Any()
    private val recentLogIds = ArrayDeque<Long>()
    private val recentLogIdSet = hashSetOf<Long>()
    private val duplicateMessages = mutableMapOf<DuplicateKey, Long>()
    private val roomWindows = mutableMapOf<Long, ArrayDeque<Long>>()
    private val userWindows = mutableMapOf<Long, ArrayDeque<Long>>()

    fun admit(incoming: GlmIncomingMessage): AdmissionResult = synchronized(lock) {
        if (!markFirstSeen(incoming.logId)) {
            return@synchronized AdmissionResult.DuplicateLog
        }

        val now = nowMillis()
        pruneDuplicateMessages(now)
        pruneWindows(roomWindows, now - roomWindowMillis)
        pruneWindows(userWindows, now - userWindowMillis)
        val duplicateKey = DuplicateKey(
            chatId = incoming.chatId,
            userId = incoming.userId,
            messageHash = hash(normalize(incoming.message))
        )
        val duplicateAt = duplicateMessages[duplicateKey]
        if (duplicateAt != null && now - duplicateAt < duplicateWindowMillis) {
            return@synchronized AdmissionResult.DuplicateMessage
        }

        val roomWindow = roomWindows.getOrPut(incoming.chatId) { ArrayDeque() }
        val userWindow = userWindows.getOrPut(incoming.userId) { ArrayDeque() }
        trimWindowKeys(roomWindows, maxRateLimitKeys, incoming.chatId)
        trimWindowKeys(userWindows, maxRateLimitKeys, incoming.userId)

        if (roomWindow.size >= roomMaxRequests) {
            return@synchronized AdmissionResult.RoomRateLimited(
                retryAfterMillis = retryAfter(roomWindow, roomWindowMillis, now)
            )
        }
        if (userWindow.size >= userMaxRequests) {
            return@synchronized AdmissionResult.UserRateLimited(
                retryAfterMillis = retryAfter(userWindow, userWindowMillis, now)
            )
        }

        roomWindow.addLast(now)
        userWindow.addLast(now)
        duplicateMessages[duplicateKey] = now
        AdmissionResult.Accepted
    }

    private fun markFirstSeen(logId: Long): Boolean {
        if (!recentLogIdSet.add(logId)) return false

        recentLogIds.addLast(logId)
        while (recentLogIds.size > maxRecentLogIds) {
            recentLogIdSet.remove(recentLogIds.removeFirst())
        }
        return true
    }

    private fun pruneDuplicateMessages(now: Long) {
        duplicateMessages.entries.removeAll { now - it.value >= duplicateWindowMillis }
        if (duplicateMessages.size <= maxDuplicateKeys) return

        duplicateMessages.entries
            .sortedBy { it.value }
            .take(duplicateMessages.size - maxDuplicateKeys)
            .forEach { duplicateMessages.remove(it.key) }
    }

    private fun pruneWindow(window: ArrayDeque<Long>, cutoff: Long) {
        while (window.isNotEmpty() && window.first() <= cutoff) {
            window.removeFirst()
        }
    }

    private fun pruneWindows(windows: MutableMap<Long, ArrayDeque<Long>>, cutoff: Long) {
        val iterator = windows.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            pruneWindow(entry.value, cutoff)
            if (entry.value.isEmpty()) iterator.remove()
        }
    }

    private fun trimWindowKeys(
        windows: MutableMap<Long, ArrayDeque<Long>>,
        maximum: Int,
        protectedKey: Long
    ) {
        while (windows.size > maximum) {
            val oldestKey = windows
                .filterKeys { it != protectedKey }
                .minByOrNull { (_, window) -> window.firstOrNull() ?: Long.MIN_VALUE }
                ?.key
                ?: return
            windows.remove(oldestKey)
        }
    }

    private fun retryAfter(window: ArrayDeque<Long>, duration: Long, now: Long): Long {
        val first = window.firstOrNull() ?: return duration
        return (first + duration - now).coerceAtLeast(1L)
    }

    private fun normalize(message: String): String =
        message.trim().replace(WHITESPACE, " ").lowercase(Locale.ROOT)

    private fun hash(value: String): String =
        MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
            .joinToString(separator = "") { "%02x".format(it) }

    private data class DuplicateKey(
        val chatId: Long,
        val userId: Long,
        val messageHash: String
    )

    private companion object {
        const val DEFAULT_MAX_RECENT_LOG_IDS = 512
        const val DEFAULT_MAX_DUPLICATE_KEYS = 1024
        const val DEFAULT_MAX_RATE_LIMIT_KEYS = 2048
        val WHITESPACE = Regex("\\s+")
    }
}
