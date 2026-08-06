package ai.coreline.heybot

import java.io.File

data class YoutubeDownloadProxySettings(
    val baseUrl: String,
    val routeSecretFile: File,
    val allowedChatIds: Set<Long>,
    val requestTimeoutMillis: Long = DEFAULT_REQUEST_TIMEOUT_MILLIS,
    val pollIntervalMillis: Long = DEFAULT_POLL_INTERVAL_MILLIS,
    val jobTimeoutMillis: Long = DEFAULT_JOB_TIMEOUT_MILLIS,
    val promptMaxChars: Int = DEFAULT_PROMPT_MAX_CHARS,
    val youtubeDownloadMaxBytes: Int = DEFAULT_YOUTUBE_DOWNLOAD_MAX_BYTES,
    val maxPendingPerRoom: Int = DEFAULT_MAX_PENDING_PER_ROOM,
    val roomRateWindowMillis: Long = DEFAULT_RATE_WINDOW_MILLIS,
    val roomRateMaxRequests: Int = DEFAULT_ROOM_RATE_MAX,
    val userRateWindowMillis: Long = DEFAULT_RATE_WINDOW_MILLIS,
    val userRateMaxRequests: Int = DEFAULT_USER_RATE_MAX,
    val stateFile: File = File(DEFAULT_STATE_FILE)
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = routeSecretFile.takeIf { it.isFile }?.readText()?.trim().orEmpty()
        require(token.isNotBlank()) { "YoutubeDownload proxy route secret is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:4364"
        const val DEFAULT_REQUEST_TIMEOUT_MILLIS = 30_000L
        const val DEFAULT_POLL_INTERVAL_MILLIS = 1_000L
        const val DEFAULT_JOB_TIMEOUT_MILLIS = 30 * 60 * 1_000L
        const val DEFAULT_PROMPT_MAX_CHARS = 1_000
        const val DEFAULT_YOUTUBE_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
        const val DEFAULT_MAX_PENDING_PER_ROOM = 1
        const val DEFAULT_RATE_WINDOW_MILLIS = 10 * 60 * 1_000L
        const val DEFAULT_ROOM_RATE_MAX = 1
        const val DEFAULT_USER_RATE_MAX = 1
        const val DEFAULT_STATE_FILE = "/data/local/private/iris-youtube-jobs.json"

        fun load(environment: Map<String, String> = System.getenv()): YoutubeDownloadProxySettingsLoadResult {
            if (!environment["IRIS_YOUTUBE_DOWNLOAD_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                return YoutubeDownloadProxySettingsLoadResult.Disabled
            }
            val baseUrl = environment["IRIS_YOUTUBE_DOWNLOAD_PROXY_BASE_URL"]
                ?.trim()
                ?.trimEnd('/')
                ?.takeIf { it.isNotBlank() }
                ?: DEFAULT_BASE_URL
            val validLoopback = baseUrl.startsWith("http://127.0.0.1:") ||
                baseUrl.startsWith("http://localhost:")
            if (!validLoopback) {
                return YoutubeDownloadProxySettingsLoadResult.Invalid(
                    "IRIS_YOUTUBE_DOWNLOAD_PROXY_BASE_URL must be loopback HTTP"
                )
            }
            val secretPath = environment["IRIS_YOUTUBE_DOWNLOAD_PROXY_SECRET_FILE"]?.trim().orEmpty()
            if (secretPath.isBlank() || !File(secretPath).isAbsolute) {
                return YoutubeDownloadProxySettingsLoadResult.Invalid(
                    "IRIS_YOUTUBE_DOWNLOAD_PROXY_SECRET_FILE must be an absolute path"
                )
            }
            val rawChatIds = (
                environment["IRIS_YOUTUBE_DOWNLOAD_ALLOWED_CHAT_IDS"]
                    ?: environment["IRIS_GLM_ALLOWED_CHAT_IDS"]
                ).orEmpty()
                .split(',')
                .map { it.trim() }
            val chatIds = rawChatIds.map { it.toLongOrNull() }
            if (rawChatIds.isEmpty() || rawChatIds.any { it.isBlank() } ||
                chatIds.any { it == null || it <= 0L }
            ) {
                return YoutubeDownloadProxySettingsLoadResult.Invalid(
                    "IRIS_YOUTUBE_DOWNLOAD_ALLOWED_CHAT_IDS contains an invalid chat ID"
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
                "IRIS_YOUTUBE_DOWNLOAD_PROXY_REQUEST_TIMEOUT_MS",
                DEFAULT_REQUEST_TIMEOUT_MILLIS,
                1_000L..120_000L
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload request timeout")
            val pollInterval = long(
                "IRIS_YOUTUBE_DOWNLOAD_PROXY_POLL_INTERVAL_MS",
                DEFAULT_POLL_INTERVAL_MILLIS,
                250L..30_000L
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload poll interval")
            val jobTimeout = long(
                "IRIS_YOUTUBE_DOWNLOAD_PROXY_JOB_TIMEOUT_MS",
                DEFAULT_JOB_TIMEOUT_MILLIS,
                10_000L..3_600_000L
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload job timeout")
            val promptMax = int(
                "IRIS_YOUTUBE_DOWNLOAD_URL_MAX_CHARS",
                DEFAULT_PROMPT_MAX_CHARS,
                1..4_000
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid YouTube URL maximum")
            val youtubeDownloadMax = int(
                "IRIS_YOUTUBE_DOWNLOAD_MAX_BYTES",
                DEFAULT_YOUTUBE_DOWNLOAD_MAX_BYTES,
                1_024..50 * 1024 * 1024
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload byte maximum")
            val perRoom = int(
                "IRIS_YOUTUBE_DOWNLOAD_MAX_PENDING_PER_ROOM",
                DEFAULT_MAX_PENDING_PER_ROOM,
                1..20
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid room pending maximum")
            val rateWindow = long(
                "IRIS_YOUTUBE_DOWNLOAD_RATE_WINDOW_MS",
                DEFAULT_RATE_WINDOW_MILLIS,
                10_000L..86_400_000L
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload rate window")
            val roomRate = int(
                "IRIS_YOUTUBE_DOWNLOAD_ROOM_RATE_MAX",
                DEFAULT_ROOM_RATE_MAX,
                1..100
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload room rate")
            val userRate = int(
                "IRIS_YOUTUBE_DOWNLOAD_USER_RATE_MAX",
                DEFAULT_USER_RATE_MAX,
                1..100
            ) ?: return YoutubeDownloadProxySettingsLoadResult.Invalid("Invalid youtubeDownload user rate")
            val stateFile = File(
                environment["IRIS_YOUTUBE_DOWNLOAD_STATE_FILE"]?.trim()?.takeIf { it.isNotBlank() }
                    ?: DEFAULT_STATE_FILE
            )
            if (!stateFile.isAbsolute) {
                return YoutubeDownloadProxySettingsLoadResult.Invalid(
                    "IRIS_YOUTUBE_DOWNLOAD_STATE_FILE must be an absolute path"
                )
            }
            return YoutubeDownloadProxySettingsLoadResult.Ready(
                YoutubeDownloadProxySettings(
                    baseUrl = baseUrl,
                    routeSecretFile = File(secretPath),
                    allowedChatIds = chatIds.filterNotNull().toSet(),
                    requestTimeoutMillis = requestTimeout,
                    pollIntervalMillis = pollInterval,
                    jobTimeoutMillis = jobTimeout,
                    promptMaxChars = promptMax,
                    youtubeDownloadMaxBytes = youtubeDownloadMax,
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

sealed interface YoutubeDownloadProxySettingsLoadResult {
    data object Disabled : YoutubeDownloadProxySettingsLoadResult
    data class Ready(val settings: YoutubeDownloadProxySettings) : YoutubeDownloadProxySettingsLoadResult
    data class Invalid(val reason: String) : YoutubeDownloadProxySettingsLoadResult
}
