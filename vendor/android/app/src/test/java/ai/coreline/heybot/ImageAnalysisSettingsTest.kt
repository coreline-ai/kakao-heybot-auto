package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ImageAnalysisSettingsTest {
    @Test fun `disabled by default and loads loopback configuration`() {
        assertEquals(ImageAnalysisSettingsLoadResult.Disabled, ImageAnalysisSettings.load(emptyMap()))
        val result = ImageAnalysisSettings.load(
            mapOf(
                "IRIS_VISION_PROXY_ENABLED" to "true",
                "IRIS_VISION_PROXY_BASE_URL" to "http://127.0.0.1:4340",
                "IRIS_VISION_PROXY_SECRET_FILE" to "/data/local/private/vision.token"
            )
        )
        assertTrue(result is ImageAnalysisSettingsLoadResult.Ready)
        val settings = (result as ImageAnalysisSettingsLoadResult.Ready).settings
        assertEquals(1_800_000L, settings.recentImageWindowMillis)
    }

    @Test fun `loads configurable recent image window and rejects unsafe range`() {
        val base = mapOf(
            "IRIS_VISION_PROXY_ENABLED" to "true",
            "IRIS_VISION_PROXY_SECRET_FILE" to "/data/local/private/vision.token"
        )
        val ready = ImageAnalysisSettings.load(
            base + ("IRIS_VISION_RECENT_IMAGE_WINDOW_MS" to "3600000")
        ) as ImageAnalysisSettingsLoadResult.Ready
        assertEquals(3_600_000L, ready.settings.recentImageWindowMillis)
        assertTrue(
            ImageAnalysisSettings.load(
                base + ("IRIS_VISION_RECENT_IMAGE_WINDOW_MS" to "1000")
            ) is ImageAnalysisSettingsLoadResult.Invalid
        )
    }

    @Test fun `legacy static vision room list is ignored in favor of capability policy`() {
        val result = ImageAnalysisSettings.load(
            mapOf(
                "IRIS_VISION_PROXY_ENABLED" to "true",
                "IRIS_VISION_PROXY_SECRET_FILE" to "/data/local/private/vision.token",
                "IRIS_VISION_ALLOWED_CHAT_IDS" to "not,a,valid,room,list"
            )
        )

        assertTrue(result is ImageAnalysisSettingsLoadResult.Ready)
    }
}
