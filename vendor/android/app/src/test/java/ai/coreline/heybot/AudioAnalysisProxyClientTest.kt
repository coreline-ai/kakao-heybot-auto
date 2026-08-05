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

class AudioAnalysisProxyClientTest {
    private lateinit var server: MockWebServer
    private lateinit var secret: File

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
        secret = File.createTempFile("audio-route", ".secret").apply {
            writeText("a".repeat(48))
        }
    }

    @After
    fun tearDown() {
        server.shutdown()
        secret.delete()
    }

    @Test
    fun `create always encodes required Korean language contract`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(202)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"version":1,"jobId":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","requestId":"audio:10:20","chatId":"10","status":"queued"}"""
                )
        )
        val source = IncomingAudioAttachment(
            sourceLogId = 20,
            chatId = 10,
            userId = 30,
            sourceUrl = "https://talk.kakaocdn.net/audio.m4a",
            declaredBytes = 100,
            expiresAtMillis = 2_000_000_000_000,
            declaredExtension = "m4a"
        )

        val result = AudioAnalysisProxyClient(settings())
            .create("audio:10:20", 10, source).getOrThrow()
        val request = server.takeRequest()
        val body = request.body.readUtf8()

        assertEquals("/v1/audio/transcriptions", request.path)
        assertEquals("Bearer ${"a".repeat(48)}", request.getHeader("Authorization"))
        assertTrue(body.contains("\"language\":\"ko\""))
        assertEquals("queued", result.status)
    }

    private fun settings() = AudioAnalysisSettings(
        baseUrl = server.url("/").toString().trimEnd('/'),
        routeSecretFile = secret,
        allowedChatIds = setOf(10L),
        stateFile = File("/tmp/audio-client-test-state")
    )
}
