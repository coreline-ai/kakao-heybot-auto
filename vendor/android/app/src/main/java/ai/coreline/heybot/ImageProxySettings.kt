package ai.coreline.heybot

import java.io.File

data class ImageProxySettings(
    val baseUrl: String,
    val routeSecretFile: File,
    val allowedChatIds: Set<Long>,
    val requestTimeoutMillis: Long = DEFAULT_REQUEST_TIMEOUT_MILLIS,
    val pollIntervalMillis: Long = DEFAULT_POLL_INTERVAL_MILLIS,
    val jobTimeoutMillis: Long = DEFAULT_JOB_TIMEOUT_MILLIS,
    val promptMaxChars: Int = DEFAULT_PROMPT_MAX_CHARS,
    val imageMaxBytes: Int = DEFAULT_IMAGE_MAX_BYTES,
    val deliveryConfirmTimeoutMillis: Long = DEFAULT_DELIVERY_CONFIRM_TIMEOUT_MILLIS,
    val maxPendingPerRoom: Int = DEFAULT_MAX_PENDING_PER_ROOM,
    val roomRateWindowMillis: Long = DEFAULT_RATE_WINDOW_MILLIS,
    val roomRateMaxRequests: Int = DEFAULT_ROOM_RATE_MAX,
    val userRateWindowMillis: Long = DEFAULT_RATE_WINDOW_MILLIS,
    val userRateMaxRequests: Int = DEFAULT_USER_RATE_MAX,
    val stateFile: File = File(DEFAULT_STATE_FILE)
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = routeSecretFile.takeIf { it.isFile }?.readText()?.trim().orEmpty()
        require(token.isNotBlank()) { "Image proxy route secret is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:4340"
        const val DEFAULT_REQUEST_TIMEOUT_MILLIS = 30_000L
        const val DEFAULT_POLL_INTERVAL_MILLIS = 1_000L
        const val DEFAULT_JOB_TIMEOUT_MILLIS = 30 * 60 * 1_000L
        const val DEFAULT_PROMPT_MAX_CHARS = 1_000
        const val DEFAULT_IMAGE_MAX_BYTES = 12 * 1024 * 1024
        const val DEFAULT_DELIVERY_CONFIRM_TIMEOUT_MILLIS = 45_000L
        const val DEFAULT_MAX_PENDING_PER_ROOM = 3
        const val DEFAULT_RATE_WINDOW_MILLIS = 10 * 60 * 1_000L
        const val DEFAULT_ROOM_RATE_MAX = 3
        const val DEFAULT_USER_RATE_MAX = 2
        const val DEFAULT_STATE_FILE = "/data/local/private/iris-image-jobs.json"

        fun load(environment: Map<String, String> = System.getenv()): ImageProxySettingsLoadResult {
            if (!environment["IRIS_IMAGE_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                return ImageProxySettingsLoadResult.Disabled
            }
            val baseUrl = environment["IRIS_IMAGE_PROXY_BASE_URL"]
                ?.trim()
                ?.trimEnd('/')
                ?.takeIf { it.isNotBlank() }
                ?: DEFAULT_BASE_URL
            val validLoopback = baseUrl.startsWith("http://127.0.0.1:") ||
                baseUrl.startsWith("http://localhost:")
            if (!validLoopback) {
                return ImageProxySettingsLoadResult.Invalid(
                    "IRIS_IMAGE_PROXY_BASE_URL must be loopback HTTP"
                )
            }
            val secretPath = environment["IRIS_IMAGE_PROXY_SECRET_FILE"]?.trim().orEmpty()
            if (secretPath.isBlank() || !File(secretPath).isAbsolute) {
                return ImageProxySettingsLoadResult.Invalid(
                    "IRIS_IMAGE_PROXY_SECRET_FILE must be an absolute path"
                )
            }
            val rawChatIds = (
                environment["IRIS_IMAGE_ALLOWED_CHAT_IDS"]
                    ?: environment["IRIS_GLM_ALLOWED_CHAT_IDS"]
                ).orEmpty()
                .split(',')
                .map { it.trim() }
            val chatIds = rawChatIds.map { it.toLongOrNull() }
            if (rawChatIds.isEmpty() || rawChatIds.any { it.isBlank() } ||
                chatIds.any { it == null || it <= 0L }
            ) {
                return ImageProxySettingsLoadResult.Invalid(
                    "IRIS_IMAGE_ALLOWED_CHAT_IDS contains an invalid chat ID"
                )
            }

            fun int(name: String, default: Int, range: IntRange): Int? {
                val raw = environment[name]
                val parsed = if (raw == null) default else raw.trim().toIntOrNull() ?: return null
                return parsed.takeIf { it in range }
            }

            fun long(name: String, default: Long, range: LongRange): Long? {
                val raw = environment[name]
                val parsed = if (raw == null) default else raw.trim().toLongOrNull() ?: return null
                return parsed.takeIf { it in range }
            }

            val requestTimeout = long(
                "IRIS_IMAGE_PROXY_REQUEST_TIMEOUT_MS",
                DEFAULT_REQUEST_TIMEOUT_MILLIS,
                1_000L..120_000L
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image request timeout")
            val pollInterval = long(
                "IRIS_IMAGE_PROXY_POLL_INTERVAL_MS",
                DEFAULT_POLL_INTERVAL_MILLIS,
                250L..30_000L
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image poll interval")
            val jobTimeout = long(
                "IRIS_IMAGE_PROXY_JOB_TIMEOUT_MS",
                DEFAULT_JOB_TIMEOUT_MILLIS,
                10_000L..3_600_000L
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image job timeout")
            val promptMax = int(
                "IRIS_IMAGE_PROMPT_MAX_CHARS",
                DEFAULT_PROMPT_MAX_CHARS,
                1..4_000
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image prompt maximum")
            val imageMax = int(
                "IRIS_IMAGE_MAX_BYTES",
                DEFAULT_IMAGE_MAX_BYTES,
                1_024..50 * 1024 * 1024
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image byte maximum")
            val deliveryConfirmTimeout = long(
                "IRIS_IMAGE_DELIVERY_CONFIRM_TIMEOUT_MS",
                DEFAULT_DELIVERY_CONFIRM_TIMEOUT_MILLIS,
                5_000L..300_000L
            ) ?: return ImageProxySettingsLoadResult.Invalid(
                "Invalid image delivery confirmation timeout"
            )
            val perRoom = int(
                "IRIS_IMAGE_MAX_PENDING_PER_ROOM",
                DEFAULT_MAX_PENDING_PER_ROOM,
                1..20
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid room pending maximum")
            val rateWindow = long(
                "IRIS_IMAGE_RATE_WINDOW_MS",
                DEFAULT_RATE_WINDOW_MILLIS,
                10_000L..86_400_000L
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image rate window")
            val roomRate = int(
                "IRIS_IMAGE_ROOM_RATE_MAX",
                DEFAULT_ROOM_RATE_MAX,
                1..100
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image room rate")
            val userRate = int(
                "IRIS_IMAGE_USER_RATE_MAX",
                DEFAULT_USER_RATE_MAX,
                1..100
            ) ?: return ImageProxySettingsLoadResult.Invalid("Invalid image user rate")
            val stateFile = File(
                environment["IRIS_IMAGE_STATE_FILE"]?.trim()?.takeIf { it.isNotBlank() }
                    ?: DEFAULT_STATE_FILE
            )
            if (!stateFile.isAbsolute) {
                return ImageProxySettingsLoadResult.Invalid(
                    "IRIS_IMAGE_STATE_FILE must be an absolute path"
                )
            }
            return ImageProxySettingsLoadResult.Ready(
                ImageProxySettings(
                    baseUrl = baseUrl,
                    routeSecretFile = File(secretPath),
                    allowedChatIds = chatIds.filterNotNull().toSet(),
                    requestTimeoutMillis = requestTimeout,
                    pollIntervalMillis = pollInterval,
                    jobTimeoutMillis = jobTimeout,
                    promptMaxChars = promptMax,
                    imageMaxBytes = imageMax,
                    deliveryConfirmTimeoutMillis = deliveryConfirmTimeout,
                    maxPendingPerRoom = perRoom,
                    roomRateWindowMillis = rateWindow,
                    roomRateMaxRequests = roomRate,
                    userRateWindowMillis = rateWindow,
                    userRateMaxRequests = userRate,
                    stateFile = stateFile
                )
            )
        }
    }
}

sealed interface ImageProxySettingsLoadResult {
    data object Disabled : ImageProxySettingsLoadResult
    data class Ready(val settings: ImageProxySettings) : ImageProxySettingsLoadResult
    data class Invalid(val reason: String) : ImageProxySettingsLoadResult
}
