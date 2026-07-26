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

class ImageProxyClientTest {
    private lateinit var server: MockWebServer
    private lateinit var secretFile: File

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        secretFile = File.createTempFile("image-route", ".secret").apply {
            writeText("r".repeat(48))
            deleteOnExit()
        }
    }

    @After
    fun tearDown() {
        server.shutdown()
        secretFile.delete()
    }

    @Test
    fun `creates a job with string IDs and route authorization`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(202).setHeader("Content-Type", "application/json")
                .setBody(
                    """
                    {"jobId":"11111111-1111-4111-8111-111111111111",
                     "requestId":"iris-image-1","chatId":"18480337854645134",
                     "status":"queued"}
                    """.trimIndent()
                )
        )
        val client = ImageProxyClient(settings())

        val job = client.create(
            requestId = "iris-image-1",
            chatId = 18480337854645134L,
            userId = 7216943976749157453L,
            logId = 900719925474099312L,
            prompt = "분홍색 로봇"
        ).getOrThrow()

        val request = server.takeRequest()
        val body = request.body.readUtf8()
        assertEquals("Bearer ${"r".repeat(48)}", request.getHeader("Authorization"))
        assertTrue(body.contains("\"chatId\":\"18480337854645134\""))
        assertTrue(body.contains("\"userId\":\"7216943976749157453\""))
        assertTrue(body.contains("\"logId\":\"900719925474099312\""))
        assertEquals("queued", job.status)
    }

    @Test
    fun `downloads only bounded PNG bytes`() = runBlocking {
        val png = validPngHeader()
        server.enqueue(
            MockResponse().setResponseCode(200).setHeader("Content-Type", "image/png")
                .setBody(okio.Buffer().write(png))
        )
        val client = ImageProxyClient(settings())

        assertTrue(
            client.download("job", 18480337854645134L)
                .getOrThrow()
                .contentEquals(png)
        )
        assertEquals(
            "/v1/image/jobs/job/file?chatId=18480337854645134",
            server.takeRequest().path
        )
    }

    private fun settings() = ImageProxySettings(
        baseUrl = server.url("/").toString().trimEnd('/'),
        routeSecretFile = secretFile,
        allowedChatIds = setOf(18480337854645134L),
        imageMaxBytes = 1024
    )

    private fun validPngHeader(): ByteArray = ByteArray(24).apply {
        byteArrayOf(
            0x89.toByte(), 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a
        ).copyInto(this)
        this[18] = 1 // width 256
        this[22] = 1 // height 256
    }
}
