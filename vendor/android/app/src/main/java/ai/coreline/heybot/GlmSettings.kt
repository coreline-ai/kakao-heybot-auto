package ai.coreline.heybot

import java.io.File

data class GeneralConversationSettings(
    val allowedChatIds: Set<Long>,
    val blockFile: File,
    val modeFile: File,
    val circuitWindowMillis: Long,
    val circuitFailureThreshold: Int
)

data class RoomCapabilitySettings(
    val policyFile: File
)

/**
 * GLM automatic-reply settings intentionally live outside Iris' JSON config.
 * Configurable logs and persists that JSON, so it must never contain an API key.
 */
data class GlmSettings(
    val baseUrl: String,
    val model: String,
    val fallbackModel: String? = null,
    val trigger: String,
    val allowedChatIds: Set<Long>,
    val apiKeyFile: File,
    val timeoutMillis: Long,
    val generalConversationTimeoutMillis: Long = DEFAULT_GENERAL_CONVERSATION_TIMEOUT_MILLIS,
    val maxTokens: Int,
    val temperature: Double,
    val rateLimitRetries: Int = DEFAULT_RATE_LIMIT_RETRIES,
    val roomQueueCapacity: Int = DEFAULT_ROOM_QUEUE_CAPACITY,
    val totalQueueCapacity: Int = DEFAULT_TOTAL_QUEUE_CAPACITY,
    val maxConcurrency: Int = DEFAULT_MAX_CONCURRENCY,
    val roomRateWindowMillis: Long = DEFAULT_ROOM_RATE_WINDOW_MILLIS,
    val roomRateMaxRequests: Int = DEFAULT_ROOM_RATE_MAX_REQUESTS,
    val userRateWindowMillis: Long = DEFAULT_USER_RATE_WINDOW_MILLIS,
    val userRateMaxRequests: Int = DEFAULT_USER_RATE_MAX_REQUESTS,
    val duplicateWindowMillis: Long = DEFAULT_DUPLICATE_WINDOW_MILLIS,
    val memoryFile: File = File(DEFAULT_MEMORY_FILE),
    val memoryMaxTurns: Int = DEFAULT_MEMORY_MAX_TURNS,
    val memoryTtlMillis: Long = DEFAULT_MEMORY_TTL_MILLIS,
    val memoryMaxBytes: Int = DEFAULT_MEMORY_MAX_BYTES,
    val memoryMaxConversations: Int = DEFAULT_MEMORY_MAX_CONVERSATIONS,
    val adminUserIdsFile: File = File(DEFAULT_ADMIN_USER_IDS_FILE),
    /** Null disables privileged chat commands rather than accepting them in every room. */
    val adminControlChatId: Long? = null,
    /** Null keeps wake-word GLM enabled but disables ambient general conversation. */
    val generalConversation: GeneralConversationSettings? = null,
    /** Null makes the production room capability boundary fail closed. */
    val roomCapabilities: RoomCapabilitySettings? = null
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = apiKeyFile.takeIf { it.isFile }
            ?.readText()
            ?.trim()
            .orEmpty()

        require(token.isNotBlank()) { "GLM API key file is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4/"
        // Free-model queues can legitimately exceed one minute; streaming still
        // delivers the first chunk early, while this protects a slow completion.
        const val DEFAULT_TIMEOUT_MILLIS = 120_000L
        // Wake-word questions may wait for a free-model queue, but ambient
        // arbitration must release its shared room worker quickly.
        const val DEFAULT_GENERAL_CONVERSATION_TIMEOUT_MILLIS = 15_000L
        // The bot's policy asks for short 2–4 sentence replies.  A smaller
        // completion cap reduces time-to-final-answer on shared/free capacity.
        const val DEFAULT_MAX_TOKENS = 128
        const val DEFAULT_TEMPERATURE = 0.2
        const val DEFAULT_RATE_LIMIT_RETRIES = 2
        const val DEFAULT_ROOM_QUEUE_CAPACITY = 8
        const val DEFAULT_TOTAL_QUEUE_CAPACITY = 24
        const val DEFAULT_MAX_CONCURRENCY = 2
        const val DEFAULT_ROOM_RATE_WINDOW_MILLIS = 30_000L
        const val DEFAULT_ROOM_RATE_MAX_REQUESTS = 3
        const val DEFAULT_USER_RATE_WINDOW_MILLIS = 60_000L
        const val DEFAULT_USER_RATE_MAX_REQUESTS = 5
        const val DEFAULT_DUPLICATE_WINDOW_MILLIS = 8_000L
        const val DEFAULT_MEMORY_FILE = "/data/local/private/iris-bot-memory.json"
        const val DEFAULT_MEMORY_MAX_TURNS = 4
        const val DEFAULT_MEMORY_TTL_MILLIS = 30 * 60 * 1000L
        const val DEFAULT_MEMORY_MAX_BYTES = 1024 * 1024
        const val DEFAULT_MEMORY_MAX_CONVERSATIONS = 512
        const val DEFAULT_ADMIN_USER_IDS_FILE = "/data/local/private/iris-bot-admins.txt"
        const val DEFAULT_GENERAL_CONVERSATION_BLOCK_FILE =
            "/data/local/private/iris-general-conversation-blocks.txt"
        const val DEFAULT_GENERAL_CONVERSATION_MODE_FILE =
            "/data/local/private/iris-general-conversation-mode.json"
        const val DEFAULT_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MILLIS = 5 * 60 * 1000L
        const val DEFAULT_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD = 3
        const val DEFAULT_ROOM_CAPABILITY_POLICY_FILE =
            "/data/local/private/iris-room-capabilities.json"

        fun load(environment: Map<String, String> = System.getenv()): GlmSettingsLoadResult {
            val enabled = environment["IRIS_GLM_ENABLED"].equals("true", ignoreCase = true)
            if (!enabled) return GlmSettingsLoadResult.Disabled

            val baseUrl = environment["IRIS_GLM_BASE_URL"]
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?: DEFAULT_BASE_URL
            if (!baseUrl.startsWith("https://")) {
                return GlmSettingsLoadResult.Invalid("GLM base URL must use HTTPS")
            }

            val model = environment["IRIS_GLM_MODEL"]?.trim().orEmpty()
            if (model.isBlank()) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_MODEL is required when GLM is enabled")
            }

            val trigger = environment["IRIS_GLM_TRIGGER"]?.trim().orEmpty()
            if (trigger.isBlank()) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_TRIGGER is required when GLM is enabled")
            }

            val fallbackModel = environment["IRIS_GLM_FALLBACK_MODEL"]
                ?.trim()
                ?.takeIf { it.isNotEmpty() && it != model }

            val rawChatIds = environment["IRIS_GLM_ALLOWED_CHAT_IDS"]
                .orEmpty()
                .split(',')
                .map { it.trim() }
            if (rawChatIds.isEmpty() || rawChatIds.any { it.isBlank() }) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_ALLOWED_CHAT_IDS must contain chat IDs")
            }
            val allowedChatIds = rawChatIds.map { it.toLongOrNull() }
            if (allowedChatIds.any { it == null || it <= 0L }) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_ALLOWED_CHAT_IDS contains an invalid chat ID")
            }

            val apiKeyFilePath = environment["IRIS_GLM_API_KEY_FILE"]?.trim().orEmpty()
            if (apiKeyFilePath.isBlank()) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_API_KEY_FILE is required when GLM is enabled")
            }

            val timeoutMillis = parseLong(
                environment,
                "IRIS_GLM_TIMEOUT_MS",
                DEFAULT_TIMEOUT_MILLIS
            ) ?: return GlmSettingsLoadResult.Invalid("IRIS_GLM_TIMEOUT_MS must be an integer")
            if (timeoutMillis !in 1_000L..120_000L) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_TIMEOUT_MS must be between 1000 and 120000")
            }

            val generalConversationTimeoutMillis = parseLong(
                environment,
                "IRIS_GENERAL_CONVERSATION_TIMEOUT_MS",
                minOf(DEFAULT_GENERAL_CONVERSATION_TIMEOUT_MILLIS, timeoutMillis)
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GENERAL_CONVERSATION_TIMEOUT_MS must be an integer"
            )
            if (generalConversationTimeoutMillis !in 1_000L..timeoutMillis) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GENERAL_CONVERSATION_TIMEOUT_MS must be between 1000 and IRIS_GLM_TIMEOUT_MS"
                )
            }

            val maxTokens = parseInt(
                environment,
                "IRIS_GLM_MAX_TOKENS",
                DEFAULT_MAX_TOKENS
            ) ?: return GlmSettingsLoadResult.Invalid("IRIS_GLM_MAX_TOKENS must be an integer")
            if (maxTokens !in 1..512) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_MAX_TOKENS must be between 1 and 512")
            }

            val temperature = parseDouble(
                environment,
                "IRIS_GLM_TEMPERATURE",
                DEFAULT_TEMPERATURE
            ) ?: return GlmSettingsLoadResult.Invalid("IRIS_GLM_TEMPERATURE must be a number")
            if (temperature !in 0.0..2.0) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_TEMPERATURE must be between 0 and 2")
            }

            val rateLimitRetries = parseInt(
                environment,
                "IRIS_GLM_RATE_LIMIT_RETRIES",
                DEFAULT_RATE_LIMIT_RETRIES
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_RATE_LIMIT_RETRIES must be an integer"
            )
            if (rateLimitRetries !in 0..3) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_RATE_LIMIT_RETRIES must be between 0 and 3")
            }

            val roomQueueCapacity = parseInt(
                environment,
                "IRIS_GLM_ROOM_QUEUE_CAPACITY",
                DEFAULT_ROOM_QUEUE_CAPACITY
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_ROOM_QUEUE_CAPACITY must be an integer"
            )
            if (roomQueueCapacity !in 1..100) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_ROOM_QUEUE_CAPACITY must be between 1 and 100")
            }

            val totalQueueCapacity = parseInt(
                environment,
                "IRIS_GLM_TOTAL_QUEUE_CAPACITY",
                DEFAULT_TOTAL_QUEUE_CAPACITY
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_TOTAL_QUEUE_CAPACITY must be an integer"
            )
            if (totalQueueCapacity !in roomQueueCapacity..500) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_TOTAL_QUEUE_CAPACITY must be between room capacity and 500"
                )
            }

            val maxConcurrency = parseInt(
                environment,
                "IRIS_GLM_MAX_CONCURRENCY",
                DEFAULT_MAX_CONCURRENCY
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_MAX_CONCURRENCY must be an integer"
            )
            if (maxConcurrency !in 1..16) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_MAX_CONCURRENCY must be between 1 and 16")
            }

            val roomRateWindowMillis = parseLong(
                environment,
                "IRIS_GLM_ROOM_RATE_WINDOW_MS",
                DEFAULT_ROOM_RATE_WINDOW_MILLIS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_ROOM_RATE_WINDOW_MS must be an integer"
            )
            if (roomRateWindowMillis !in 1_000L..3_600_000L) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_ROOM_RATE_WINDOW_MS must be between 1000 and 3600000"
                )
            }

            val roomRateMaxRequests = parseInt(
                environment,
                "IRIS_GLM_ROOM_RATE_MAX",
                DEFAULT_ROOM_RATE_MAX_REQUESTS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_ROOM_RATE_MAX must be an integer"
            )
            if (roomRateMaxRequests !in 1..100) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_ROOM_RATE_MAX must be between 1 and 100")
            }

            val userRateWindowMillis = parseLong(
                environment,
                "IRIS_GLM_USER_RATE_WINDOW_MS",
                DEFAULT_USER_RATE_WINDOW_MILLIS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_USER_RATE_WINDOW_MS must be an integer"
            )
            if (userRateWindowMillis !in 1_000L..3_600_000L) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_USER_RATE_WINDOW_MS must be between 1000 and 3600000"
                )
            }

            val userRateMaxRequests = parseInt(
                environment,
                "IRIS_GLM_USER_RATE_MAX",
                DEFAULT_USER_RATE_MAX_REQUESTS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_USER_RATE_MAX must be an integer"
            )
            if (userRateMaxRequests !in 1..100) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_USER_RATE_MAX must be between 1 and 100")
            }

            val duplicateWindowMillis = parseLong(
                environment,
                "IRIS_GLM_DUPLICATE_WINDOW_MS",
                DEFAULT_DUPLICATE_WINDOW_MILLIS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_DUPLICATE_WINDOW_MS must be an integer"
            )
            if (duplicateWindowMillis !in 1_000L..60_000L) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_DUPLICATE_WINDOW_MS must be between 1000 and 60000"
                )
            }

            val memoryFile = File(
                environment["IRIS_GLM_MEMORY_FILE"]
                    ?.trim()
                    ?.takeIf { it.isNotEmpty() }
                    ?: DEFAULT_MEMORY_FILE
            )
            if (!memoryFile.isAbsolute) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_MEMORY_FILE must be an absolute path")
            }

            val memoryMaxTurns = parseInt(
                environment,
                "IRIS_GLM_MEMORY_MAX_TURNS",
                DEFAULT_MEMORY_MAX_TURNS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_MEMORY_MAX_TURNS must be an integer"
            )
            if (memoryMaxTurns !in 1..20) {
                return GlmSettingsLoadResult.Invalid("IRIS_GLM_MEMORY_MAX_TURNS must be between 1 and 20")
            }

            val memoryTtlMillis = parseLong(
                environment,
                "IRIS_GLM_MEMORY_TTL_MS",
                DEFAULT_MEMORY_TTL_MILLIS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_MEMORY_TTL_MS must be an integer"
            )
            if (memoryTtlMillis !in 60_000L..86_400_000L) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_MEMORY_TTL_MS must be between 60000 and 86400000"
                )
            }

            val memoryMaxBytes = parseInt(
                environment,
                "IRIS_GLM_MEMORY_MAX_BYTES",
                DEFAULT_MEMORY_MAX_BYTES
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_MEMORY_MAX_BYTES must be an integer"
            )
            if (memoryMaxBytes !in 4_096..10 * 1024 * 1024) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_MEMORY_MAX_BYTES must be between 4096 and 10485760"
                )
            }

            val memoryMaxConversations = parseInt(
                environment,
                "IRIS_GLM_MEMORY_MAX_CONVERSATIONS",
                DEFAULT_MEMORY_MAX_CONVERSATIONS
            ) ?: return GlmSettingsLoadResult.Invalid(
                "IRIS_GLM_MEMORY_MAX_CONVERSATIONS must be an integer"
            )
            if (memoryMaxConversations !in 1..10_000) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_GLM_MEMORY_MAX_CONVERSATIONS must be between 1 and 10000"
                )
            }

            val adminUserIdsFile = File(
                environment["IRIS_BOT_ADMIN_USER_IDS_FILE"]
                    ?.trim()
                    ?.takeIf { it.isNotEmpty() }
                    ?: DEFAULT_ADMIN_USER_IDS_FILE
            )
            if (!adminUserIdsFile.isAbsolute) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_BOT_ADMIN_USER_IDS_FILE must be an absolute path"
                )
            }

            val adminControlChatId = environment["IRIS_BOT_ADMIN_CONTROL_CHAT_ID"]
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?.toLongOrNull()
            if (
                "IRIS_BOT_ADMIN_CONTROL_CHAT_ID" in environment &&
                (adminControlChatId == null || adminControlChatId <= 0L || adminControlChatId !in allowedChatIds.filterNotNull().toSet())
            ) {
                return GlmSettingsLoadResult.Invalid(
                    "IRIS_BOT_ADMIN_CONTROL_CHAT_ID must be an allow-listed positive chat ID"
                )
            }

            val generalConversation = parseGeneralConversationSettings(
                environment = environment,
                glmAllowedChatIds = allowedChatIds.filterNotNull().toSet()
            )
            val roomCapabilities = parseRoomCapabilitySettings(environment)

            return GlmSettingsLoadResult.Ready(
                GlmSettings(
                    baseUrl = baseUrl.ensureTrailingSlash(),
                    model = model,
                    fallbackModel = fallbackModel,
                    trigger = trigger,
                    allowedChatIds = allowedChatIds.filterNotNull().toSet(),
                    apiKeyFile = File(apiKeyFilePath),
                    timeoutMillis = timeoutMillis,
                    generalConversationTimeoutMillis = generalConversationTimeoutMillis,
                    maxTokens = maxTokens,
                    temperature = temperature,
                    rateLimitRetries = rateLimitRetries,
                    roomQueueCapacity = roomQueueCapacity,
                    totalQueueCapacity = totalQueueCapacity,
                    maxConcurrency = maxConcurrency,
                    roomRateWindowMillis = roomRateWindowMillis,
                    roomRateMaxRequests = roomRateMaxRequests,
                    userRateWindowMillis = userRateWindowMillis,
                    userRateMaxRequests = userRateMaxRequests,
                    duplicateWindowMillis = duplicateWindowMillis,
                    memoryFile = memoryFile,
                    memoryMaxTurns = memoryMaxTurns,
                    memoryTtlMillis = memoryTtlMillis,
                    memoryMaxBytes = memoryMaxBytes,
                    memoryMaxConversations = memoryMaxConversations,
                    adminUserIdsFile = adminUserIdsFile,
                    adminControlChatId = adminControlChatId,
                    generalConversation = generalConversation,
                    roomCapabilities = roomCapabilities
                )
            )
        }

        private fun parseGeneralConversationSettings(
            environment: Map<String, String>,
            glmAllowedChatIds: Set<Long>
        ): GeneralConversationSettings? {
            val rawAllowed = environment["IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS"]
                ?: return null
            val parts = rawAllowed.split(',').map(String::trim)
            if (parts.isEmpty() || parts.any(String::isBlank)) return null
            val parsed = parts.map { it.toLongOrNull() }
            if (parsed.any { it == null || it <= 0L }) return null
            val allowed = parsed.filterNotNull()
            if (allowed.toSet().size != allowed.size) return null
            if (!glmAllowedChatIds.containsAll(allowed)) return null

            val blockFile = File(
                environment["IRIS_GENERAL_CONVERSATION_BLOCK_FILE"]
                    ?.trim()
                    ?.takeIf(String::isNotEmpty)
                    ?: DEFAULT_GENERAL_CONVERSATION_BLOCK_FILE
            )
            if (!blockFile.isAbsolute) return null

            val modeFile = File(
                environment["IRIS_GENERAL_CONVERSATION_MODE_FILE"]
                    ?.trim()
                    ?.takeIf(String::isNotEmpty)
                    ?: DEFAULT_GENERAL_CONVERSATION_MODE_FILE
            )
            if (!modeFile.isAbsolute) return null

            val circuitWindowMillis = parseLong(
                environment,
                "IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS",
                DEFAULT_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MILLIS
            ) ?: return null
            if (circuitWindowMillis !in 60_000L..3_600_000L) return null

            val circuitFailureThreshold = parseInt(
                environment,
                "IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD",
                DEFAULT_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD
            ) ?: return null
            if (circuitFailureThreshold !in 1..20) return null

            return GeneralConversationSettings(
                allowedChatIds = allowed.toSet(),
                blockFile = blockFile,
                modeFile = modeFile,
                circuitWindowMillis = circuitWindowMillis,
                circuitFailureThreshold = circuitFailureThreshold
            )
        }

        private fun parseRoomCapabilitySettings(
            environment: Map<String, String>
        ): RoomCapabilitySettings? {
            val path = environment["IRIS_BOT_ROOM_POLICY_FILE"]
                ?.trim()
                ?.takeIf(String::isNotEmpty)
                ?: return null
            val file = File(path)
            if (!file.isAbsolute) return null
            return RoomCapabilitySettings(file)
        }

        private fun String.ensureTrailingSlash(): String = if (endsWith('/')) this else "$this/"

        private fun parseInt(
            environment: Map<String, String>,
            name: String,
            default: Int
        ): Int? = environment[name]?.trim()?.toIntOrNull() ?: if (name in environment) null else default

        private fun parseLong(
            environment: Map<String, String>,
            name: String,
            default: Long
        ): Long? = environment[name]?.trim()?.toLongOrNull() ?: if (name in environment) null else default

        private fun parseDouble(
            environment: Map<String, String>,
            name: String,
            default: Double
        ): Double? = environment[name]?.trim()?.toDoubleOrNull() ?: if (name in environment) null else default
    }
}

sealed interface GlmSettingsLoadResult {
    data object Disabled : GlmSettingsLoadResult
    data class Ready(val settings: GlmSettings) : GlmSettingsLoadResult
    data class Invalid(val reason: String) : GlmSettingsLoadResult
}
