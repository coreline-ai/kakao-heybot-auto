package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class VideoJobCoordinatorTest {
    @Test
    fun `video command creates and sends only when video capability is allowed`() {
        val sent = CountDownLatch(1)
        val store = InMemoryVideoJobStateStore()
        val gateway = SuccessfulVideoGateway()
        val policy = policy(videoEnabled = true)
        val coordinator = coordinator(gateway, store, policy) { _, bytes ->
            assertTrue(VideoJobCoordinator.isValidMp4(bytes, 1024))
            sent.countDown()
        }
        try {
            coordinator.onIncoming(incoming(1L, "헤이봇 영상 웃으며 손을 흔드는 분홍 로봇"))
            assertTrue(sent.await(2, TimeUnit.SECONDS))
            coordinator.onIncoming(outgoing(2L))
            assertTrue(awaitStatus(store, "delivered"))
            assertEquals(1, gateway.creates.get())
        } finally {
            coordinator.close()
        }
    }

    @Test
    fun `video command stays blocked until the room video capability is enabled`() {
        val gateway = SuccessfulVideoGateway()
        val coordinator = coordinator(gateway, InMemoryVideoJobStateStore(), policy(videoEnabled = false)) { _, _ -> }
        try {
            coordinator.onIncoming(incoming(3L, "헤이봇 영상 테스트"))
            Thread.sleep(100L)
            assertEquals(0, gateway.creates.get())
        } finally {
            coordinator.close()
        }
    }

    private fun coordinator(
        gateway: VideoProxyGateway,
        store: VideoJobStateStore,
        policy: RoomCapabilityPolicyStore,
        sender: (Long, ByteArray) -> Unit
    ) = VideoJobCoordinator(
        settings = VideoProxySettings(
            baseUrl = "http://127.0.0.1:4340",
            routeSecretFile = File("/unused"),
            allowedChatIds = setOf(CHAT),
            pollIntervalMillis = 10L,
            jobTimeoutMillis = 10_000L,
            videoMaxBytes = 1024,
            deliveryConfirmTimeoutMillis = 500L
        ),
        trigger = "헤이봇",
        botId = BOT,
        gateway = gateway,
        textSender = VideoTextReplySender { _, _, _ -> },
        videoSender = VideoBytesReplySender(sender),
        stateStore = store,
        roomCapabilityPolicy = policy,
        log = {}
    )

    private fun policy(videoEnabled: Boolean) = RoomCapabilityPolicyStore.forTesting(
        rooms = listOf(ManagedRoomCapability("R01", CHAT, "테스트 방", true, true, true, videoEnabled)),
        controlChatId = CHAT,
        backend = object : ConversationMemoryBackend {
            override fun read(): ByteArray? = null
            override fun write(bytes: ByteArray) = Unit
            override fun quarantine(nowMillis: Long) = Unit
        }
    )

    private fun incoming(logId: Long, message: String) = GlmIncomingMessage(logId, CHAT, USER, "1", message, null)
    // PD20 카카오톡의 실제 동영상 공유 기록은 type=3(POST)다.
    private fun outgoing(logId: Long) = GlmIncomingMessage(logId, CHAT, BOT, "3", "", null)

    private fun awaitStatus(store: VideoJobStateStore, status: String): Boolean {
        val deadline = System.currentTimeMillis() + 2_000L
        while (System.currentTimeMillis() < deadline) {
            if (runBlocking { store.latest(CHAT, USER)?.status } == status) return true
            Thread.sleep(10L)
        }
        return false
    }

    private class SuccessfulVideoGateway : VideoProxyGateway {
        val creates = AtomicInteger()
        override suspend fun create(requestId: String, chatId: Long, userId: Long, logId: Long, prompt: String) =
            Result.success(VideoProxyJob(JOB, requestId, chatId.toString(), "queued", null, null)).also { creates.incrementAndGet() }
        override suspend fun status(jobId: String, chatId: Long) =
            Result.success(VideoProxyJob(jobId, "request", chatId.toString(), "succeeded", null, "/file"))
        override suspend fun cancel(jobId: String, chatId: Long) =
            Result.success(VideoProxyJob(jobId, "request", chatId.toString(), "cancelled", null, null))
        override suspend fun download(jobId: String, chatId: Long) = Result.success(validMp4())
    }

    private companion object {
        const val CHAT = 18480337854645134L
        const val USER = 7216943976749157453L
        const val BOT = 444364619L
        const val JOB = "11111111-1111-4111-8111-111111111111"
        fun validMp4(): ByteArray = ByteArray(16).apply { this[4] = 'f'.code.toByte(); this[5] = 't'.code.toByte(); this[6] = 'y'.code.toByte(); this[7] = 'p'.code.toByte() }
    }
}
