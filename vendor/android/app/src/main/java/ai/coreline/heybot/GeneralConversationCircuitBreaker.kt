package ai.coreline.heybot

import java.util.ArrayDeque

enum class GeneralConversationFailureCategory {
    TIMEOUT,
    RATE_LIMIT,
    NETWORK,
    SERVER
}

data class GeneralConversationCircuitStatus(
    val tripped: Boolean,
    val failuresInWindow: Int,
    val lastReason: GeneralConversationFailureCategory?
)

/**
 * Process-local rolling failure window for ambient general conversation only.
 * It stores timestamps and generic categories, never request or response text.
 */
class GeneralConversationCircuitBreaker(
    private val windowMillis: Long,
    private val failureThreshold: Int,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    init {
        require(windowMillis > 0L) { "windowMillis must be positive" }
        require(failureThreshold > 0) { "failureThreshold must be positive" }
    }

    private data class Signal(
        val atMillis: Long,
        val category: GeneralConversationFailureCategory
    )

    private val failures = ArrayDeque<Signal>()
    private var tripped = false
    private var lastReason: GeneralConversationFailureCategory? = null

    fun recordFailure(failure: Throwable): Boolean {
        val category = categoryOf(failure) ?: return false
        return synchronized(this) {
            if (tripped) return@synchronized false
            val now = nowMillis()
            prune(now)
            failures.addLast(Signal(now, category))
            lastReason = category
            if (failures.size < failureThreshold) {
                false
            } else {
                tripped = true
                true
            }
        }
    }

    fun recordSuccess() = synchronized(this) {
        if (!tripped) {
            failures.clear()
            lastReason = null
        }
    }

    fun reset() = synchronized(this) {
        failures.clear()
        tripped = false
        lastReason = null
    }

    fun status(): GeneralConversationCircuitStatus = synchronized(this) {
        prune(nowMillis())
        GeneralConversationCircuitStatus(
            tripped = tripped,
            failuresInWindow = failures.size,
            lastReason = lastReason
        )
    }

    private fun prune(now: Long) {
        val cutoff = now - windowMillis
        while (failures.isNotEmpty() && failures.first().atMillis < cutoff) {
            failures.removeFirst()
        }
        if (failures.isEmpty() && !tripped) lastReason = null
    }

    private fun categoryOf(failure: Throwable): GeneralConversationFailureCategory? =
        when (failure) {
            is GlmFailure.Timeout -> GeneralConversationFailureCategory.TIMEOUT
            is GlmFailure.RateLimited -> GeneralConversationFailureCategory.RATE_LIMIT
            is GlmFailure.Network -> GeneralConversationFailureCategory.NETWORK
            is GlmFailure.Server -> GeneralConversationFailureCategory.SERVER
            else -> null
        }
}
