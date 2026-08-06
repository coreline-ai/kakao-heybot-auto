package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.atomic.AtomicInteger

class YoutubeDownloadCoordinatorTest {
    @Test
    fun `unconfirmed YouTube Kakao processing is not resent after coordinator restart`() = runBlocking {
        val state = InMemoryYoutubeDownloadJobStateStore()
        val now = System.currentTimeMillis()
        state.upsert(
            LocalYoutubeDownloadJob(
                jobId = JOB,
                requestId = "youtube:$CHAT:9",
                chatId = CHAT,
                userId = USER,
                logId = 9L,
                status = "kakao_processing",
                createdAtMillis = now,
                deadlineAtMillis = now + 60_000L,
                updatedAtMillis = now,
                deliveryHandoffAtMillis = now,
                deliveryConfirmationDeadlineAtMillis = now + 60_000L,
                deliveryAttempt = 1
            )
        )
        val dispatches = AtomicInteger()
        val coordinator = YoutubeDownloadJobCoordinator(
            settings = YoutubeDownloadProxySettings(
                baseUrl = "http://127.0.0.1:4340",
                routeSecretFile = File("/unused"),
                allowedChatIds = setOf(CHAT)
            ),
            trigger = "헤이봇",
            botId = BOT,
            gateway = NoopGateway,
            textSender = YoutubeDownloadTextReplySender { _, _, _ -> },
            youtubeDownloadSender = YoutubeDownloadBytesReplySender { _, _, _ ->
                dispatches.incrementAndGet()
            },
            stateStore = state,
            log = {}
        )
        try {
            Thread.sleep(150L)
            assertEquals(0, dispatches.get())
            assertEquals("kakao_processing", state.latest(CHAT, USER)?.status)
            coordinator.onIncoming(
                GlmIncomingMessage(10L, CHAT, BOT, "3", "", null)
            )
            assertTrue(awaitStatus(state, "delivered"))
        } finally {
            coordinator.close()
        }
    }

    private object NoopGateway : YoutubeDownloadProxyGateway {
        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            logId: Long,
            url: String
        ) = Result.failure<YoutubeDownloadProxyJob>(IllegalStateException("Unexpected create"))

        override suspend fun status(jobId: String, chatId: Long) =
            Result.failure<YoutubeDownloadProxyJob>(IllegalStateException("Unexpected status"))

        override suspend fun cancel(jobId: String, chatId: Long) =
            Result.failure<YoutubeDownloadProxyJob>(IllegalStateException("Unexpected cancel"))

        override suspend fun download(jobId: String, chatId: Long) =
            Result.failure<ByteArray>(IllegalStateException("Unexpected download"))
    }

    private fun awaitStatus(store: YoutubeDownloadJobStateStore, status: String): Boolean {
        val deadline = System.currentTimeMillis() + 2_000L
        while (System.currentTimeMillis() < deadline) {
            if (runBlocking { store.latest(CHAT, USER)?.status } == status) return true
            Thread.sleep(10L)
        }
        return false
    }

    private companion object {
        const val CHAT = 18480337854645134L
        const val USER = 7216943976749157453L
        const val BOT = 444364619L
        const val JOB = "11111111-1111-4111-8111-111111111111"
    }
}
