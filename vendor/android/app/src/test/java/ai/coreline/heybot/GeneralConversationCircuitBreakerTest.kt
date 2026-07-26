package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.SocketTimeoutException

class GeneralConversationCircuitBreakerTest {
    @Test
    fun `trips after the configured number of relevant failures`() {
        var now = 1_000L
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 3,
            nowMillis = { now }
        )

        assertFalse(circuit.recordFailure(GlmFailure.Timeout(SocketTimeoutException())))
        now += 1_000L
        assertFalse(circuit.recordFailure(GlmFailure.RateLimited()))
        now += 1_000L
        assertTrue(circuit.recordFailure(GlmFailure.Network(IOException())))

        val status = circuit.status()
        assertTrue(status.tripped)
        assertEquals(3, status.failuresInWindow)
        assertEquals(GeneralConversationFailureCategory.NETWORK, status.lastReason)
    }

    @Test
    fun `drops failures older than the rolling window`() {
        var now = 0L
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 2,
            nowMillis = { now }
        )

        assertFalse(circuit.recordFailure(GlmFailure.Server(500)))
        now = 300_001L
        assertFalse(circuit.recordFailure(GlmFailure.Server(503)))

        assertFalse(circuit.status().tripped)
        assertEquals(1, circuit.status().failuresInWindow)
    }

    @Test
    fun `keeps a failure exactly on the rolling window boundary`() {
        var now = 0L
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 2,
            nowMillis = { now }
        )

        assertFalse(circuit.recordFailure(GlmFailure.Server(500)))
        now = 300_000L
        assertTrue(circuit.recordFailure(GlmFailure.Server(503)))
    }

    @Test
    fun `a successful request clears untripped failures`() {
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 3
        )

        circuit.recordFailure(GlmFailure.RateLimited())
        circuit.recordFailure(GlmFailure.Server(502))
        circuit.recordSuccess()

        val status = circuit.status()
        assertFalse(status.tripped)
        assertEquals(0, status.failuresInWindow)
        assertEquals(null, status.lastReason)
    }

    @Test
    fun `authentication and invalid response failures do not affect the circuit`() {
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 1
        )

        assertFalse(circuit.recordFailure(GlmFailure.Unauthorized()))
        assertFalse(circuit.recordFailure(GlmFailure.InvalidResponse(IllegalArgumentException())))

        assertFalse(circuit.status().tripped)
        assertEquals(0, circuit.status().failuresInWindow)
    }

    @Test
    fun `manual reset clears a tripped circuit`() {
        val circuit = GeneralConversationCircuitBreaker(
            windowMillis = 300_000L,
            failureThreshold = 1
        )

        assertTrue(circuit.recordFailure(GlmFailure.RateLimited()))
        circuit.reset()

        val status = circuit.status()
        assertFalse(status.tripped)
        assertEquals(0, status.failuresInWindow)
        assertEquals(null, status.lastReason)
    }
}
