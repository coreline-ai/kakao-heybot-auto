package ai.coreline.heybot

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class KakaoAudioShareFormatTest {
    @Test
    fun `share formats parse case insensitively and validate format-bound magic`() {
        assertTrue(KakaoAudioShareFormat.parse("MP3") === KakaoAudioShareFormat.MP3)
        assertTrue(KakaoAudioShareFormat.parse("m4a") === KakaoAudioShareFormat.M4A)
        assertTrue(KakaoAudioShareFormat.parse("wav") === KakaoAudioShareFormat.WAV)
        assertNull(KakaoAudioShareFormat.parse("pdf"))

        assertTrue(KakaoAudioShareFormat.MP3.matchesMagic("ID3fixture000".toByteArray()))
        assertTrue(KakaoAudioShareFormat.M4A.matchesMagic("0000ftypM4A ".toByteArray()))
        assertTrue(KakaoAudioShareFormat.WAV.matchesMagic("RIFF0000WAVE".toByteArray()))
        assertFalse(KakaoAudioShareFormat.MP3.matchesMagic("PK0304000000".toByteArray()))
        assertFalse(KakaoAudioShareFormat.WAV.matchesMagic("0000ftypM4A ".toByteArray()))
    }
}
