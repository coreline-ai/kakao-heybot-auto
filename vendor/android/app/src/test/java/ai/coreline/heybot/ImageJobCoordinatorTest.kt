package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class ImageJobCoordinatorTest {
    @Test
    fun `creates polls and sends the image to the immutable job chat ID`() {
        val images = mutableListOf<Pair<Long, ByteArray>>()
        val imageLatch = CountDownLatch(1)
        val replies = mutableListOf<String>()
        val gateway = SuccessfulGateway()
        val store = InMemoryImageJobStateStore()
        val coordinator = coordinator(
            gateway = gateway,
            stateStore = store,
            text = { _, message, _ -> synchronized(replies) { replies += message } },
            image = { chatId, bytes ->
                synchronized(images) { images += chatId to bytes }
                imageLatch.countDown()
            }
        )

        coordinator.onIncoming(incoming(logId = 77L, message = "헤이봇 이미지 분홍색 로봇"))

        assertTrue(imageLatch.await(2, TimeUnit.SECONDS))
        assertTrue(runBlocking { store.latest(CHAT_ID, USER_ID)?.status != "delivered" })
        coordinator.onIncoming(outgoingImage(logId = 78L))
        assertTrue(awaitStatus(store, "delivered"))
        assertEquals(1, gateway.createCalls.get())
        assertEquals(CHAT_ID, images.single().first)
        assertTrue(ImageJobCoordinator.isValidPng(images.single().second, 1024))
        assertTrue(replies.any { it.contains("접수") })
        coordinator.close()
    }

    @Test
    fun `does not create an image job when the room image capability is disabled`() {
        val gateway = SuccessfulGateway()
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability("R01", CHAT_ID, "테스트 방", true, true, false)
            ),
            controlChatId = CHAT_ID,
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
        )
        val coordinator = coordinator(gateway = gateway, roomCapabilityPolicy = policy, image = { _, _ -> })

        coordinator.onIncoming(incoming(logId = 79L, message = "헤이봇 이미지 테스트"))

        Thread.sleep(50L)
        assertEquals(0, gateway.createCalls.get())
        coordinator.close()
    }

    @Test
    fun `revoking image permission while a job is polling prevents byte delivery`() {
        val statusStarted = CountDownLatch(1)
        val releaseStatus = CountDownLatch(1)
        val store = InMemoryImageJobStateStore()
        val imageSends = AtomicInteger()
        val policy = policyFor(
            ManagedRoomCapability("R01", CHAT_ID, "테스트 방", true, true, true)
        )
        val coordinator = coordinator(
            gateway = BlockingStatusGateway(statusStarted, releaseStatus),
            stateStore = store,
            roomCapabilityPolicy = policy,
            image = { _, _ -> imageSends.incrementAndGet() }
        )

        try {
            coordinator.onIncoming(incoming(logId = 80L, message = "헤이봇 이미지 권한 변경 테스트"))
            assertTrue(statusStarted.await(2, TimeUnit.SECONDS))

            val preview = policy.preview(USER_ID, "R01", RoomCapability.IMAGE, false)
                as RoomCapabilityMutationResult.PreviewReady
            assertTrue(policy.apply(USER_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)
            releaseStatus.countDown()

            assertTrue(awaitStatus(store, "cancelled"))
            assertEquals(0, imageSends.get())
        } finally {
            releaseStatus.countDown()
            coordinator.close()
        }
    }

    @Test
    fun `an unrelated room policy update does not cancel this room image delivery`() {
        val otherRoom = CHAT_ID + 1L
        val statusStarted = CountDownLatch(1)
        val releaseStatus = CountDownLatch(1)
        val imageSent = CountDownLatch(1)
        val policy = policyFor(
            ManagedRoomCapability("R01", CHAT_ID, "테스트 방", true, true, true),
            ManagedRoomCapability("R02", otherRoom, "다른 방", true, true, true)
        )
        val coordinator = coordinator(
            gateway = BlockingStatusGateway(statusStarted, releaseStatus),
            allowedChatIds = setOf(CHAT_ID, otherRoom),
            roomCapabilityPolicy = policy,
            image = { chatId, _ ->
                assertEquals(CHAT_ID, chatId)
                imageSent.countDown()
            }
        )

        try {
            coordinator.onIncoming(incoming(logId = 81L, message = "헤이봇 이미지 다른 방 변경 테스트"))
            assertTrue(statusStarted.await(2, TimeUnit.SECONDS))

            val preview = policy.preview(USER_ID, "R02", RoomCapability.IMAGE, false)
                as RoomCapabilityMutationResult.PreviewReady
            assertTrue(policy.apply(USER_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)
            releaseStatus.countDown()

            assertTrue(imageSent.await(2, TimeUnit.SECONDS))
        } finally {
            releaseStatus.countDown()
            coordinator.close()
        }
    }

    @Test
    fun `text and general permission changes do not cancel an already admitted image job`() {
        val controlRoom = CHAT_ID + 2L
        val statusStarted = CountDownLatch(1)
        val releaseStatus = CountDownLatch(1)
        val imageSent = CountDownLatch(1)
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability("R01", CHAT_ID, "이미지 방", true, true, true),
                ManagedRoomCapability("R02", controlRoom, "제어 방", true, true, true)
            ),
            controlChatId = controlRoom,
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
        )
        val coordinator = coordinator(
            gateway = BlockingStatusGateway(statusStarted, releaseStatus),
            allowedChatIds = setOf(CHAT_ID, controlRoom),
            roomCapabilityPolicy = policy,
            image = { _, _ -> imageSent.countDown() }
        )

        try {
            coordinator.onIncoming(incoming(logId = 82L, message = "헤이봇 이미지 독립 권한 테스트"))
            assertTrue(statusStarted.await(2, TimeUnit.SECONDS))

            val preview = policy.preview(USER_ID, "R01", RoomCapability.TEXT, false)
                as RoomCapabilityMutationResult.PreviewReady
            assertTrue(policy.apply(USER_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)
            assertFalse(policy.allows(CHAT_ID, RoomCapability.TEXT))
            assertTrue(policy.allows(CHAT_ID, RoomCapability.IMAGE))
            releaseStatus.countDown()

            assertTrue(imageSent.await(2, TimeUnit.SECONDS))
        } finally {
            releaseStatus.countDown()
            coordinator.close()
        }
    }

    @Test
    fun `image retry is ignored after image permission is revoked`() = runBlocking {
        val ready = CountDownLatch(1)
        val store = InMemoryImageJobStateStore()
        val gateway = SuccessfulGateway()
        val policy = policyFor(
            ManagedRoomCapability("R01", CHAT_ID, "테스트 방", true, true, true)
        )
        val coordinator = coordinator(
            gateway = gateway,
            stateStore = store,
            roomCapabilityPolicy = policy,
            log = { if (it == "Image proxy coordinator ready") ready.countDown() },
            image = { _, _ -> error("disabled image retry must not send bytes") }
        )

        try {
            assertTrue(ready.await(2, TimeUnit.SECONDS))
            store.upsert(
                LocalImageJob(
                    jobId = JOB_ID,
                    requestId = "image:$CHAT_ID:83",
                    chatId = CHAT_ID,
                    userId = USER_ID,
                    logId = 83L,
                    status = "awaiting_unlock",
                    roomCapabilityRevision = policy.snapshot()
                        .capabilityRevision(CHAT_ID, RoomCapability.IMAGE)!!,
                    createdAtMillis = System.currentTimeMillis(),
                    deadlineAtMillis = System.currentTimeMillis() + 10_000L,
                    updatedAtMillis = System.currentTimeMillis()
                )
            )
            val preview = policy.preview(USER_ID, "R01", RoomCapability.IMAGE, false)
                as RoomCapabilityMutationResult.PreviewReady
            assertTrue(policy.apply(USER_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)

            coordinator.onIncoming(incoming(logId = 84L, message = "헤이봇 이미지 재전송"))
            Thread.sleep(100L)

            assertEquals(0, gateway.statusCalls.get())
            assertEquals("awaiting_unlock", store.latest(CHAT_ID, USER_ID)?.status)
        } finally {
            coordinator.close()
        }
    }

    @Test
    fun `recovers pending polling after coordinator restart`() = runBlocking {
        val store = InMemoryImageJobStateStore()
        store.initialize()
        store.upsert(
            LocalImageJob(
                jobId = JOB_ID,
                requestId = "image:$CHAT_ID:88",
                chatId = CHAT_ID,
                userId = USER_ID,
                logId = 88L,
                status = "running",
                createdAtMillis = System.currentTimeMillis(),
                deadlineAtMillis = System.currentTimeMillis() + 10_000L,
                updatedAtMillis = System.currentTimeMillis()
            )
        )
        val latch = CountDownLatch(1)
        val coordinator = coordinator(
            gateway = SuccessfulGateway(),
            stateStore = store,
            image = { chatId, _ ->
                assertEquals(CHAT_ID, chatId)
                latch.countDown()
            }
        )

        assertTrue(latch.await(2, TimeUnit.SECONDS))
        coordinator.onIncoming(outgoingImage(logId = 89L))
        assertTrue(awaitStatus(store, "delivered"))
        assertEquals("delivered", store.latest(CHAT_ID, USER_ID)?.status)
        coordinator.close()
    }

    @Test
    fun `does not mark delivered until a bot image log is observed`() = runBlocking {
        val store = InMemoryImageJobStateStore()
        val imageLatch = CountDownLatch(1)
        val replies = mutableListOf<String>()
        val coordinator = coordinator(
            gateway = SuccessfulGateway(),
            stateStore = store,
            deliveryConfirmTimeoutMillis = 50L,
            text = { _, message, _ -> synchronized(replies) { replies += message } },
            image = { _, _ -> imageLatch.countDown() }
        )

        coordinator.onIncoming(incoming(logId = 90L, message = "헤이봇 이미지 잠금 테스트"))

        assertTrue(imageLatch.await(2, TimeUnit.SECONDS))
        assertTrue(awaitStatus(store, "awaiting_unlock"))
        assertTrue(replies.any { it.contains("잠금을 해제") })
        coordinator.close()
    }

    @Test
    fun `serializes recovered image delivery per room`() = runBlocking {
        val store = InMemoryImageJobStateStore()
        store.initialize()
        val now = System.currentTimeMillis()
        listOf("job-a", "job-b").forEachIndexed { index, id ->
            store.upsert(
                LocalImageJob(
                    jobId = id,
                    requestId = "request-$id",
                    chatId = CHAT_ID,
                    userId = USER_ID,
                    logId = 100L + index,
                    status = "succeeded",
                    createdAtMillis = now + index,
                    deadlineAtMillis = now + 10_000L,
                    updatedAtMillis = now + index
                )
            )
        }
        val sendCount = AtomicInteger()
        val bothSent = CountDownLatch(2)
        val coordinator = coordinator(
            gateway = SuccessfulGateway(),
            stateStore = store,
            image = { _, _ ->
                sendCount.incrementAndGet()
                bothSent.countDown()
            }
        )

        assertTrue(waitUntil { sendCount.get() == 1 })
        Thread.sleep(100)
        assertEquals(1, sendCount.get())
        coordinator.onIncoming(outgoingImage(logId = 110L))
        assertTrue(bothSent.await(2, TimeUnit.SECONDS))
        coordinator.onIncoming(outgoingImage(logId = 111L))
        coordinator.close()
    }

    @Test
    fun `image command does not call the GLM gateway`() = runBlocking {
        val glm = RecordingGlmGateway()
        val handler = GlmAutoReplyHandler(
            settings = GlmSettings(
                baseUrl = "https://api.z.ai/api/paas/v4/",
                model = "glm",
                trigger = "헤이봇",
                allowedChatIds = setOf(CHAT_ID),
                apiKeyFile = File("/unused"),
                timeoutMillis = 10_000,
                maxTokens = 64,
                temperature = 0.2,
                rateLimitRetries = 0
            ),
            botId = BOT_ID,
            gateway = glm,
            replySender = GlmReplySender { _, _, _ -> },
            log = {}
        )

        handler.process(incoming(logId = 1L, message = "헤이봇 이미지 분홍색 로봇"))

        assertEquals(0, glm.calls)
        handler.close()
    }

    private fun coordinator(
        gateway: ImageProxyGateway,
        stateStore: ImageJobStateStore = InMemoryImageJobStateStore(),
        deliveryConfirmTimeoutMillis: Long = 1_000L,
        text: (Long, String, Long?) -> Unit = { _, _, _ -> },
        image: (Long, ByteArray) -> Unit,
        allowedChatIds: Set<Long> = setOf(CHAT_ID),
        log: (String) -> Unit = {},
        roomCapabilityPolicy: RoomCapabilityPolicyStore =
            RoomCapabilityPolicyStore.legacy(allowedChatIds)
    ) = ImageJobCoordinator(
        settings = ImageProxySettings(
            baseUrl = "http://127.0.0.1:4340",
            routeSecretFile = File("/unused"),
            allowedChatIds = allowedChatIds,
            pollIntervalMillis = 10L,
            jobTimeoutMillis = 10_000L,
            imageMaxBytes = 1024,
            deliveryConfirmTimeoutMillis = deliveryConfirmTimeoutMillis
        ),
        trigger = "헤이봇",
        botId = BOT_ID,
        gateway = gateway,
        textSender = ImageTextReplySender(text),
        imageSender = ImageBytesReplySender(image),
        stateStore = stateStore,
        roomCapabilityPolicy = roomCapabilityPolicy,
        log = log
    )

    private fun incoming(logId: Long, message: String) = GlmIncomingMessage(
        logId = logId,
        chatId = CHAT_ID,
        userId = USER_ID,
        messageType = "1",
        message = message,
        threadId = null
    )

    private fun policyFor(vararg rooms: ManagedRoomCapability): RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.forTesting(
            rooms = rooms.toList(),
            controlChatId = CHAT_ID,
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
        )

    private fun outgoingImage(logId: Long) = GlmIncomingMessage(
        logId = logId,
        chatId = CHAT_ID,
        userId = BOT_ID,
        messageType = "3",
        message = "",
        threadId = null
    )

    private fun awaitStatus(
        store: ImageJobStateStore,
        expected: String,
        timeoutMillis: Long = 2_000L
    ): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMillis
        while (System.currentTimeMillis() < deadline) {
            val status = runBlocking { store.latest(CHAT_ID, USER_ID)?.status }
            if (status == expected) return true
            Thread.sleep(10)
        }
        return false
    }

    private fun waitUntil(timeoutMillis: Long = 2_000L, predicate: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMillis
        while (System.currentTimeMillis() < deadline) {
            if (predicate()) return true
            Thread.sleep(10)
        }
        return false
    }

    private class SuccessfulGateway : ImageProxyGateway {
        val createCalls = AtomicInteger()
        val statusCalls = AtomicInteger()

        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            logId: Long,
            prompt: String
        ): Result<ImageProxyJob> {
            createCalls.incrementAndGet()
            return Result.success(
                ImageProxyJob(JOB_ID, requestId, chatId.toString(), "queued", null, null)
            )
        }

        override suspend fun status(jobId: String, chatId: Long): Result<ImageProxyJob> {
            statusCalls.incrementAndGet()
            return Result.success(
                ImageProxyJob(jobId, "request", chatId.toString(), "succeeded", null, "/file")
            )
        }

        override suspend fun cancel(jobId: String, chatId: Long): Result<ImageProxyJob> =
            Result.success(
                ImageProxyJob(jobId, "request", chatId.toString(), "cancelled", null, null)
            )

        override suspend fun download(jobId: String, chatId: Long): Result<ByteArray> =
            Result.success(validPng())
    }

    private class BlockingStatusGateway(
        private val statusStarted: CountDownLatch,
        private val releaseStatus: CountDownLatch
    ) : ImageProxyGateway {
        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            logId: Long,
            prompt: String
        ): Result<ImageProxyJob> = Result.success(
            ImageProxyJob(JOB_ID, requestId, chatId.toString(), "queued", null, null)
        )

        override suspend fun status(jobId: String, chatId: Long): Result<ImageProxyJob> {
            statusStarted.countDown()
            if (!releaseStatus.await(2, TimeUnit.SECONDS)) {
                return Result.failure(IllegalStateException("test status was not released"))
            }
            return Result.success(
                ImageProxyJob(jobId, "request", chatId.toString(), "succeeded", null, "/file")
            )
        }

        override suspend fun cancel(jobId: String, chatId: Long): Result<ImageProxyJob> =
            Result.success(ImageProxyJob(jobId, "request", chatId.toString(), "cancelled", null, null))

        override suspend fun download(jobId: String, chatId: Long): Result<ByteArray> =
            Result.success(validPng())
    }

    private class RecordingGlmGateway : GlmGateway {
        var calls = 0
        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            calls += 1
            error("should not be called")
        }
    }

    companion object {
        const val CHAT_ID = 18480337854645134L
        const val USER_ID = 7216943976749157453L
        const val BOT_ID = 444364619L
        const val JOB_ID = "11111111-1111-4111-8111-111111111111"

        fun validPng(): ByteArray = ByteArray(24).apply {
            byteArrayOf(
                0x89.toByte(), 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a
            ).copyInto(this)
            this[18] = 1
            this[22] = 1
        }
    }
}
