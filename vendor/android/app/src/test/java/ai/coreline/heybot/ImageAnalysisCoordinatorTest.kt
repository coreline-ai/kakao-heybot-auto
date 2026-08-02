package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ImageAnalysisCoordinatorTest {
    @Test fun `same-account image then explicit command creates and delivers result`() {
        val replies = mutableListOf<String>()
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
        val replies = mutableListOf<String>()
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
        val replies = mutableListOf<String>()
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
    private class MemoryBackend:ConversationMemoryBackend{override fun read():ByteArray?=null;override fun write(bytes:ByteArray)=Unit;override fun quarantine(nowMillis:Long)=Unit}
}
