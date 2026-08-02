package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class PenBrushJobCoordinatorTest {
    @Test
    fun `pen-brush delivery publishes the confirmed media trace`() {
        val sent = CountDownLatch(1)
        val state = InMemoryPenBrushJobStateStore()
        val traces = RequestTraceStore.inMemory()
        val gateway = SuccessfulGateway()
        val coordinator = coordinator(gateway, state, policy(true), traces) { _, bytes ->
            assertTrue(PenBrushJobCoordinator.isValidMp4(bytes, 1_024))
            sent.countDown()
        }
        try {
            coordinator.onIncoming(incoming(1L, "헤이봇 펜브러쉬 분홍 로봇"))
            assertTrue(sent.await(2, TimeUnit.SECONDS))
            coordinator.onIncoming(outgoing(2L))
            assertTrue(awaitStatus(state, "delivered"))
            assertEquals(1, gateway.creates.get())
            assertEquals(
                RequestTraceStage.DB_CONFIRMED,
                traces.get(RequestTraceIds.from(CHAT, 1L))?.stage
            )
        } finally {
            coordinator.close()
        }
    }

    @Test
    fun `pen-brush capability denial is traced without provider call`() {
        val traces = RequestTraceStore.inMemory()
        val gateway = SuccessfulGateway()
        val coordinator = coordinator(
            gateway,
            InMemoryPenBrushJobStateStore(),
            policy(false),
            traces
        ) { _, _ -> Unit }
        try {
            coordinator.onIncoming(incoming(3L, "헤이봇 펜브러쉬 테스트"))
            Thread.sleep(50L)
            assertEquals(0, gateway.creates.get())
            assertEquals(
                RequestTraceStage.POLICY_DENIED,
                traces.get(RequestTraceIds.from(CHAT, 3L))?.stage
            )
        } finally {
            coordinator.close()
        }
    }

    private fun coordinator(
        gateway: PenBrushProxyGateway,
        state: PenBrushJobStateStore,
        policy: RoomCapabilityPolicyStore,
        traces: RequestTraceStore,
        sender: (Long, ByteArray) -> Unit
    ) = PenBrushJobCoordinator(
        settings = PenBrushProxySettings(
            baseUrl = "http://127.0.0.1:4340",
            routeSecretFile = File("/unused"),
            allowedChatIds = setOf(CHAT),
            pollIntervalMillis = 10L,
            jobTimeoutMillis = 10_000L,
            videoMaxBytes = 1_024,
            deliveryConfirmTimeoutMillis = 500L
        ),
        trigger = "헤이봇",
        botId = BOT,
        gateway = gateway,
        textSender = PenBrushTextReplySender { _, _, _ -> },
        videoSender = PenBrushBytesReplySender(sender),
        stateStore = state,
        roomCapabilityPolicy = policy,
        log = {},
        requestTraceStore = traces
    )

    private fun policy(enabled: Boolean) = RoomCapabilityPolicyStore.forTesting(
        rooms = listOf(
            ManagedRoomCapability(
                "R01", CHAT, "테스트 방", true, true, true,
                videoEnabled = false,
                penBrushEnabled = enabled
            )
        ),
        controlChatId = CHAT,
        backend = object : ConversationMemoryBackend {
            override fun read(): ByteArray? = null
            override fun write(bytes: ByteArray) = Unit
            override fun quarantine(nowMillis: Long) = Unit
        }
    )

    private fun incoming(logId: Long, message: String) =
        GlmIncomingMessage(logId, CHAT, USER, "1", message, null)

    private fun outgoing(logId: Long) =
        GlmIncomingMessage(logId, CHAT, BOT, "3", "", null)

    private fun awaitStatus(store: PenBrushJobStateStore, status: String): Boolean {
        val deadline = System.currentTimeMillis() + 2_000L
        while (System.currentTimeMillis() < deadline) {
            if (runBlocking { store.latest(CHAT, USER)?.status } == status) return true
            Thread.sleep(10L)
        }
        return false
    }

    private class SuccessfulGateway : PenBrushProxyGateway {
        val creates = AtomicInteger()
        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            logId: Long,
            prompt: String
        ) = Result.success(
            PenBrushProxyJob(JOB, requestId, chatId.toString(), "queued", null, null)
        ).also { creates.incrementAndGet() }

        override suspend fun status(jobId: String, chatId: Long) = Result.success(
            PenBrushProxyJob(jobId, "request", chatId.toString(), "succeeded", null, "/file")
        )

        override suspend fun cancel(jobId: String, chatId: Long) = Result.success(
            PenBrushProxyJob(jobId, "request", chatId.toString(), "cancelled", null, null)
        )

        override suspend fun download(jobId: String, chatId: Long) = Result.success(validMp4())
    }

    private companion object {
        const val CHAT = 18480337854645134L
        const val USER = 7216943976749157453L
        const val BOT = 444364619L
        const val JOB = "11111111-1111-4111-8111-111111111111"
        fun validMp4(): ByteArray = ByteArray(16).apply {
            this[4] = 'f'.code.toByte()
            this[5] = 't'.code.toByte()
            this[6] = 'y'.code.toByte()
            this[7] = 'p'.code.toByte()
        }
    }
}
