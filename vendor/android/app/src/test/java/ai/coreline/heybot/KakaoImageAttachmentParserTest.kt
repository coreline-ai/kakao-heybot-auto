package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class KakaoImageAttachmentParserTest {
    private val now = 1_800_000_000_000L
    private val parser = KakaoImageAttachmentParser { now }

    @Test
    fun `synthetic Kakao attachment becomes a strict domain model`() {
        val result = parser.parse(10, 20, 30, "2", fixture())
        assertTrue(result is ImageAttachmentParseResult.Parsed)
        val image = (result as ImageAttachmentParseResult.Parsed).attachment
        assertEquals(10L, image.sourceLogId)
        assertEquals(1254, image.width)
        assertEquals(1_591_685L, image.declaredBytes)
        assertEquals("https://talk.kakaocdn.net/fake/signed.png?token=fixture", image.url)
    }

    @Test
    fun `rejects unsupported type host oversized and expired input`() {
        assertRejected("1", fixture(), ImageAttachmentParseFailure.UNSUPPORTED_MESSAGE_TYPE)
        assertRejected("2", fixture().replace("talk.kakaocdn.net", "evil.example"), ImageAttachmentParseFailure.FORBIDDEN_HOST)
        assertRejected("2", fixture().replace("1591685", "10485761"), ImageAttachmentParseFailure.INVALID_NUMBER)
        assertRejected("2", fixture().replace("1800000060000", "1799999999999"), ImageAttachmentParseFailure.EXPIRED)
    }

    private fun assertRejected(type: String, json: String, reason: ImageAttachmentParseFailure) {
        val result = parser.parse(10, 20, 30, type, json)
        assertEquals(reason, (result as ImageAttachmentParseResult.Rejected).reason)
    }

    private fun fixture() = """
        {
          "url":"https://talk.kakaocdn.net/fake/signed.png?token=fixture",
          "thumbnailUrl":"https://talk.kakaocdn.net/fake/thumb.png",
          "w":1254,"h":1254,"s":1591685,"expire":1800000060000,"mt":"image/png",
          "k":"synthetic-only","cs":"unused","cmt":""
        }
    """.trimIndent()
}
