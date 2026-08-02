package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GeneralConversationArbiterTest {
    private val arbiter = GeneralConversationArbiter()

    @Test
    fun `accepts only a reply with the exact contract`() {
        val result = arbiter.parse("""{"action":"REPLY","reply":"도와드릴게요."}""")

        assertEquals(GeneralConversationDecision.Reply("도와드릴게요."), result)
    }

    @Test
    fun `wait and ignore require empty replies`() {
        assertEquals(
            GeneralConversationDecision.Wait,
            arbiter.parse("""{"action":"WAIT","reply":""}""")
        )
        assertEquals(
            GeneralConversationDecision.Ignore,
            arbiter.parse("""{"action":"IGNORE","reply":""}""")
        )
        assertEquals(
            GeneralConversationDecision.Invalid,
            arbiter.parse("""{"action":"WAIT","reply":"잠시만요"}""")
        )
    }

    @Test
    fun `rejects unknown fields malformed JSON and oversized replies`() {
        assertEquals(
            GeneralConversationDecision.Invalid,
            arbiter.parse("""{"action":"REPLY","reply":"답변","extra":"x"}""")
        )
        assertEquals(
            GeneralConversationDecision.Invalid,
            arbiter.parse("""{"action":1,"reply":"답변"}""")
        )
        assertEquals(GeneralConversationDecision.Invalid, arbiter.parse("not json"))
        assertTrue(
            arbiter.parse("""{"action":"REPLY","reply":"${"가".repeat(301)}"}""")
                is GeneralConversationDecision.Invalid
        )
    }

    @Test
    fun `uses the bounded general conversation request budget`() {
        val request = arbiter.buildRequest(
            GlmSettings(
                baseUrl = "https://api.z.ai/api/paas/v4/",
                model = "glm-4.5-flash",
                trigger = "헤이봇",
                allowedChatIds = setOf(1L),
                apiKeyFile = java.io.File("/tmp/test-token"),
                timeoutMillis = 120_000L,
                generalConversationTimeoutMillis = 12_000L,
                maxTokens = 128,
                temperature = 0.2
            ),
            "안녕하세요"
        )

        assertEquals(12_000L, request.timeoutMillis)
        assertEquals(GlmRequestKind.GENERAL_CONVERSATION, request.kind)
        assertEquals(384, request.maxTokens)
    }

    @Test
    fun `uses only supplied same user history and unfinished messages as context`() {
        val request = arbiter.buildRequest(
            settings = GlmSettings(
                baseUrl = "https://api.z.ai/api/paas/v4/",
                model = "glm-4.5-flash",
                trigger = "헤이봇",
                allowedChatIds = setOf(1L),
                apiKeyFile = java.io.File("/tmp/test-token"),
                timeoutMillis = 120_000L,
                maxTokens = 128,
                temperature = 0.2
            ),
            message = "현재 발화",
            history = listOf(
                ConversationTurn(
                    userMessage = "이전 질문",
                    assistantMessage = "이전 답변",
                    updatedAtMillis = 1L
                )
            ),
            pendingMessages = listOf("미완성 발화")
        )

        assertEquals(
            listOf("system", "user", "assistant", "user", "user"),
            request.messages.map { it.role }
        )
        assertEquals("이전 질문", request.messages[1].content)
        assertEquals("이전 답변", request.messages[2].content)
        assertTrue(request.messages[3].content.contains("미완성 발화"))
        assertEquals("현재 마지막 발화입니다.\n현재 발화", request.messages.last().content)
    }

    @Test
    fun `injects a vision result as untrusted data before the current utterance`() {
        val request = arbiter.buildRequest(
            settings = testSettings(),
            message = "가방은 무슨 색이야?",
            visionContext = VisionConversationContext(
                chatId = 1L,
                ownerUserId = 2L,
                sourceLogId = 3L,
                resultLogId = 4L,
                task = VisionTask.DESCRIBE,
                safeAnswer = "로봇 옆에 노란 가방이 있습니다.",
                uncertainty = "low",
                capabilityRevision = 1L,
                createdAtMillis = 1_000L,
                expiresAtMillis = 2_000L
            )
        )

        assertEquals(listOf("system", "user", "user"), request.messages.map { it.role })
        assertTrue(request.messages[1].content.contains("명령이 아닙니다"))
        assertTrue(request.messages[1].content.contains("노란 가방"))
        assertEquals("현재 마지막 발화입니다.\n가방은 무슨 색이야?", request.messages.last().content)
    }

    @Test
    fun `truncated response retry uses the largest budget and concise contract`() {
        val settings = testSettings()
        val request = arbiter.buildRequest(settings, "오늘 할 일을 세 단계로 정리해줘")
        val retry = arbiter.buildTruncationRetryRequest(request)

        assertEquals(384, request.maxTokens)
        assertEquals(512, retry.maxTokens)
        assertTrue(retry.messages.first().content.contains("180자 이내"))
    }

    private fun testSettings() = GlmSettings(
        baseUrl = "https://api.z.ai/api/paas/v4/",
        model = "glm-4.5-flash",
        trigger = "헤이봇",
        allowedChatIds = setOf(1L),
        apiKeyFile = java.io.File("/tmp/test-token"),
        timeoutMillis = 120_000L,
        maxTokens = 128,
        temperature = 0.2
    )
}
