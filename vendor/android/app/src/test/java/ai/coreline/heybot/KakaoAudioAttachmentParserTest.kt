package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class KakaoAudioAttachmentParserTest {
    private val now = 1_800_000_000_000L
    private val parser = KakaoAudioAttachmentParser { now }

    @Test
    fun `parses the verified type 18 M4A contract without retaining the filename`() {
        val result = parser.parse(10, 20, 30, "18", fixture("sample.m4a"))
        assertTrue(result is AudioAttachmentParseResult.Parsed)
        val audio = (result as AudioAttachmentParseResult.Parsed).attachment
        assertEquals("m4a", audio.declaredExtension)
        assertEquals(8_674L, audio.declaredBytes)
        assertEquals("https://talk.kakaocdn.net/fake/audio?token=fixture", audio.sourceUrl)
        assertEquals("mp3", parsed("sample.mp3").declaredExtension)
        assertEquals("wav", parsed("sample.wav").declaredExtension)
    }

    @Test
    fun `rejects generic non-audio files unsafe URLs unknown fields and expired sources`() {
        assertRejected(fixture("report.pdf"), AudioAttachmentParseFailure.UNSUPPORTED_EXTENSION)
        assertRejected(fixture("archive.zip"), AudioAttachmentParseFailure.UNSUPPORTED_EXTENSION)
        assertRejected(fixture("page.html"), AudioAttachmentParseFailure.UNSUPPORTED_EXTENSION)
        assertRejected(fixture("sheet.xlsx"), AudioAttachmentParseFailure.UNSUPPORTED_EXTENSION)
        assertRejected(fixture("sample.m4a").replace("talk.kakaocdn.net", "evil.example"), AudioAttachmentParseFailure.FORBIDDEN_HOST)
        assertRejected(fixture("sample.m4a").dropLast(1) + ",\"extra\":1}", AudioAttachmentParseFailure.UNSUPPORTED_FIELD)
        assertRejected(fixture("sample.m4a").replace("1800000060000", "1799999999999"), AudioAttachmentParseFailure.EXPIRED)
        assertRejected(fixture("sample.m4a").replace("8674", "104857601"), AudioAttachmentParseFailure.INVALID_NUMBER)
        assertRejected(
            fixture("sample.m4a").replace("\"s\":8674", "\"s\":8675"),
            AudioAttachmentParseFailure.INVALID_NUMBER
        )
    }

    private fun assertRejected(json: String, reason: AudioAttachmentParseFailure) {
        val result = parser.parse(10, 20, 30, "18", json)
        assertEquals(reason, (result as AudioAttachmentParseResult.Rejected).reason)
    }

    private fun parsed(name: String): IncomingAudioAttachment =
        (parser.parse(10, 20, 30, "18", fixture(name)) as AudioAttachmentParseResult.Parsed).attachment

    private fun fixture(name: String) =
        """{"cs":"fixture","expire":1800000060000,"k":"fixture","name":"$name","s":8674,"size":8674,"url":"https://talk.kakaocdn.net/fake/audio?token=fixture"}"""
}
