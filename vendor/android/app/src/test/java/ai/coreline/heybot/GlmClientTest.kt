package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit

class GlmClientTest {
    private lateinit var server: MockWebServer
    private lateinit var apiKeyFile: File

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        apiKeyFile = File.createTempFile("iris-glm-test", ".token").apply {
            writeText("test-token")
            deleteOnExit()
        }
    }

    @After
    fun tearDown() {
        server.shutdown()
        apiKeyFile.delete()
    }

    @Test
    fun `sends a streamed ZAI chat completion request and combines chunks`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    "data: {\"id\":\"request-1\",\"choices\":[{\"delta\":{\"content\":\"안녕\"}}]}\n\n" +
                        "data: {\"choices\":[{\"finish_reason\":\"stop\",\"delta\":{\"content\":\"하세요\"}}],\"usage\":{\"prompt_tokens\":11,\"completion_tokens\":4,\"total_tokens\":15}}\n\n" +
                        "data: [DONE]\n\n"
                )
        )
        val client = GlmClient(testSettings())

        val response = client.generate(
            GlmChatRequest(
                model = "glm-4.5-flash",
                messages = listOf(GlmMessage("user", "안녕")),
                temperature = 0.2,
                maxTokens = 256
            )
        ).getOrThrow()

        val request = server.takeRequest()
        val requestBody = request.body.readUtf8()
        assertEquals("/api/paas/v4/chat/completions", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
        assertEquals("text/event-stream", request.getHeader("Accept"))
        assertTrue(requestBody.contains("\"stream\":true"))
        assertTrue(requestBody.contains("\"clear_thinking\":true"))
        assertEquals("안녕하세요", response.content)
        assertEquals(15, response.totalTokens)
    }

    @Test
    fun `measures latency through the completed stream`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                "data: {\"choices\":[{\"finish_reason\":\"stop\",\"delta\":{\"content\":\"OK\"}}]}\n\n" +
                    "data: [DONE]\n\n"
            )
        )
        var now = 100L
        val client = GlmClient(
            settings = testSettings(),
            nowMillis = { now.also { now += 250L } }
        )

        val response = client.generate(
            GlmChatRequest(
                model = "glm-4.5-flash",
                messages = listOf(GlmMessage("user", "ping")),
                temperature = 0.2,
                maxTokens = 16
            )
        ).getOrThrow()

        assertEquals(250L, response.latencyMillis)
    }

    @Test
    fun `maps unauthorized responses without exposing response body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("{\"error\":\"invalid key\"}"))
        val result = GlmClient(testSettings()).generate(
            GlmChatRequest(
                model = "glm-4.5-flash",
                messages = listOf(GlmMessage("user", "안녕")),
                temperature = 0.2,
                maxTokens = 256
            )
        )

        assertTrue(result.exceptionOrNull() is GlmFailure.Unauthorized)
    }

    @Test
    fun `rejects an empty choice and malformed successful response`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("data: {\"choices\":[]}\n\ndata: [DONE]\n\n"))
        server.enqueue(MockResponse().setResponseCode(200).setBody("data: not-json\n\n"))
        val request = GlmChatRequest(
            model = "glm-4.5-flash",
            messages = listOf(GlmMessage("user", "안녕")),
            temperature = 0.2,
            maxTokens = 256
        )
        val client = GlmClient(testSettings())

        assertTrue(client.generate(request).exceptionOrNull() is GlmFailure.EmptyResponse)
        assertTrue(client.generate(request).exceptionOrNull() is GlmFailure.InvalidResponse)
    }

    @Test
    fun `does not use reasoning content as a Kakao reply`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """data: {"choices":[{"delta":{"reasoning_content":"internal only"}}]}

                data: [DONE]

                """.trimIndent()
            )
        )

        val result = GlmClient(testSettings()).generate(
            GlmChatRequest(
                model = "glm-4.5-flash",
                messages = listOf(GlmMessage("user", "안녕")),
                temperature = 0.2,
                maxTokens = 256
            )
        )

        assertTrue(result.exceptionOrNull() is GlmFailure.EmptyResponse)
    }

    @Test
    fun `honors the shorter per-request timeout`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(2, TimeUnit.SECONDS)
                .setBody(
                    "data: {\"choices\":[{\"finish_reason\":\"stop\",\"delta\":{\"content\":\"늦은 응답\"}}]}\n\n"
                )
        )

        val result = GlmClient(testSettings()).generate(
            GlmChatRequest(
                model = "glm-4.5-flash",
                messages = listOf(GlmMessage("user", "ping")),
                temperature = 0.2,
                maxTokens = 16,
                timeoutMillis = 100L,
                kind = GlmRequestKind.GENERAL_CONVERSATION
            )
        )

        assertTrue(result.exceptionOrNull() is GlmFailure.Timeout)
    }

    @Test
    fun `default transport applies the configured budget to every socket phase`() {
        val client = GlmClient.defaultHttpClient(15_000L)

        assertEquals(15_000, client.connectTimeoutMillis)
        assertEquals(15_000, client.readTimeoutMillis)
        assertEquals(15_000, client.writeTimeoutMillis)
        assertEquals(15_000, client.callTimeoutMillis)
    }

    private fun testSettings(): GlmSettings = GlmSettings(
        baseUrl = server.url("/api/paas/v4/").toString(),
        model = "glm-4.5-flash",
        trigger = "헤이봇",
        allowedChatIds = setOf(18480337854645134L),
        apiKeyFile = apiKeyFile,
        timeoutMillis = 10_000L,
        maxTokens = 256,
        temperature = 0.2
    )
}
