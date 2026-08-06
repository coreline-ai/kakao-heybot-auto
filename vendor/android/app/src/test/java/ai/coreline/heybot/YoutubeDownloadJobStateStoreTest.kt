package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class YoutubeDownloadJobStateStoreTest {
    @Test
    fun `v1 unconfirmed delivery migrates to processing without becoming retryable`() = runBlocking {
        val backend = MemoryBackend(
            """{"version":1,"jobs":[{"jobId":"job-1","requestId":"youtube:1:2","chatId":"1","userId":"2","logId":"3","status":"awaiting_unlock","createdAtMillis":100,"deadlineAtMillis":200,"updatedAtMillis":300}]}"""
                .toByteArray()
        )
        val store = AtomicJsonYoutubeDownloadJobStateStore(backend)
        store.initialize()

        val job = store.latest(1L, 2L)
        assertNotNull(job)
        assertEquals("kakao_processing", job?.status)
        assertEquals(300L, job?.deliveryHandoffAtMillis)
        assertEquals(
            KakaoVideoDeliveryPolicy.legacyConfirmationDeadlineMillis(300L),
            job?.deliveryConfirmationDeadlineAtMillis
        )
        assertEquals(1, job?.deliveryAttempt)
        assertTrue(backend.snapshot().toString(Charsets.UTF_8).contains("\"version\":2"))
    }

    private class MemoryBackend(initial: ByteArray?) : ConversationMemoryBackend {
        private var value = initial
        override fun read(): ByteArray? = value
        override fun write(bytes: ByteArray) { value = bytes }
        override fun quarantine(nowMillis: Long) { value = null }
        fun snapshot(): ByteArray = requireNotNull(value)
    }
}
