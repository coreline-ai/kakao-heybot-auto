package ai.coreline.heybot

import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNull
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File
import java.util.Collections
import java.util.concurrent.CopyOnWriteArrayList

class AudioAnalysisCoordinatorTest {
    @Test
    fun `another users recent room audio is transcribed summarized and delivered`() = runBlocking {
        val replies = CopyOnWriteArrayList<String>()
        val transcript = AudioTranscriptResult(
            1, 2_000, "ko",
            listOf(AudioSegment("S0001", 0, 1_500, "다음 주 화요일에 다시 확인합니다.")),
            0.75, emptyList()
        )
        val gateway = ImmediateAudioGateway(transcript)
        val conversation = RecordingConversationGateway()
        val modeStore = ConversationEngineModeStore.inMemory()
        val router = ConversationGatewayRouter(modeStore, conversation, null, null)
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability(
                    reference = "R01", chatId = CHAT_ID, label = "연구소",
                    textEnabled = true, generalConversationEnabled = true, imageEnabled = true,
                    audioAnalysisEnabled = true
                )
            ),
            controlChatId = CHAT_ID,
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
        )
        val stateStore = InMemoryAudioAnalysisStateStore()
        val coordinator = AudioAnalysisCoordinator(
            settings = AudioAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                File("/tmp/not-used-state"), pollIntervalMillis = 1
            ),
            trigger = "헤이봇", botId = 999, gateway = gateway,
            summaryGenerator = AudioSummaryGenerator(router, "test"),
            engineModeStore = modeStore,
            replySender = AudioAnalysisReplySender { _, text, _ -> replies += text },
            roomCapabilityPolicy = policy,
            stateStore = stateStore
        )
        val audio = IncomingAudioAttachment(
            10, CHAT_ID, OTHER_USER_ID, "https://talk.kakaocdn.net/test.m4a",
            100, System.currentTimeMillis() + 60_000, "m4a"
        )
        coordinator.onIncoming(
            GlmIncomingMessage(10, CHAT_ID, OTHER_USER_ID, "18", "", null, audioAttachment = audio)
        )
        coordinator.onIncoming(
            GlmIncomingMessage(11, CHAT_ID, USER_ID, "1", "헤이봇 음성 요약", null)
        )

        withTimeout(2_000) {
            while (replies.none { it.contains("음성 요약 ·") }) delay(10)
        }
        assertTrue(gateway.created)
        assertTrue(gateway.createdSource?.userId == OTHER_USER_ID)
        assertTrue(conversation.requests.all { it.kind == GlmRequestKind.AUDIO_SUMMARY })
        assertTrue(replies.any { it.contains("다음 단계") })

        coordinator.onIncoming(message(12, "헤이봇 음성 상태"))
        withTimeout(2_000) { while (replies.none { it.startsWith("음성 분석 상태:") }) delay(10) }
        coordinator.onIncoming(message(13, "헤이봇 음성 원문 1"))
        withTimeout(2_000) { while (replies.none { it.startsWith("음성 원문 1/") }) delay(10) }
        coordinator.onIncoming(message(14, "헤이봇 음성 근거 1"))
        withTimeout(2_000) { while (replies.none { it.startsWith("음성 근거 1/") }) delay(10) }
        coordinator.onIncoming(message(15, "헤이봇 음성 재요약"))
        withTimeout(2_000) { while (replies.count { it.contains("음성 요약 ·") } < 2) delay(10) }
        coordinator.onIncoming(message(16, "헤이봇 음성 삭제"))
        withTimeout(2_000) { while (replies.none { it == "저장된 음성 전사 기록을 삭제했어요." }) delay(10) }
        assertNull(stateStore.latest(CHAT_ID, USER_ID))
        coordinator.onIncoming(message(17, "헤이봇 음성 상태"))
        withTimeout(2_000) { while (replies.none { it == "확인할 음성 분석 작업이 없어요." }) delay(10) }
        coordinator.close()
    }

    @Test
    fun `requester can cancel a pending room audio job`() = runBlocking {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = PendingAudioGateway()
        val coordinator = AudioAnalysisCoordinator(
            settings = AudioAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                File("/tmp/not-used-state"), pollIntervalMillis = 10_000
            ),
            trigger = "헤이봇", botId = 999, gateway = gateway,
            summaryGenerator = AudioSummaryGenerator(
                ConversationGatewayRouter(
                    ConversationEngineModeStore.inMemory(), RecordingConversationGateway(), null, null
                ),
                "test"
            ),
            engineModeStore = ConversationEngineModeStore.inMemory(),
            replySender = AudioAnalysisReplySender { _, text, _ -> replies += text },
            roomCapabilityPolicy = policy()
        )
        val audio = IncomingAudioAttachment(
            20, CHAT_ID, OTHER_USER_ID, "https://talk.kakaocdn.net/test.wav",
            100, System.currentTimeMillis() + 60_000, "wav"
        )
        coordinator.onIncoming(
            GlmIncomingMessage(20, CHAT_ID, OTHER_USER_ID, "18", "", null, audioAttachment = audio)
        )
        coordinator.onIncoming(message(21, "헤이봇 음성 요약"))
        withTimeout(2_000) { while (replies.none { it.startsWith("음성 분석을 시작했어요.") }) delay(10) }
        coordinator.onIncoming(message(22, "헤이봇 음성 취소"))
        withTimeout(2_000) { while (replies.none { it == "음성 분석을 취소했어요." }) delay(10) }
        assertTrue(gateway.cancelled)
        coordinator.close()
    }

    @Test
    fun `automatic analysis runs only when the room auto capability is on`() = runBlocking {
        val transcript = AudioTranscriptResult(
            1, 1_000, "ko",
            listOf(AudioSegment("S0001", 0, 900, "자동 분석 테스트입니다.")),
            0.9, emptyList()
        )
        suspend fun exercise(autoEnabled: Boolean): Pair<ImmediateAudioGateway, List<String>> {
            val replies = CopyOnWriteArrayList<String>()
            val gateway = ImmediateAudioGateway(transcript)
            val modeStore = ConversationEngineModeStore.inMemory()
            val coordinator = AudioAnalysisCoordinator(
                settings = AudioAnalysisSettings(
                    "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                    File("/tmp/not-used-state"), pollIntervalMillis = 1
                ),
                trigger = "헤이봇", botId = 999, gateway = gateway,
                summaryGenerator = AudioSummaryGenerator(
                    ConversationGatewayRouter(modeStore, RecordingConversationGateway(), null, null),
                    "test"
                ),
                engineModeStore = modeStore,
                replySender = AudioAnalysisReplySender { _, text, _ -> replies += text },
                roomCapabilityPolicy = policy(autoEnabled)
            )
            val audio = IncomingAudioAttachment(
                if (autoEnabled) 31 else 30, CHAT_ID, OTHER_USER_ID,
                "https://talk.kakaocdn.net/auto.m4a", 100,
                System.currentTimeMillis() + 60_000, "m4a"
            )
            coordinator.onIncoming(
                GlmIncomingMessage(
                    audio.sourceLogId, CHAT_ID, OTHER_USER_ID, "18", "", null,
                    audioAttachment = audio
                )
            )
            if (autoEnabled) {
                withTimeout(2_000) { while (replies.none { it.contains("음성 요약 ·") }) delay(10) }
            } else {
                delay(100)
            }
            coordinator.close()
            return gateway to replies.toList()
        }

        val (offGateway, offReplies) = exercise(false)
        assertFalse(offGateway.created)
        assertTrue(offReplies.isEmpty())
        val (onGateway, onReplies) = exercise(true)
        assertTrue(onGateway.created)
        assertTrue(onReplies.any { it.contains("음성 요약 ·") })
    }

    @Test
    fun `manual command in an audio-disabled room creates no job and no reply`() = runBlocking {
        val replies = CopyOnWriteArrayList<String>()
        val gateway = PendingAudioGateway()
        val modeStore = ConversationEngineModeStore.inMemory()
        val coordinator = AudioAnalysisCoordinator(
            settings = AudioAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                File("/tmp/not-used-state"), pollIntervalMillis = 1
            ),
            trigger = "헤이봇", botId = 999, gateway = gateway,
            summaryGenerator = AudioSummaryGenerator(
                ConversationGatewayRouter(modeStore, RecordingConversationGateway(), null, null),
                "test"
            ),
            engineModeStore = modeStore,
            replySender = AudioAnalysisReplySender { _, text, _ -> replies += text },
            roomCapabilityPolicy = policy(audioEnabled = false)
        )
        val audio = IncomingAudioAttachment(
            40, CHAT_ID, OTHER_USER_ID, "https://talk.kakaocdn.net/denied.mp3",
            100, System.currentTimeMillis() + 60_000, "mp3"
        )
        coordinator.onIncoming(
            GlmIncomingMessage(40, CHAT_ID, OTHER_USER_ID, "18", "", null, audioAttachment = audio)
        )
        coordinator.onIncoming(message(41, "헤이봇 음성 요약"))
        delay(100)
        assertFalse(gateway.created)
        assertTrue(replies.isEmpty())
        coordinator.close()
    }

    @Test
    fun `only unconfirmed multipart parts are replayed by audio resend command`() = runBlocking {
        val traces = RequestTraceStore.inMemory()
        val tracker = TextDeliveryTracker(
            botId = 999L,
            traces = traces,
            confirmTimeoutMillis = 10L,
            lateWindowMillis = 10L
        )
        val stateStore = InMemoryAudioAnalysisStateStore()
        val replies = CopyOnWriteArrayList<String>()
        var acceptConfirmation = false
        var nextLogId = 300L
        val modeStore = ConversationEngineModeStore.inMemory()
        val transcript = AudioTranscriptResult(
            1, 2_000, "ko", listOf(AudioSegment("S0001", 0, 1_500, "긴 회의 내용")), 0.8, emptyList()
        )
        val coordinator = AudioAnalysisCoordinator(
            settings = AudioAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                File("/tmp/not-used-state"), pollIntervalMillis = 1
            ),
            trigger = "헤이봇", botId = 999L, gateway = ImmediateAudioGateway(transcript),
            summaryGenerator = AudioSummaryGenerator(
                ConversationGatewayRouter(modeStore, MultipartConversationGateway(), null, null), "test"
            ),
            engineModeStore = modeStore,
            replySender = AudioAnalysisReplySender { chatId, text, threadId ->
                replies += text
                tracker.dispatched(chatId, text, Result.success(Unit))
                if (acceptConfirmation) {
                    tracker.onIncoming(GlmIncomingMessage(++nextLogId, chatId, 999L, "1", text, threadId))
                }
            },
            roomCapabilityPolicy = policy(), stateStore = stateStore,
            textDeliveryTracker = tracker, requestTraceStore = traces
        )
        val audio = IncomingAudioAttachment(
            60L, CHAT_ID, OTHER_USER_ID, "https://talk.kakaocdn.net/pending.m4a",
            100, System.currentTimeMillis() + 60_000, "m4a"
        )
        coordinator.onIncoming(GlmIncomingMessage(60L, CHAT_ID, OTHER_USER_ID, "18", "", null, audioAttachment = audio))
        coordinator.onIncoming(message(61L, "헤이봇 음성 요약"))
        withTimeout(2_000) {
            while (
                stateStore.latest(CHAT_ID, USER_ID)?.delivery?.parts?.firstOrNull()?.attempts != 2 ||
                replies.none { it.startsWith("[1/") && it.contains("재전송") }
            ) delay(10)
        }
        val pending = stateStore.latest(CHAT_ID, USER_ID)!!.delivery!!
        assertTrue(pending.parts.any { it.confirmedLogId == null })

        acceptConfirmation = true
        coordinator.onIncoming(message(62L, "헤이봇 음성 재전송"))
        withTimeout(2_000) {
            while (stateStore.latest(CHAT_ID, USER_ID)?.status != "succeeded") delay(10)
        }
        val completed = stateStore.latest(CHAT_ID, USER_ID)!!.delivery!!
        assertTrue(completed.parts.all { it.confirmedLogId != null })
        tracker.close()
        coordinator.close()
    }

    @Test
    fun `each multipart summary part is DB-confirmed before audio context is committed`() = runBlocking {
        val replies = CopyOnWriteArrayList<String>()
        val traces = RequestTraceStore.inMemory()
        val tracker = TextDeliveryTracker(botId = 999L, traces = traces)
        val stateStore = InMemoryAudioAnalysisStateStore()
        val contextStore = AudioConversationContextStore()
        var nextLogId = 100L
        val transcript = AudioTranscriptResult(
            1, 2_000, "ko", listOf(AudioSegment("S0001", 0, 1_500, "긴 회의 내용")), 0.8, emptyList()
        )
        val modeStore = ConversationEngineModeStore.inMemory()
        val coordinator = AudioAnalysisCoordinator(
            settings = AudioAnalysisSettings(
                "http://127.0.0.1:4340", File("/tmp/not-used"), setOf(CHAT_ID),
                File("/tmp/not-used-state"), pollIntervalMillis = 1
            ),
            trigger = "헤이봇", botId = 999L, gateway = ImmediateAudioGateway(transcript),
            summaryGenerator = AudioSummaryGenerator(
                ConversationGatewayRouter(modeStore, MultipartConversationGateway(), null, null), "test"
            ),
            engineModeStore = modeStore,
            replySender = AudioAnalysisReplySender { chatId, text, threadId ->
                replies += text
                tracker.dispatched(chatId, text, Result.success(Unit))
                tracker.onIncoming(GlmIncomingMessage(++nextLogId, chatId, 999L, "1", text, threadId))
            },
            roomCapabilityPolicy = policy(),
            stateStore = stateStore,
            textDeliveryTracker = tracker,
            requestTraceStore = traces,
            audioContextStore = contextStore
        )
        val audio = IncomingAudioAttachment(
            50L, CHAT_ID, OTHER_USER_ID, "https://talk.kakaocdn.net/long.m4a",
            100, System.currentTimeMillis() + 60_000, "m4a"
        )
        coordinator.onIncoming(GlmIncomingMessage(50L, CHAT_ID, OTHER_USER_ID, "18", "", null, audioAttachment = audio))
        coordinator.onIncoming(message(51L, "헤이봇 음성 요약"))

        withTimeout(2_000) {
            while (stateStore.latest(CHAT_ID, USER_ID)?.status != "succeeded") delay(10)
        }
        val completed = stateStore.latest(CHAT_ID, USER_ID) ?: error("missing audio job")
        assertTrue(completed.delivery!!.parts.size > 1)
        assertTrue(completed.delivery!!.parts.all { it.confirmedLogId != null && it.confirmedLogId!! > 0L })
        val context = contextStore.findOwned(CHAT_ID, USER_ID, completed.roomCapabilityRevision)
        assertTrue(replies.count { it.startsWith("[") } >= completed.delivery!!.parts.size)
        assertTrue(context != null && context.resultLogIds.size == completed.delivery!!.parts.size)
        tracker.close()
        coordinator.close()
    }

    private class ImmediateAudioGateway(private val transcript: AudioTranscriptResult) : AudioAnalysisGateway {
        var created = false
        var createdSource: IncomingAudioAttachment? = null
        private val job get() = AudioAnalysisJob(
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "audio:$CHAT_ID:10",
            CHAT_ID.toString(), "transcribed", null, transcript
        )
        override suspend fun create(requestId: String, chatId: Long, source: IncomingAudioAttachment) =
            Result.success(job.also {
                created = true
                createdSource = source
            })
        override suspend fun status(jobId: String, chatId: Long) = Result.success(job)
        override suspend fun cancel(jobId: String, chatId: Long) = Result.success(job.copy(status = "cancelled"))
        override suspend fun purge(jobId: String, chatId: Long) = Result.success(true)
    }

    private class PendingAudioGateway : AudioAnalysisGateway {
        var created = false
        var cancelled = false
        private var status = "queued"
        private val job get() = AudioAnalysisJob(
            "aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff", "audio:$CHAT_ID:20",
            CHAT_ID.toString(), status, null, null
        )

        override suspend fun create(requestId: String, chatId: Long, source: IncomingAudioAttachment) =
            Result.success(job.also { created = true })
        override suspend fun status(jobId: String, chatId: Long) = Result.success(job)
        override suspend fun cancel(jobId: String, chatId: Long): Result<AudioAnalysisJob> {
            cancelled = true
            status = "cancelled"
            return Result.success(job)
        }
        override suspend fun purge(jobId: String, chatId: Long) = Result.success(true)
    }

    private class MultipartConversationGateway : ConversationGateway {
        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            val isEvidenceMap = request.messages.last().content.contains("\"facts\"")
            val response = if (isEvidenceMap) {
                """{"version":1,"facts":[{"text":"긴 회의 내용","evidence":["S0001"]}],"warnings":[]}"""
            } else {
                val points = (1..8).joinToString(",") { index ->
                    """{"text":"핵심 ${index} ${"가".repeat(200)}","evidence":["S0001"]}"""
                }
                """{"version":1,"pattern":"AUTO","view":"DEFAULT","title":"긴 음성 요약","oneLine":"여러 part를 확인합니다.","oneLineEvidence":["S0001"],"keyPoints":[${points}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}"""
            }
            return Result.success(GlmChatResponse(response, null, "stop", null, null, null, 1))
        }
    }

    private class RecordingConversationGateway : ConversationGateway {
        val requests = Collections.synchronizedList(mutableListOf<GlmChatRequest>())
        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            requests += request
            val isEvidenceMap = request.messages.last().content.contains("\"facts\"")
            val response = if (isEvidenceMap) {
                """{"version":1,"facts":[{"text":"다음 주 화요일에 다시 확인","evidence":["S0001"]}],"warnings":[]}"""
            } else {
                """{"version":1,"pattern":"AUTO","view":"DEFAULT","title":"음성 요약","oneLine":"다음 주 화요일에 다시 확인합니다.","oneLineEvidence":["S0001"],"keyPoints":[{"text":"핵심 내용과 다음 단계","evidence":["S0001"]}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}"""
            }
            return Result.success(GlmChatResponse(response, null, "stop", null, null, null, 1))
        }
    }

    private fun message(logId: Long, text: String) =
        GlmIncomingMessage(logId, CHAT_ID, USER_ID, "1", text, null)

    private fun policy(
        autoEnabled: Boolean = false,
        audioEnabled: Boolean = true
    ) = RoomCapabilityPolicyStore.forTesting(
        rooms = listOf(
            ManagedRoomCapability(
                reference = "R01", chatId = CHAT_ID, label = "연구소",
                textEnabled = true, generalConversationEnabled = true, imageEnabled = true,
                audioAnalysisEnabled = audioEnabled,
                audioAutoAnalysisEnabled = autoEnabled && audioEnabled
            )
        ),
        controlChatId = CHAT_ID,
        backend = object : ConversationMemoryBackend {
            override fun read(): ByteArray? = null
            override fun write(bytes: ByteArray) = Unit
            override fun quarantine(nowMillis: Long) = Unit
        }
    )

    private companion object {
        const val CHAT_ID = 10L
        const val USER_ID = 20L
        const val OTHER_USER_ID = 21L
    }
}
