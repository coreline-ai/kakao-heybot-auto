package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AudioAnalysisSettingsTest {
    @Test
    fun `audio stays disabled unless explicitly enabled`() {
        assertEquals(AudioAnalysisSettingsLoadResult.Disabled, AudioAnalysisSettings.load(emptyMap()))
    }

    @Test
    fun `requires loopback manager and absolute secret`() {
        val common = mapOf(
            "IRIS_AUDIO_PROXY_ENABLED" to "true",
            "IRIS_AUDIO_PROXY_SECRET_FILE" to "/tmp/audio.secret",
            "IRIS_AUDIO_ALLOWED_CHAT_IDS" to "10,20"
        )
        assertTrue(AudioAnalysisSettings.load(common) is AudioAnalysisSettingsLoadResult.Ready)
        assertTrue(
            AudioAnalysisSettings.load(common + ("IRIS_AUDIO_PROXY_BASE_URL" to "http://10.0.0.1:4340"))
                is AudioAnalysisSettingsLoadResult.Invalid
        )
        assertTrue(
            AudioAnalysisSettings.load(common + ("IRIS_AUDIO_PROXY_SECRET_FILE" to File("relative").path))
                is AudioAnalysisSettingsLoadResult.Invalid
        )
    }
}
