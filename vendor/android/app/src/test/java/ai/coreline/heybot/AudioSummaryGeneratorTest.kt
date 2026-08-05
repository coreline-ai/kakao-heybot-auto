package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioSummaryGeneratorTest {
    @Test
    fun `uses captured engine for map and reduce while retaining segment ids`() = runBlocking {
        val codex = RecordingAudioGateway()
        val router = ConversationGatewayRouter(
            modeStore = ConversationEngineModeStore.inMemory(),
            glm = RecordingAudioGateway(),
            codex = codex,
            grok = null
        )
        val transcript = AudioTranscriptResult(
            1, 3_000, "ko",
            listOf(
                AudioSegment("S0001", 0, 1_000, "첫 번째 안건"),
                AudioSegment("S0002", 1_000, 2_000, "다음 주에 다시 확인")
            ),
            0.8, emptyList()
        )

        val output = AudioSummaryGenerator(router, "test-model")
            .summarize(
                transcript,
                AudioSummaryProfile(AudioSummaryPattern.MEETING, AudioSummaryView.MINUTES),
                ConversationEngine.CODEX
            ).getOrThrow()

        assertEquals(ConversationEngine.CODEX, output.engine)
        assertEquals(2, codex.requests.size)
        assertTrue(codex.requests.first().messages.last().content.contains("S0001"))
        assertTrue(codex.requests.all { it.kind == GlmRequestKind.AUDIO_SUMMARY })
        assertTrue(output.text.contains("[S0001]"))
        assertEquals("MEETING", output.document.pattern)
    }

    @Test
    fun `repairs one invalid JSON response then fails closed on a second invalid response`() = runBlocking {
        val repaired = SequencedAudioGateway(
            listOf(
                "not-json",
                """{"version":1,"facts":[{"text":"확인","evidence":["S0001"]}],"warnings":[]}""",
                """{"version":1,"pattern":"AUTO","view":"DEFAULT","title":"요약","oneLine":"확인","oneLineEvidence":["S0001"],"keyPoints":[{"text":"확인","evidence":["S0001"]}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}"""
            )
        )
        val transcript = AudioTranscriptResult(
            1, 1_000, "ko", listOf(AudioSegment("S0001", 0, 900, "확인합니다.")), 0.9, emptyList()
        )
        val output = AudioSummaryGenerator(
            ConversationGatewayRouter(ConversationEngineModeStore.inMemory(), repaired, null, null), "test"
        ).summarize(transcript, AudioSummaryProfile(), ConversationEngine.GLM).getOrThrow()
        assertEquals(3, repaired.requests.size)
        assertTrue(output.text.contains("한 줄 요약"))

        val invalid = SequencedAudioGateway(listOf("bad", "still bad"))
        val result = AudioSummaryGenerator(
            ConversationGatewayRouter(ConversationEngineModeStore.inMemory(), invalid, null, null), "test"
        ).summarize(transcript, AudioSummaryProfile(), ConversationEngine.GLM)
        assertTrue(result.exceptionOrNull()?.message == "SUMMARY_OUTPUT_INVALID")
    }

    private class RecordingAudioGateway : ConversationGateway {
        val requests = mutableListOf<GlmChatRequest>()
        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            requests += request
            val response = if (requests.size == 1) {
                """{"version":1,"facts":[{"text":"첫 번째 안건","evidence":["S0001"]}],"warnings":[]}"""
            } else {
                """{"version":1,"pattern":"MEETING","view":"MINUTES","title":"회의 요약","oneLine":"첫 번째 안건을 확인합니다.","oneLineEvidence":["S0001"],"keyPoints":[{"text":"첫 번째 안건","evidence":["S0001"]}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}"""
            }
            return Result.success(GlmChatResponse(response, null, "stop", null, null, null, 1))
        }
    }

    private class SequencedAudioGateway(
        private val responses: List<String>
    ) : ConversationGateway {
        val requests = mutableListOf<GlmChatRequest>()
        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            requests += request
            return Result.success(
                GlmChatResponse(responses.getOrElse(requests.lastIndex) { "bad" }, null, "stop", null, null, null, 1)
            )
        }
    }
}
