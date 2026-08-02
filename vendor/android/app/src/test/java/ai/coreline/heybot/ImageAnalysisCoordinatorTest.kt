package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class ImageAnalysisCoordinatorTest {
    @Test fun `same-account image then explicit command creates and delivers result`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = FakeGateway()
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability(
                    "R01", 10, "테스트", true, true, true,
                    imageAnalysisEnabled = true,
                    imageAnalysisRevision = 1
                )
            ),
            controlChatId = 10,
            backend = MemoryBackend()
        )
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings("http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1),
            trigger = "헤이봇", botId = 20, gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy
        )
        coordinator.onIncoming(message(1, "2", "", image(1, 10, 20)))
        coordinator.onIncoming(message(2, "1", "헤이봇 이미지 분석"))
        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.any { it.startsWith("이미지 분석 결과") }) return@repeat; Thread.sleep(2) }
        assertEquals(1L, gateway.source?.sourceLogId)
        assertTrue(replies.any { it.contains("로봇이 손을 흔들고") })
        coordinator.close()
    }

    @Test fun `same-account image alone never starts automatic analysis`() {
        val gateway = FakeGateway()
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340",
                File("/tmp/none"),
                setOf(10L),
                pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, _, _ -> },
            roomCapabilityPolicy = policy()
        )

        coordinator.onIncoming(message(60, "2", "", image(60, 10, 20)))

        assertFalse(gateway.created.await(100, TimeUnit.MILLISECONDS))
        coordinator.close()
    }

    @Test fun `OCR command creates a task scoped job and renders task label`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = FakeGateway()
        val traces = RequestTraceStore.inMemory()
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy(),
            requestTraceStore = traces
        )
        coordinator.onIncoming(message(70, "2", "", image(70, 10, 20)))
        coordinator.onIncoming(message(71, "1", "헤이봇 이미지 글자 추출"))

        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.any { it.startsWith("이미지 글자 추출 결과") }) return@repeat; Thread.sleep(2) }
        assertEquals(VisionTask.OCR, gateway.task)
        assertTrue(gateway.requestId.endsWith(":ocr"))
        assertTrue(replies.any { it.contains("HELLO") })
        assertEquals(RequestTraceKind.VISION, traces.get(RequestTraceIds.from(10L, 71L))?.kind)
        assertEquals(RequestTraceStage.ENQUEUED, traces.get(RequestTraceIds.from(10L, 71L))?.stage)
        coordinator.close()
    }

    @Test fun `OCR answer applies the shared privacy sanitizer before reply`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = FakeGateway("문의 test@example.com 010-1234-5678")
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy()
        )
        coordinator.onIncoming(message(72, "2", "", image(72, 10, 20)))
        coordinator.onIncoming(message(73, "1", "헤이봇 이미지 글자 추출"))

        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.any { it.startsWith("이미지 글자 추출 결과") }) return@repeat; Thread.sleep(2) }
        val result = replies.first { it.startsWith("이미지 글자 추출 결과") }
        assertTrue(result.contains("[이메일 마스킹]"))
        assertTrue(result.contains("[전화번호 마스킹]"))
        assertFalse(result.contains("test@example.com"))
        coordinator.close()
    }

    @Test fun `empty cache falls back to DB for exact reply without two-minute limit`() {
        val gateway = FakeGateway()
        val oldImage = image(41, 10, 999)
        val lookup = FakeLookup(exact = oldImage)
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340",
                File("/tmp/none"),
                setOf(10L),
                pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 999,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, _, _ -> },
            roomCapabilityPolicy = policy(),
            attachmentLookup = lookup
        )

        coordinator.onIncoming(message(50, "1", "헤이봇 이미지 분석", threadId = 41))

        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        assertEquals(41L, gateway.source?.sourceLogId)
        assertEquals(1, lookup.exactCalls)
        assertEquals(0, lookup.latestCalls)
        coordinator.close()
    }

    @Test fun `empty cache falls back to same-user recent DB image after restart`() {
        val gateway = FakeGateway()
        val recentImage = image(51, 10, 20)
        val lookup = FakeLookup(latest = recentImage)
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340",
                File("/tmp/none"),
                setOf(10L),
                pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 999,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, _, _ -> },
            roomCapabilityPolicy = policy(),
            attachmentLookup = lookup
        )

        coordinator.onIncoming(message(52, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        assertEquals(51L, gateway.source?.sourceLogId)
        assertEquals(0, lookup.exactCalls)
        assertEquals(1, lookup.latestCalls)
        coordinator.close()
    }

    @Test fun `different user falls back to recent bot image in same room`() {
        val gateway = FakeGateway()
        val botImage = image(61, 10, 999)
        val lookup = FakeLookup(latest = botImage)
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340",
                File("/tmp/none"),
                setOf(10L),
                pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 999,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, _, _ -> },
            roomCapabilityPolicy = policy(),
            attachmentLookup = lookup
        )

        coordinator.onIncoming(message(62, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.created.await(2, TimeUnit.SECONDS))
        assertEquals(61L, gateway.source?.sourceLogId)
        assertEquals(2, lookup.latestCalls)
        assertEquals(Long.MIN_VALUE, lookup.lastNotBeforeMillis)
        coordinator.close()
    }

    @Test fun `transport create failure retries with one id and succeeds without failure reply`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = RetryGateway(transportFailures = 2)
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy(),
            createRetryDelaysMillis = listOf(0L, 0L, 0L)
        )
        coordinator.onIncoming(message(80, "2", "", image(80, 10, 20)))
        coordinator.onIncoming(message(81, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.succeeded.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.any { it.startsWith("이미지 분석 결과") }) return@repeat; Thread.sleep(2) }
        assertEquals(3, gateway.attempts.get())
        assertEquals(1, gateway.requestIds.distinct().size)
        assertFalse(replies.any { it.contains("자동 복구하지 못했어요") })
        assertTrue(replies.any { it.startsWith("이미지 분석 결과") })
        coordinator.close()
    }

    @Test fun `transport create failure exhausts retries once and preserves root reason`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = RetryGateway(transportFailures = 4)
        val traces = RequestTraceStore.inMemory()
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy(),
            requestTraceStore = traces,
            createRetryDelaysMillis = listOf(0L, 0L, 0L)
        )
        coordinator.onIncoming(message(82, "2", "", image(82, 10, 20)))
        coordinator.onIncoming(message(83, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.exhausted.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.isNotEmpty()) return@repeat; Thread.sleep(2) }
        assertEquals(4, gateway.attempts.get())
        assertEquals(1, replies.count { it.contains("자동 복구하지 못했어요") })
        val trace = traces.get(RequestTraceIds.from(10L, 83L))!!
        assertEquals("VISION_TRANSPORT_UNAVAILABLE", trace.rootReasonCode)
        coordinator.close()
    }

    @Test fun `authorization create failure is not retried`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = RetryGateway(authorizationFailure = true)
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy(),
            createRetryDelaysMillis = listOf(0L, 0L, 0L)
        )
        coordinator.onIncoming(message(84, "2", "", image(84, 10, 20)))
        coordinator.onIncoming(message(85, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.exhausted.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.isNotEmpty()) return@repeat; Thread.sleep(2) }
        assertEquals(1, gateway.attempts.get())
        assertTrue(replies.single().contains("인증 설정"))
        coordinator.close()
    }

    @Test fun `capability change during transport backoff cancels retry silently`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = RetryGateway(transportFailures = 4)
        val policy = policy()
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy,
            createRetryDelaysMillis = listOf(100L, 100L, 100L)
        )
        coordinator.onIncoming(message(86, "2", "", image(86, 10, 20)))
        coordinator.onIncoming(message(87, "1", "헤이봇 이미지 분석"))
        assertTrue(gateway.attempted.await(2, TimeUnit.SECONDS))

        val preview = policy.preview(999, "R01", RoomCapability.IMAGE_ANALYSIS, false)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.apply(999, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)
        Thread.sleep(250)

        assertEquals(1, gateway.attempts.get())
        assertTrue(replies.isEmpty())
        coordinator.close()
    }

    @Test fun `status polling resumes after a transient transport failure`() {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = StatusRetryGateway()
        val coordinator = ImageAnalysisCoordinator(
            settings = ImageAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/none"), setOf(10L), pollIntervalMillis = 1
            ),
            trigger = "헤이봇",
            botId = 20,
            gateway = gateway,
            replySender = ImageAnalysisReplySender { _, message, _ -> synchronized(replies) { replies += message } },
            roomCapabilityPolicy = policy()
        )
        coordinator.onIncoming(message(88, "2", "", image(88, 10, 20)))
        coordinator.onIncoming(message(89, "1", "헤이봇 이미지 분석"))

        assertTrue(gateway.succeeded.await(2, TimeUnit.SECONDS))
        repeat(100) { if (replies.any { it.startsWith("이미지 분석 결과") }) return@repeat; Thread.sleep(2) }
        assertEquals(2, gateway.statusAttempts.get())
        assertTrue(replies.any { it.contains("상태 조회 재연결 성공") })
        assertFalse(replies.any { it.contains("자동 복구하지 못했어요") })
        coordinator.close()
    }

    private fun message(
        log: Long,
        type: String,
        text: String,
        image: IncomingImageAttachment? = null,
        threadId: Long? = null
    ) = GlmIncomingMessage(log, 10, 20, type, text, threadId, image)
    private fun image(log:Long,chat:Long,user:Long)=IncomingImageAttachment(log,chat,user,"https://talk.kakaocdn.net/fake.png",null,100,100,100,System.currentTimeMillis()+60_000,"image/png")
    private fun policy() = RoomCapabilityPolicyStore.forTesting(
        rooms = listOf(
            ManagedRoomCapability(
                "R01", 10, "테스트", true, true, true,
                imageAnalysisEnabled = true,
                imageAnalysisRevision = 1
            )
        ),
        controlChatId = 10,
        backend = MemoryBackend()
    )
    private class FakeLookup(
        private val exact: IncomingImageAttachment? = null,
        private val latest: IncomingImageAttachment? = null
    ) : ImageAttachmentLookup {
        var exactCalls = 0
        var latestCalls = 0
        var lastNotBeforeMillis: Long? = null
        override fun findExact(chatId: Long, sourceLogId: Long): IncomingImageAttachment? {
            exactCalls += 1
            return exact?.takeIf { it.chatId == chatId && it.sourceLogId == sourceLogId }
        }
        override fun findLatest(
            chatId: Long,
            userId: Long,
            notBeforeMillis: Long
        ): IncomingImageAttachment? {
            latestCalls += 1
            lastNotBeforeMillis = notBeforeMillis
            return latest?.takeIf { it.chatId == chatId && it.userId == userId }
        }
    }
    private class FakeGateway(private val answerOverride: String? = null):ImageAnalysisGateway {
        val created=CountDownLatch(1);var source:IncomingImageAttachment?=null
        var task:VisionTask=VisionTask.DESCRIBE;var requestId:String=""
        override suspend fun create(requestId:String,chatId:Long,userId:Long,source:IncomingImageAttachment,task:VisionTask):Result<ImageAnalysisJob>{this.source=source;this.task=task;this.requestId=requestId;created.countDown();return Result.success(job("queued"))}
        override suspend fun status(jobId:String,chatId:Long)=Result.success(job("succeeded",ImageAnalysisResult(2,task,answerOverride ?: if(task==VisionTask.OCR)"HELLO" else "밝은 방에서 로봇이 손을 흔들고 있습니다.",listOf("로봇"),if(task==VisionTask.OCR)listOf("HELLO") else emptyList(),"low")))
        override suspend fun cancel(jobId:String,chatId:Long)=Result.success(job("cancelled"))
        private fun job(status:String,result:ImageAnalysisResult?=null)=ImageAnalysisJob("job",requestId.ifBlank { "vision:10:1:describe" },"10",status,null,result)
    }
    private class RetryGateway(
        private val transportFailures: Int = 0,
        private val authorizationFailure: Boolean = false
    ) : ImageAnalysisGateway {
        val attempts = AtomicInteger()
        val requestIds = mutableListOf<String>()
        val attempted = CountDownLatch(1)
        val succeeded = CountDownLatch(1)
        val exhausted = CountDownLatch(1)

        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            source: IncomingImageAttachment,
            task: VisionTask
        ): Result<ImageAnalysisJob> {
            val attempt = attempts.incrementAndGet()
            attempted.countDown()
            synchronized(requestIds) { requestIds += requestId }
            if (authorizationFailure) {
                exhausted.countDown()
                return Result.failure(VisionAuthorizationException(401))
            }
            if (attempt <= transportFailures) {
                if (attempt == 4) exhausted.countDown()
                return Result.failure(VisionTransportException(IOException("offline")))
            }
            succeeded.countDown()
            return Result.success(ImageAnalysisJob("retry-job", requestId, chatId.toString(), "queued", null, null))
        }

        override suspend fun status(jobId: String, chatId: Long): Result<ImageAnalysisJob> =
            Result.success(
                ImageAnalysisJob(
                    jobId,
                    requestIds.last(),
                    chatId.toString(),
                    "succeeded",
                    null,
                    ImageAnalysisResult(2, VisionTask.DESCRIBE, "재시도 성공", emptyList(), emptyList(), "low")
                )
            )

        override suspend fun cancel(jobId: String, chatId: Long): Result<ImageAnalysisJob> =
            Result.success(ImageAnalysisJob(jobId, requestIds.last(), chatId.toString(), "cancelled", null, null))
    }
    private class StatusRetryGateway : ImageAnalysisGateway {
        val statusAttempts = AtomicInteger()
        val succeeded = CountDownLatch(1)
        private var requestId = ""

        override suspend fun create(
            requestId: String,
            chatId: Long,
            userId: Long,
            source: IncomingImageAttachment,
            task: VisionTask
        ): Result<ImageAnalysisJob> {
            this.requestId = requestId
            return Result.success(
                ImageAnalysisJob("status-retry-job", requestId, chatId.toString(), "queued", null, null)
            )
        }

        override suspend fun status(jobId: String, chatId: Long): Result<ImageAnalysisJob> {
            if (statusAttempts.incrementAndGet() == 1) {
                return Result.failure(VisionTransportException(IOException("reverse temporarily unavailable")))
            }
            succeeded.countDown()
            return Result.success(
                ImageAnalysisJob(
                    jobId,
                    requestId,
                    chatId.toString(),
                    "succeeded",
                    null,
                    ImageAnalysisResult(
                        2,
                        VisionTask.DESCRIBE,
                        "상태 조회 재연결 성공",
                        emptyList(),
                        emptyList(),
                        "low"
                    )
                )
            )
        }

        override suspend fun cancel(jobId: String, chatId: Long): Result<ImageAnalysisJob> =
            Result.success(ImageAnalysisJob(jobId, requestId, chatId.toString(), "cancelled", null, null))
    }
    private class MemoryBackend:ConversationMemoryBackend{override fun read():ByteArray?=null;override fun write(bytes:ByteArray)=Unit;override fun quarantine(nowMillis:Long)=Unit}
}
