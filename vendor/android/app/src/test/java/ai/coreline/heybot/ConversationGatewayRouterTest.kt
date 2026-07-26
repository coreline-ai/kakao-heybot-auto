package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class ConversationGatewayRouterTest {
    @Test
    fun `routes requests according to the global engine mode`() = runBlocking {
        val store = ConversationEngineModeStore.inMemory()
        val seen = mutableListOf<String>()
        fun gateway(name: String) = ConversationGateway { request ->
            seen += name
            Result.success(
                GlmChatResponse(
                    content = name,
                    requestId = null,
                    finishReason = "stop",
                    promptTokens = null,
                    completionTokens = null,
                    totalTokens = null,
                    latencyMillis = 1
                )
            )
        }
        val router = ConversationGatewayRouter(store, gateway("glm"), gateway("codex"), gateway("grok"))
        val request = GlmChatRequest("glm", listOf(GlmMessage("user", "안녕")), 0.2, 32)

        router.generate(request)
        store.set(ConversationEngine.CODEX)
        router.generate(request)
        store.set(ConversationEngine.GROK)
        router.generate(request)

        assertEquals(listOf("glm", "codex", "grok"), seen)
    }
}
