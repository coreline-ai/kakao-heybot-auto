package ai.coreline.heybot

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.math.ceil

/**
 * Kakao only creates the final chat-log attachment after it has processed an
 * ACTION_SEND video.  A missing row is therefore an unknown/processing state,
 * not evidence that the share failed.
 */
object KakaoVideoDeliveryPolicy {
    private const val MIB = 1024L * 1024L
    private const val MIN_CONFIRMATION_WAIT_MILLIS = 5L * 60L * 1_000L
    private const val BASE_CONFIRMATION_WAIT_MILLIS = 3L * 60L * 1_000L
    private const val PER_MIB_WAIT_MILLIS = 75L * 1_000L
    private const val MAX_CONFIRMATION_WAIT_MILLIS = 20L * 60L * 1_000L
    const val LOCAL_HANDOFF_TIMEOUT_MILLIS = 30_000L

    fun confirmationDeadlineMillis(handoffAtMillis: Long, artifactBytes: Int): Long {
        require(handoffAtMillis >= 0L)
        require(artifactBytes > 0)
        val mebibytes = ceil(artifactBytes.toDouble() / MIB).toLong()
        val wait = (BASE_CONFIRMATION_WAIT_MILLIS + mebibytes * PER_MIB_WAIT_MILLIS)
            .coerceIn(MIN_CONFIRMATION_WAIT_MILLIS, MAX_CONFIRMATION_WAIT_MILLIS)
        return handoffAtMillis + wait
    }

    /** v1 state had no byte-size or handoff receipt; never make it retryable. */
    fun legacyConfirmationDeadlineMillis(updatedAtMillis: Long): Long =
        updatedAtMillis + MAX_CONFIRMATION_WAIT_MILLIS
}

/**
 * Prevents one room from handing two MP4s to Kakao at once.  Ownership remains
 * until the matching Kakao DB row is observed or the bounded confirmation
 * window expires.  It is deliberately process-local; persisted job state
 * recreates the reservation on startup without dispatching the media again.
 */
class KakaoVideoDeliveryGate {
    private val mutex = Mutex()
    private val ownerByChatId = mutableMapOf<Long, String>()

    suspend fun tryAcquire(chatId: Long, jobId: String): Boolean = mutex.withLock {
        val current = ownerByChatId[chatId]
        if (current == null || current == jobId) {
            ownerByChatId[chatId] = jobId
            true
        } else {
            false
        }
    }

    suspend fun owns(chatId: Long, jobId: String): Boolean = mutex.withLock {
        ownerByChatId[chatId] == jobId
    }

    suspend fun release(chatId: Long, jobId: String) = mutex.withLock {
        if (ownerByChatId[chatId] == jobId) ownerByChatId.remove(chatId)
    }
}
