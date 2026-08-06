package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KakaoVideoDeliveryPolicyTest {
    @Test
    fun `confirmation deadline is bounded and grows with artifact size`() {
        val now = 1_000L
        val oneMiB = 1024 * 1024
        val small = KakaoVideoDeliveryPolicy.confirmationDeadlineMillis(now, oneMiB)
        val typical = KakaoVideoDeliveryPolicy.confirmationDeadlineMillis(now, 14 * oneMiB)
        val large = KakaoVideoDeliveryPolicy.confirmationDeadlineMillis(now, 50 * oneMiB)
        assertEquals(5 * 60 * 1_000L, small - now)
        assertEquals(20 * 60 * 1_000L, typical - now)
        assertEquals(20 * 60 * 1_000L, large - now)
    }

    @Test
    fun `per room delivery gate keeps only one owner until released`() = runBlocking {
        val gate = KakaoVideoDeliveryGate()
        assertTrue(gate.tryAcquire(1L, "video:1"))
        assertFalse(gate.tryAcquire(1L, "youtube:2"))
        assertTrue(gate.owns(1L, "video:1"))
        gate.release(1L, "video:1")
        assertTrue(gate.tryAcquire(1L, "youtube:2"))
    }
}
