package ai.coreline.heybot

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.io.InterruptedIOException
import java.net.SocketTimeoutException

class GlmFailureTest {
    @Test
    fun `maps ZAI status codes to stable failure categories`() {
        assertTrue(GlmFailure.fromHttpCode(401) is GlmFailure.Unauthorized)
        assertTrue(GlmFailure.fromHttpCode(403) is GlmFailure.Forbidden)
        assertTrue(GlmFailure.fromHttpCode(429) is GlmFailure.RateLimited)
        assertTrue(GlmFailure.fromHttpCode(500) is GlmFailure.Server)
        assertTrue(GlmFailure.fromHttpCode(400) is GlmFailure.Http)
    }

    @Test
    fun `maps network and both OkHttp timeout forms safely`() {
        assertTrue(GlmFailure.fromThrowable(IOException("offline")) is GlmFailure.Network)
        assertTrue(GlmFailure.fromThrowable(SocketTimeoutException("timeout")) is GlmFailure.Timeout)
        assertTrue(GlmFailure.fromThrowable(InterruptedIOException("call timeout")) is GlmFailure.Timeout)
    }
}
