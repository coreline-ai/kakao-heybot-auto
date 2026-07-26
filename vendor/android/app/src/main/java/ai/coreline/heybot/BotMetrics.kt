package ai.coreline.heybot

import java.util.ArrayDeque

data class BotMetricsSnapshot(
    val startedAtMillis: Long,
    val glmSuccesses: Long,
    val glmFailures: Long,
    val timeoutResponses: Long,
    val rateLimitedResponses: Long,
    val fallbackAttempts: Long,
    val duplicateDrops: Long,
    val rateLimitDrops: Long,
    val queueFullDrops: Long,
    val generalPolicyDrops: Long,
    val replySafetyBlocks: Long,
    val replyPiiRedactions: Long,
    val generalCircuitTrips: Long,
    val generalConversationRequests: Long,
    val generalConversationReplies: Long,
    val generalConversationWaits: Long,
    val generalConversationIgnores: Long,
    val generalConversationInvalidResponses: Long,
    val generalConversationTruncationRetries: Long,
    val averageLatencyMillis: Long?,
    val p50LatencyMillis: Long?,
    val p95LatencyMillis: Long?,
    val lastSuccessAtMillis: Long?,
    val lastFailureAtMillis: Long?,
    val lastFailureType: String?
)

class BotMetrics(
    private val startedAtMillis: Long = System.currentTimeMillis(),
    private val maxRecentLatencies: Int = DEFAULT_MAX_RECENT_LATENCIES
) {
    private val lock = Any()
    private val latencies = ArrayDeque<Long>()
    private var glmSuccesses = 0L
    private var glmFailures = 0L
    private var timeoutResponses = 0L
    private var rateLimitedResponses = 0L
    private var fallbackAttempts = 0L
    private var duplicateDrops = 0L
    private var rateLimitDrops = 0L
    private var queueFullDrops = 0L
    private var generalPolicyDrops = 0L
    private var replySafetyBlocks = 0L
    private var replyPiiRedactions = 0L
    private var generalCircuitTrips = 0L
    private var generalConversationRequests = 0L
    private var generalConversationReplies = 0L
    private var generalConversationWaits = 0L
    private var generalConversationIgnores = 0L
    private var generalConversationInvalidResponses = 0L
    private var generalConversationTruncationRetries = 0L
    private var lastSuccessAtMillis: Long? = null
    private var lastFailureAtMillis: Long? = null
    private var lastFailureType: String? = null

    fun recordGlmSuccess(latencyMillis: Long, nowMillis: Long) = synchronized(lock) {
        glmSuccesses += 1
        lastSuccessAtMillis = nowMillis
        latencies.addLast(latencyMillis.coerceAtLeast(0L))
        while (latencies.size > maxRecentLatencies) latencies.removeFirst()
    }

    fun recordGlmFailure(type: String, nowMillis: Long) = synchronized(lock) {
        glmFailures += 1
        lastFailureAtMillis = nowMillis
        lastFailureType = type.take(MAX_FAILURE_TYPE_LENGTH)
    }

    fun recordExternalFailure(failure: Throwable) = synchronized(lock) {
        when (failure) {
            is GlmFailure.Timeout -> timeoutResponses += 1
            is GlmFailure.RateLimited -> rateLimitedResponses += 1
        }
    }

    fun recordFallbackAttempt() = synchronized(lock) {
        fallbackAttempts += 1
    }

    fun recordDuplicateDrop() = synchronized(lock) {
        duplicateDrops += 1
    }

    fun recordRateLimitDrop() = synchronized(lock) {
        rateLimitDrops += 1
    }

    fun recordQueueFullDrop() = synchronized(lock) {
        queueFullDrops += 1
    }

    fun recordGeneralPolicyDrop() = synchronized(lock) {
        generalPolicyDrops += 1
    }

    fun recordReplySafetyBlock() = synchronized(lock) {
        replySafetyBlocks += 1
    }

    fun recordReplyPiiRedactions(count: Int) = synchronized(lock) {
        replyPiiRedactions += count.coerceAtLeast(0)
    }

    fun recordGeneralCircuitTrip() = synchronized(lock) {
        generalCircuitTrips += 1
    }

    fun recordGeneralConversationRequest() = synchronized(lock) {
        generalConversationRequests += 1
    }

    fun recordGeneralConversationReply() = synchronized(lock) {
        generalConversationReplies += 1
    }

    fun recordGeneralConversationWait() = synchronized(lock) {
        generalConversationWaits += 1
    }

    fun recordGeneralConversationIgnore() = synchronized(lock) {
        generalConversationIgnores += 1
    }

    fun recordGeneralConversationInvalidResponse() = synchronized(lock) {
        generalConversationInvalidResponses += 1
    }

    fun recordGeneralConversationTruncationRetry() = synchronized(lock) {
        generalConversationTruncationRetries += 1
    }

    fun snapshot(): BotMetricsSnapshot = synchronized(lock) {
        val sortedLatencies = latencies.sorted()
        BotMetricsSnapshot(
            startedAtMillis = startedAtMillis,
            glmSuccesses = glmSuccesses,
            glmFailures = glmFailures,
            timeoutResponses = timeoutResponses,
            rateLimitedResponses = rateLimitedResponses,
            fallbackAttempts = fallbackAttempts,
            duplicateDrops = duplicateDrops,
            rateLimitDrops = rateLimitDrops,
            queueFullDrops = queueFullDrops,
            generalPolicyDrops = generalPolicyDrops,
            replySafetyBlocks = replySafetyBlocks,
            replyPiiRedactions = replyPiiRedactions,
            generalCircuitTrips = generalCircuitTrips,
            generalConversationRequests = generalConversationRequests,
            generalConversationReplies = generalConversationReplies,
            generalConversationWaits = generalConversationWaits,
            generalConversationIgnores = generalConversationIgnores,
            generalConversationInvalidResponses = generalConversationInvalidResponses,
            generalConversationTruncationRetries = generalConversationTruncationRetries,
            averageLatencyMillis = latencies
                .takeIf { it.isNotEmpty() }
                ?.sum()
                ?.div(latencies.size),
            p50LatencyMillis = percentile(sortedLatencies, 0.50),
            p95LatencyMillis = percentile(sortedLatencies, 0.95),
            lastSuccessAtMillis = lastSuccessAtMillis,
            lastFailureAtMillis = lastFailureAtMillis,
            lastFailureType = lastFailureType
        )
    }

    private fun percentile(sorted: List<Long>, percentile: Double): Long? {
        if (sorted.isEmpty()) return null
        val index = kotlin.math.ceil(sorted.size * percentile).toInt().coerceAtLeast(1) - 1
        return sorted[index.coerceAtMost(sorted.lastIndex)]
    }

    private companion object {
        const val DEFAULT_MAX_RECENT_LATENCIES = 20
        const val MAX_FAILURE_TYPE_LENGTH = 64
    }
}
