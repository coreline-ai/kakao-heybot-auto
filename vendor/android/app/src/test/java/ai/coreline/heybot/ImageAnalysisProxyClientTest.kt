package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
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
            """{"jobId":"job","requestId":"vision:10:20:ocr","chatId":"10","status":"queued"}"""
        ))
        val source = IncomingImageAttachment(20, 10, 30, "https://talk.kakaocdn.net/fake.png?token=x", null, 100, 100, 500, 2_000_000_000_000, "image/png")
        val result = ImageAnalysisProxyClient(settings()).create("vision:10:20:ocr", 10, 30, source, VisionTask.OCR).getOrThrow()
        val request = server.takeRequest(); val body = request.body.readUtf8()
        assertEquals("/v1/vision/jobs", request.path)
        assertEquals("Bearer ${"v".repeat(48)}", request.getHeader("Authorization"))
        assertTrue(body.contains("\"url\":\"https://talk.kakaocdn.net/fake.png?token=x\""))
        assertTrue(body.contains("\"task\":\"ocr\""))
        assertFalse(body.contains("base64"))
        assertEquals("queued", result.status)
    }

    @Test fun `decodes strict successful result`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(
            """{"jobId":"job","requestId":"vision:10:20:translate_ko","chatId":"10","status":"succeeded","result":{"version":2,"task":"translate_ko","answer":"안녕하세요.","visibleObjects":[],"extractedText":["HELLO"],"uncertainty":"low"}}"""
        ))
        val job = ImageAnalysisProxyClient(settings()).status("job", 10).getOrThrow()
        assertEquals("안녕하세요.", job.result?.answer)
        assertEquals(VisionTask.TRANSLATE_KO, job.result?.task)
        assertEquals(listOf("HELLO"), job.result?.extractedText)
        assertEquals("/v1/vision/jobs/job?chatId=10", server.takeRequest().path)
    }

    @Test fun `keeps legacy describe v1 compatibility but rejects malformed v2`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(
            """{"jobId":"legacy","requestId":"vision:10:20","chatId":"10","status":"succeeded","result":{"version":1,"summary":"기존 설명","visibleObjects":[],"visibleText":[],"uncertainty":"medium"}}"""
        ))
        val client = ImageAnalysisProxyClient(settings())
        val legacy = client.status("legacy", 10).getOrThrow()
        assertEquals(VisionTask.DESCRIBE, legacy.result?.task)
        assertEquals("기존 설명", legacy.result?.answer)

        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(
            """{"jobId":"bad","requestId":"vision:10:21:ocr","chatId":"10","status":"succeeded","result":{"version":2,"answer":"HELLO","visibleObjects":[],"extractedText":[],"uncertainty":"low"}}"""
        ))
        val malformed = client.status("bad", 10)
        assertTrue(malformed.isFailure)
        assertTrue(malformed.exceptionOrNull() is VisionInvalidResponseException)
    }

    @Test fun `classifies authorization and transport failures without retrying`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        val unauthorized = ImageAnalysisProxyClient(settings()).status("job", 10)
        assertTrue(unauthorized.exceptionOrNull() is VisionAuthorizationException)

        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        val disconnected = ImageAnalysisProxyClient(settings()).status("job", 10)
        assertTrue(disconnected.exceptionOrNull() is VisionTransportException)
    }

    private fun settings() = ImageAnalysisSettings(
        server.url("/").toString().trimEnd('/'), secret
    )
}
