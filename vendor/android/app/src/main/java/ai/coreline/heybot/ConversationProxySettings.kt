package ai.coreline.heybot

import java.io.File

data class ConversationProxySettings(
    val baseUrl: String,
    val routeSecretFile: File,
    val requestTimeoutMillis: Long,
    val modeFile: File
) {
    fun authorizationHeader(): Result<String> = runCatching {
        val token = routeSecretFile.takeIf { it.isFile }?.readText()?.trim().orEmpty()
        require(token.isNotBlank()) { "Conversation proxy route secret is unavailable" }
        if (token.startsWith("Bearer ", ignoreCase = true)) token else "Bearer $token"
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:4340"
        const val DEFAULT_MODE_FILE = "/data/local/private/iris-conversation-engine.conf"

        fun load(environment: Map<String, String> = System.getenv()): ConversationProxySettingsLoadResult {
            if (!environment["IRIS_CONVERSATION_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                return ConversationProxySettingsLoadResult.Disabled
            }
            val baseUrl = environment["IRIS_CONVERSATION_PROXY_BASE_URL"]?.trim()?.trimEnd('/')
                ?.takeIf { it.isNotBlank() } ?: DEFAULT_BASE_URL
            if (!(baseUrl.startsWith("http://127.0.0.1:") || baseUrl.startsWith("http://localhost:"))) {
                return ConversationProxySettingsLoadResult.Invalid("IRIS_CONVERSATION_PROXY_BASE_URL must be loopback HTTP")
            }
            val secret = environment["IRIS_CONVERSATION_PROXY_SECRET_FILE"]?.trim().orEmpty()
            if (secret.isBlank() || !File(secret).isAbsolute) {
                return ConversationProxySettingsLoadResult.Invalid("IRIS_CONVERSATION_PROXY_SECRET_FILE must be absolute")
            }
            val timeout = environment["IRIS_CONVERSATION_PROXY_TIMEOUT_MS"]?.trim()?.toLongOrNull() ?: 100_000L
            if (timeout !in 1_000L..300_000L) return ConversationProxySettingsLoadResult.Invalid("Invalid conversation proxy timeout")
            val modeFile = File(environment["IRIS_CONVERSATION_ENGINE_FILE"]?.trim()?.takeIf { it.isNotBlank() } ?: DEFAULT_MODE_FILE)
            if (!modeFile.isAbsolute) return ConversationProxySettingsLoadResult.Invalid("IRIS_CONVERSATION_ENGINE_FILE must be absolute")
            return ConversationProxySettingsLoadResult.Ready(
                ConversationProxySettings(baseUrl, File(secret), timeout, modeFile)
            )
        }
    }
}

sealed interface ConversationProxySettingsLoadResult {
    data object Disabled : ConversationProxySettingsLoadResult
    data class Ready(val settings: ConversationProxySettings) : ConversationProxySettingsLoadResult
    data class Invalid(val reason: String) : ConversationProxySettingsLoadResult
}
