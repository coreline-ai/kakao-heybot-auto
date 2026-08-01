package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File

class ImageAnalysisProxyClientTest {
    private lateinit var server: MockWebServer
    private lateinit var secret: File

    @Before fun setUp() {
        server = MockWebServer().apply { start() }
        secret = File.createTempFile("vision-route", ".secret").apply { writeText("v".repeat(48)) }
    }
    @After fun tearDown() { server.shutdown(); secret.delete() }

    @Test fun `creates scoped source job without base64 image`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(202).setHeader("Content-Type", "application/json").setBody(
            """{"jobId":"job","requestId":"vision:10:20","chatId":"10","status":"queued"}"""
        ))
        val source = IncomingImageAttachment(20, 10, 30, "https://talk.kakaocdn.net/fake.png?token=x", null, 100, 100, 500, 2_000_000_000_000, "image/png")
        val result = ImageAnalysisProxyClient(settings()).create("vision:10:20", 10, 30, source).getOrThrow()
        val request = server.takeRequest(); val body = request.body.readUtf8()
        assertEquals("/v1/vision/jobs", request.path)
        assertEquals("Bearer ${"v".repeat(48)}", request.getHeader("Authorization"))
        assertTrue(body.contains("\"url\":\"https://talk.kakaocdn.net/fake.png?token=x\""))
        assertFalse(body.contains("base64"))
        assertEquals("queued", result.status)
    }

    @Test fun `decodes strict successful result`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(
            """{"jobId":"job","requestId":"vision:10:20","chatId":"10","status":"succeeded","result":{"version":1,"summary":"로봇입니다.","visibleObjects":["로봇"],"visibleText":[],"uncertainty":"low"}}"""
        ))
        val job = ImageAnalysisProxyClient(settings()).status("job", 10).getOrThrow()
        assertEquals("로봇입니다.", job.result?.summary)
        assertEquals("/v1/vision/jobs/job?chatId=10", server.takeRequest().path)
    }

    private fun settings() = ImageAnalysisSettings(
        server.url("/").toString().trimEnd('/'), secret, setOf(10L)
    )
}
