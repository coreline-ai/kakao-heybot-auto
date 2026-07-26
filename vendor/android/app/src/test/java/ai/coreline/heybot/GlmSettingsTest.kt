package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GlmSettingsTest {
    @Test
    fun `GLM is disabled unless explicitly enabled`() {
        assertTrue(GlmSettings.load(emptyMap()) is GlmSettingsLoadResult.Disabled)
    }

    @Test
    fun `loads the explicitly configured Coreline settings`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Ready)
        val settings = (result as GlmSettingsLoadResult.Ready).settings
        assertEquals("glm-4.5-flash", settings.model)
        assertEquals(null, settings.fallbackModel)
        assertEquals("헤이봇", settings.trigger)
        assertEquals(setOf(18480337854645134L), settings.allowedChatIds)
        assertEquals(GlmSettings.DEFAULT_BASE_URL, settings.baseUrl)
        assertEquals(120_000L, settings.timeoutMillis)
        assertEquals(15_000L, settings.generalConversationTimeoutMillis)
        assertEquals(128, settings.maxTokens)
        assertEquals(2, settings.rateLimitRetries)
        assertEquals(8, settings.roomQueueCapacity)
        assertEquals(24, settings.totalQueueCapacity)
        assertEquals(2, settings.maxConcurrency)
        assertEquals(3, settings.roomRateMaxRequests)
        assertEquals(5, settings.userRateMaxRequests)
        assertEquals(8_000L, settings.duplicateWindowMillis)
        assertEquals(4, settings.memoryMaxTurns)
        assertEquals(30 * 60 * 1000L, settings.memoryTtlMillis)
        assertEquals(null, settings.adminControlChatId)
    }

    @Test
    fun `rejects enabled GLM without an allow-listed chat ID`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `rejects malformed allow-listed chat IDs instead of silently dropping them`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134,not-a-number",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `rejects a non HTTPS GLM endpoint`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_BASE_URL" to "http://api.z.ai/api/paas/v4/",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `rejects an excessive rate limit retry count`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token",
                "IRIS_GLM_RATE_LIMIT_RETRIES" to "4"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `loads a distinct optional fallback model`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_FALLBACK_MODEL" to "glm-4.7-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertEquals("glm-4.7-flash", (result as GlmSettingsLoadResult.Ready).settings.fallbackModel)
    }

    @Test
    fun `ignores a fallback identical to the primary model`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_FALLBACK_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
            )
        )

        assertEquals(null, (result as GlmSettingsLoadResult.Ready).settings.fallbackModel)
    }

    @Test
    fun `rejects an out-of-range timeout`() {
        val result = GlmSettings.load(
            mapOf(
                "IRIS_GLM_ENABLED" to "true",
                "IRIS_GLM_MODEL" to "glm-4.5-flash",
                "IRIS_GLM_TRIGGER" to "헤이봇",
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token",
                "IRIS_GLM_TIMEOUT_MS" to "120001"
            )
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `loads and bounds the shorter general conversation timeout`() {
        val loaded = GlmSettings.load(
            validEnvironment() + mapOf(
                "IRIS_GLM_TIMEOUT_MS" to "30000",
                "IRIS_GENERAL_CONVERSATION_TIMEOUT_MS" to "7000"
            )
        ) as GlmSettingsLoadResult.Ready
        assertEquals(7_000L, loaded.settings.generalConversationTimeoutMillis)

        assertTrue(
            GlmSettings.load(
                validEnvironment() + mapOf(
                    "IRIS_GLM_TIMEOUT_MS" to "10000",
                    "IRIS_GENERAL_CONVERSATION_TIMEOUT_MS" to "10001"
                )
            ) is GlmSettingsLoadResult.Invalid
        )
    }

    @Test
    fun `loads P1 stability overrides`() {
        val result = GlmSettings.load(
            validEnvironment() + mapOf(
                "IRIS_GLM_ROOM_QUEUE_CAPACITY" to "10",
                "IRIS_GLM_TOTAL_QUEUE_CAPACITY" to "30",
                "IRIS_GLM_MAX_CONCURRENCY" to "3",
                "IRIS_GLM_ROOM_RATE_WINDOW_MS" to "45000",
                "IRIS_GLM_ROOM_RATE_MAX" to "4",
                "IRIS_GLM_USER_RATE_WINDOW_MS" to "90000",
                "IRIS_GLM_USER_RATE_MAX" to "7",
                "IRIS_GLM_DUPLICATE_WINDOW_MS" to "9000",
                "IRIS_GLM_MEMORY_FILE" to "/data/local/private/custom-memory.json",
                "IRIS_GLM_MEMORY_MAX_TURNS" to "6",
                "IRIS_GLM_MEMORY_TTL_MS" to "3600000",
                "IRIS_BOT_ADMIN_USER_IDS_FILE" to "/data/local/private/custom-admins.txt"
            )
        ) as GlmSettingsLoadResult.Ready

        assertEquals(10, result.settings.roomQueueCapacity)
        assertEquals(30, result.settings.totalQueueCapacity)
        assertEquals(3, result.settings.maxConcurrency)
        assertEquals(45_000L, result.settings.roomRateWindowMillis)
        assertEquals(4, result.settings.roomRateMaxRequests)
        assertEquals(90_000L, result.settings.userRateWindowMillis)
        assertEquals(7, result.settings.userRateMaxRequests)
        assertEquals(9_000L, result.settings.duplicateWindowMillis)
        assertEquals(6, result.settings.memoryMaxTurns)
        assertEquals(3_600_000L, result.settings.memoryTtlMillis)
    }

    @Test
    fun `rejects invalid P1 queue and relative private paths`() {
        assertTrue(
            GlmSettings.load(
                validEnvironment() + ("IRIS_GLM_TOTAL_QUEUE_CAPACITY" to "4")
            ) is GlmSettingsLoadResult.Invalid
        )
        assertTrue(
            GlmSettings.load(
                validEnvironment() + ("IRIS_GLM_MEMORY_FILE" to "relative.json")
            ) is GlmSettingsLoadResult.Invalid
        )
        assertTrue(
            GlmSettings.load(
                validEnvironment() + ("IRIS_BOT_ADMIN_USER_IDS_FILE" to "admins.txt")
            ) is GlmSettingsLoadResult.Invalid
        )
        assertTrue(
            GlmSettings.load(
                validEnvironment() + ("IRIS_GLM_MAX_CONCURRENCY" to "two")
            ) is GlmSettingsLoadResult.Invalid
        )
    }

    @Test
    fun `loads an allow-listed admin control room only`() {
        val result = GlmSettings.load(
            validEnvironment() + ("IRIS_BOT_ADMIN_CONTROL_CHAT_ID" to "18480337854645134")
        ) as GlmSettingsLoadResult.Ready

        assertEquals(18480337854645134L, result.settings.adminControlChatId)
    }

    @Test
    fun `rejects an admin control room outside the GLM room allowlist`() {
        val result = GlmSettings.load(
            validEnvironment() + ("IRIS_BOT_ADMIN_CONTROL_CHAT_ID" to "18243496625741211")
        )

        assertTrue(result is GlmSettingsLoadResult.Invalid)
    }

    @Test
    fun `loads an explicit general conversation subset and circuit settings`() {
        val result = GlmSettings.load(
            validEnvironment() + mapOf(
                "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134,18226456888539938",
                "IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GENERAL_CONVERSATION_BLOCK_FILE" to
                    "/data/local/private/iris-general-conversation-blocks.txt",
                "IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS" to "300000",
                "IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD" to "3"
            )
        ) as GlmSettingsLoadResult.Ready

        val general = requireNotNull(result.settings.generalConversation)
        assertEquals(setOf(18480337854645134L), general.allowedChatIds)
        assertEquals(300_000L, general.circuitWindowMillis)
        assertEquals(3, general.circuitFailureThreshold)
    }

    @Test
    fun `invalid general conversation settings fail closed without disabling wake word GLM`() {
        val invalidValues = listOf(
            "",
            "0",
            "-1",
            "not-a-number",
            "18480337854645134,18480337854645134",
            "18226456888539938"
        )
        invalidValues.forEach { raw ->
            val result = GlmSettings.load(
                validEnvironment() +
                    ("IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS" to raw)
            ) as GlmSettingsLoadResult.Ready
            assertEquals(null, result.settings.generalConversation)
        }

        val relativePath = GlmSettings.load(
            validEnvironment() + mapOf(
                "IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GENERAL_CONVERSATION_BLOCK_FILE" to "blocks.txt"
            )
        ) as GlmSettingsLoadResult.Ready
        assertEquals(null, relativePath.settings.generalConversation)

        val badThreshold = GlmSettings.load(
            validEnvironment() + mapOf(
                "IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS" to "18480337854645134",
                "IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD" to "0"
            )
        ) as GlmSettingsLoadResult.Ready
        assertEquals(null, badThreshold.settings.generalConversation)
    }

    private fun validEnvironment() = mapOf(
        "IRIS_GLM_ENABLED" to "true",
        "IRIS_GLM_MODEL" to "glm-4.5-flash",
        "IRIS_GLM_TRIGGER" to "헤이봇",
        "IRIS_GLM_ALLOWED_CHAT_IDS" to "18480337854645134",
        "IRIS_GLM_API_KEY_FILE" to "/data/local/private/zai-token"
    )
}
