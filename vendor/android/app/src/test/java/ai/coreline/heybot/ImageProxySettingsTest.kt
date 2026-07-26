package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ImageProxySettingsTest {
    @Test
    fun `is disabled by default`() {
        assertTrue(ImageProxySettings.load(emptyMap()) is ImageProxySettingsLoadResult.Disabled)
    }

    @Test
    fun `loads loopback settings and inherits GLM chat IDs`() {
        val result = ImageProxySettings.load(
            mapOf(
                "IRIS_IMAGE_PROXY_ENABLED" to "true",
                "IRIS_IMAGE_PROXY_SECRET_FILE" to "/data/local/private/image-route.secret",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to
                    "18480337854645134,18226456888539938,18243496625741211"
            )
        ) as ImageProxySettingsLoadResult.Ready

        assertEquals("http://127.0.0.1:4340", result.settings.baseUrl)
        assertEquals(3, result.settings.allowedChatIds.size)
        assertEquals(3, result.settings.maxPendingPerRoom)
        assertEquals(12 * 1024 * 1024, result.settings.imageMaxBytes)
        assertEquals(45_000L, result.settings.deliveryConfirmTimeoutMillis)
    }

    @Test
    fun `rejects external URL malformed ID and relative secret`() {
        val base = mapOf(
            "IRIS_IMAGE_PROXY_ENABLED" to "true",
            "IRIS_IMAGE_PROXY_SECRET_FILE" to "/data/local/private/image-route.secret",
            "IRIS_IMAGE_ALLOWED_CHAT_IDS" to "18480337854645134"
        )
        assertTrue(
            ImageProxySettings.load(
                base + ("IRIS_IMAGE_PROXY_BASE_URL" to "https://example.com")
            ) is ImageProxySettingsLoadResult.Invalid
        )
        assertTrue(
            ImageProxySettings.load(
                base + ("IRIS_IMAGE_ALLOWED_CHAT_IDS" to "not-an-id")
            ) is ImageProxySettingsLoadResult.Invalid
        )
        assertTrue(
            ImageProxySettings.load(
                base + ("IRIS_IMAGE_PROXY_SECRET_FILE" to "secret.txt")
            ) is ImageProxySettingsLoadResult.Invalid
        )
    }
}
