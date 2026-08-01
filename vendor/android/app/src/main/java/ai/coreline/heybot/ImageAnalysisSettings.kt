package ai.coreline.heybot

import java.io.File

data class ImageAnalysisSettings(
    val baseUrl: String,
    val routeSecretFile: File,
    val allowedChatIds: Set<Long>,
    val requestTimeoutMillis: Long = 30_000L,
    val pollIntervalMillis: Long = 1_000L,
    val jobTimeoutMillis: Long = 120_000L,
    val recentImageWindowMillis: Long = 30 * 60 * 1_000L,
    val maxPendingPerRoom: Int = 1,
    val roomRateWindowMillis: Long = 10 * 60 * 1_000L,
    val roomRateMaxRequests: Int = 3,
    val userRateWindowMillis: Long = 10 * 60 * 1_000L,
    val userRateMaxRequests: Int = 2
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = routeSecretFile.takeIf { it.isFile }?.readText()?.trim().orEmpty()
        require(token.isNotBlank()) { "Vision proxy route secret is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:4340"

        fun load(environment: Map<String, String> = System.getenv()): ImageAnalysisSettingsLoadResult {
            if (!environment["IRIS_VISION_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                return ImageAnalysisSettingsLoadResult.Disabled
            }
            val baseUrl = environment["IRIS_VISION_PROXY_BASE_URL"]
                ?.trim()?.trimEnd('/')?.takeIf(String::isNotBlank) ?: DEFAULT_BASE_URL
            if (!(baseUrl.startsWith("http://127.0.0.1:") || baseUrl.startsWith("http://localhost:"))) {
                return ImageAnalysisSettingsLoadResult.Invalid("IRIS_VISION_PROXY_BASE_URL must be loopback HTTP")
            }
            val secretPath = environment["IRIS_VISION_PROXY_SECRET_FILE"].orEmpty().trim()
            if (secretPath.isBlank() || !File(secretPath).isAbsolute) {
                return ImageAnalysisSettingsLoadResult.Invalid("IRIS_VISION_PROXY_SECRET_FILE must be absolute")
            }
            val rawIds = (environment["IRIS_VISION_ALLOWED_CHAT_IDS"]
                ?: environment["IRIS_GLM_ALLOWED_CHAT_IDS"]).orEmpty()
                .split(',').map(String::trim)
            val ids = rawIds.map(String::toLongOrNull)
            if (rawIds.any(String::isBlank) || ids.any { it == null || it <= 0L }) {
                return ImageAnalysisSettingsLoadResult.Invalid("IRIS_VISION_ALLOWED_CHAT_IDS is invalid")
            }
            fun long(name: String, fallback: Long, range: LongRange): Long? {
                val parsed = environment[name]?.trim()?.toLongOrNull() ?: fallback
                return parsed.takeIf { it in range }
            }
            fun int(name: String, fallback: Int, range: IntRange): Int? {
                val parsed = environment[name]?.trim()?.toIntOrNull() ?: fallback
                return parsed.takeIf { it in range }
            }
            return ImageAnalysisSettingsLoadResult.Ready(
                ImageAnalysisSettings(
                    baseUrl = baseUrl,
                    routeSecretFile = File(secretPath),
                    allowedChatIds = ids.filterNotNull().toSet(),
                    requestTimeoutMillis = long(
                        "IRIS_VISION_PROXY_REQUEST_TIMEOUT_MS", 30_000L, 1_000L..120_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision request timeout"),
                    pollIntervalMillis = long(
                        "IRIS_VISION_PROXY_POLL_INTERVAL_MS", 1_000L, 250L..30_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision poll interval"),
                    jobTimeoutMillis = long(
                        "IRIS_VISION_PROXY_JOB_TIMEOUT_MS", 120_000L, 10_000L..600_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision job timeout"),
                    recentImageWindowMillis = long(
                        "IRIS_VISION_RECENT_IMAGE_WINDOW_MS",
                        30 * 60 * 1_000L,
                        30_000L..86_400_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision recent image window"),
                    maxPendingPerRoom = int(
                        "IRIS_VISION_MAX_PENDING_PER_ROOM", 1, 1..10
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision pending maximum"),
                    roomRateWindowMillis = long(
                        "IRIS_VISION_RATE_WINDOW_MS", 10 * 60 * 1_000L, 10_000L..86_400_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision rate window"),
                    roomRateMaxRequests = int(
                        "IRIS_VISION_ROOM_RATE_MAX", 3, 1..100
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision room rate"),
                    userRateWindowMillis = long(
                        "IRIS_VISION_RATE_WINDOW_MS", 10 * 60 * 1_000L, 10_000L..86_400_000L
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision rate window"),
                    userRateMaxRequests = int(
                        "IRIS_VISION_USER_RATE_MAX", 2, 1..100
                    ) ?: return ImageAnalysisSettingsLoadResult.Invalid("Invalid vision user rate")
                )
            )
        }
    }
}

sealed interface ImageAnalysisSettingsLoadResult {
    data object Disabled : ImageAnalysisSettingsLoadResult
    data class Ready(val settings: ImageAnalysisSettings) : ImageAnalysisSettingsLoadResult
    data class Invalid(val reason: String) : ImageAnalysisSettingsLoadResult
}
