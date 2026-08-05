package ai.coreline.heybot

import java.io.File

data class AudioAnalysisSettings(
    val baseUrl: String,
    val routeSecretFile: File,
    val allowedChatIds: Set<Long>,
    val stateFile: File,
    val contextFile: File = File(DEFAULT_CONTEXT_FILE),
    val requestTimeoutMillis: Long = 30_000L,
    val pollIntervalMillis: Long = 1_000L,
    val jobTimeoutMillis: Long = 30 * 60_000L,
    val recentAudioWindowMillis: Long = 30 * 60_000L,
    val maxPendingPerRoom: Int = 1,
    val rateWindowMillis: Long = 10 * 60_000L,
    val roomRateMaxRequests: Int = 3,
    val userRateMaxRequests: Int = 2
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = routeSecretFile.takeIf { it.isFile }?.readText()?.trim().orEmpty()
        require(token.isNotBlank()) { "Audio proxy route secret is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:4340"
        const val DEFAULT_STATE_FILE = "/data/local/private/iris-audio-jobs.json"
        const val DEFAULT_CONTEXT_FILE = "/data/local/private/iris-audio-context.json"

        fun load(environment: Map<String, String> = System.getenv()): AudioAnalysisSettingsLoadResult {
            if (!environment["IRIS_AUDIO_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                return AudioAnalysisSettingsLoadResult.Disabled
            }
            val baseUrl = environment["IRIS_AUDIO_PROXY_BASE_URL"]?.trim()?.trimEnd('/')
                ?.takeIf(String::isNotBlank) ?: DEFAULT_BASE_URL
            if (!(baseUrl.startsWith("http://127.0.0.1:") || baseUrl.startsWith("http://localhost:"))) {
                return AudioAnalysisSettingsLoadResult.Invalid("IRIS_AUDIO_PROXY_BASE_URL must be loopback HTTP")
            }
            val secretPath = environment["IRIS_AUDIO_PROXY_SECRET_FILE"].orEmpty().trim()
            if (secretPath.isBlank() || !File(secretPath).isAbsolute) {
                return AudioAnalysisSettingsLoadResult.Invalid("IRIS_AUDIO_PROXY_SECRET_FILE must be absolute")
            }
            val stateFile = File(
                environment["IRIS_AUDIO_STATE_FILE"]?.trim()?.takeIf(String::isNotBlank)
                    ?: DEFAULT_STATE_FILE
            )
            if (!stateFile.isAbsolute) {
                return AudioAnalysisSettingsLoadResult.Invalid("IRIS_AUDIO_STATE_FILE must be absolute")
            }
            val contextFile = File(
                environment["IRIS_AUDIO_CONTEXT_FILE"]?.trim()?.takeIf(String::isNotBlank)
                    ?: DEFAULT_CONTEXT_FILE
            )
            if (!contextFile.isAbsolute) {
                return AudioAnalysisSettingsLoadResult.Invalid("IRIS_AUDIO_CONTEXT_FILE must be absolute")
            }
            val rawIds = (environment["IRIS_AUDIO_ALLOWED_CHAT_IDS"]
                ?: environment["IRIS_GLM_ALLOWED_CHAT_IDS"]).orEmpty()
                .split(',').map(String::trim)
            val ids = rawIds.map(String::toLongOrNull)
            if (rawIds.any(String::isBlank) || ids.any { it == null || it <= 0L }) {
                return AudioAnalysisSettingsLoadResult.Invalid("IRIS_AUDIO_ALLOWED_CHAT_IDS is invalid")
            }
            fun long(name: String, fallback: Long, range: LongRange): Long? {
                val value = environment[name]?.trim()?.toLongOrNull() ?: fallback
                return value.takeIf { it in range }
            }
            fun int(name: String, fallback: Int, range: IntRange): Int? {
                val value = environment[name]?.trim()?.toIntOrNull() ?: fallback
                return value.takeIf { it in range }
            }
            return AudioAnalysisSettingsLoadResult.Ready(
                AudioAnalysisSettings(
                    baseUrl = baseUrl,
                    routeSecretFile = File(secretPath),
                    allowedChatIds = ids.filterNotNull().toSet(),
                    stateFile = stateFile,
                    contextFile = contextFile,
                    requestTimeoutMillis = long(
                        "IRIS_AUDIO_PROXY_REQUEST_TIMEOUT_MS", 30_000L, 1_000L..120_000L
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio request timeout"),
                    pollIntervalMillis = long(
                        "IRIS_AUDIO_PROXY_POLL_INTERVAL_MS", 1_000L, 250L..30_000L
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio poll interval"),
                    jobTimeoutMillis = long(
                        "IRIS_AUDIO_PROXY_JOB_TIMEOUT_MS", 30 * 60_000L, 10_000L..3_600_000L
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio job timeout"),
                    recentAudioWindowMillis = long(
                        "IRIS_AUDIO_RECENT_WINDOW_MS", 30 * 60_000L, 30_000L..86_400_000L
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio recent window"),
                    maxPendingPerRoom = int(
                        "IRIS_AUDIO_MAX_PENDING_PER_ROOM", 1, 1..8
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio pending maximum"),
                    rateWindowMillis = long(
                        "IRIS_AUDIO_RATE_WINDOW_MS", 10 * 60_000L, 10_000L..86_400_000L
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio rate window"),
                    roomRateMaxRequests = int(
                        "IRIS_AUDIO_ROOM_RATE_MAX", 3, 1..100
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio room rate"),
                    userRateMaxRequests = int(
                        "IRIS_AUDIO_USER_RATE_MAX", 2, 1..100
                    ) ?: return AudioAnalysisSettingsLoadResult.Invalid("Invalid audio user rate")
                )
            )
        }
    }
}

sealed interface AudioAnalysisSettingsLoadResult {
    data object Disabled : AudioAnalysisSettingsLoadResult
    data class Ready(val settings: AudioAnalysisSettings) : AudioAnalysisSettingsLoadResult
    data class Invalid(val reason: String) : AudioAnalysisSettingsLoadResult
}
