package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Test
import java.net.SocketTimeoutException

class BotMetricsTest {
    @Test
    fun `calculates rolling latency percentiles and redacted counters`() {
        val metrics = BotMetrics(startedAtMillis = 10L, maxRecentLatencies = 20)
        (1L..20L).forEach { metrics.recordGlmSuccess(it * 10L, 1_000L + it) }
        metrics.recordGlmFailure("Forbidden", 2_000L)
        metrics.recordExternalFailure(GlmFailure.RateLimited())
        metrics.recordExternalFailure(GlmFailure.Timeout(SocketTimeoutException()))
        metrics.recordFallbackAttempt()
        metrics.recordDuplicateDrop()
        metrics.recordRateLimitDrop()
        metrics.recordQueueFullDrop()
        metrics.recordGeneralPolicyDrop()
        metrics.recordReplySafetyBlock()
        metrics.recordReplyPiiRedactions(2)
        metrics.recordGeneralCircuitTrip()

        val snapshot = metrics.snapshot()

        assertEquals(20L, snapshot.glmSuccesses)
        assertEquals(1L, snapshot.glmFailures)
        assertEquals(105L, snapshot.averageLatencyMillis)
        assertEquals(100L, snapshot.p50LatencyMillis)
        assertEquals(190L, snapshot.p95LatencyMillis)
        assertEquals(1L, snapshot.rateLimitedResponses)
        assertEquals(1L, snapshot.timeoutResponses)
        assertEquals(1L, snapshot.fallbackAttempts)
        assertEquals(1L, snapshot.generalPolicyDrops)
        assertEquals(1L, snapshot.replySafetyBlocks)
        assertEquals(2L, snapshot.replyPiiRedactions)
        assertEquals(1L, snapshot.generalCircuitTrips)
        assertEquals("Forbidden", snapshot.lastFailureType)
    }
}
