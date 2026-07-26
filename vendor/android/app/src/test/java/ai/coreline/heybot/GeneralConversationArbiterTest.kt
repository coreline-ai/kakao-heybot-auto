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
            arbiter.parse("""{"action":"REPLY","reply":"${"가".repeat(481)}"}""")
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
}
