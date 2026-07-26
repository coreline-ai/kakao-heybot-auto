package ai.coreline.heybot

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.security.MessageDigest

/**
 * HTTP management is intentionally opt-in. The bot keeps its Kakao/GLM work
 * running when this configuration is absent or invalid; only the management
 * server remains unavailable.
 */
class IrisHttpSecuritySettings private constructor(
    val host: String,
    private val adminSecret: ByteArray
) {
    internal fun authenticator(): IrisHttpAuthenticator = IrisHttpAuthenticator(adminSecret)

    companion object {
        private const val ENABLED = "IRIS_HTTP_API_ENABLED"
        private const val ADMIN_SECRET_FILE = "IRIS_HTTP_ADMIN_SECRET_FILE"
        private const val MAX_SECRET_FILE_BYTES = 1_024L
        private const val MIN_SECRET_CHARS = 24
        private const val MAX_SECRET_CHARS = 512

        fun load(
            environment: Map<String, String> = System.getenv()
        ): IrisHttpSecuritySettingsLoadResult {
            val enabled = environment[ENABLED]?.trim()?.lowercase() ?: "false"
            if (enabled == "false") return IrisHttpSecuritySettingsLoadResult.Disabled
            if (enabled != "true") {
                return IrisHttpSecuritySettingsLoadResult.Invalid("HTTP_API_ENABLED_INVALID")
            }

            val path = environment[ADMIN_SECRET_FILE]?.trim().orEmpty()
            if (path.isBlank()) {
                return IrisHttpSecuritySettingsLoadResult.Invalid("HTTP_ADMIN_SECRET_FILE_MISSING")
            }
            val file = File(path)
            val isSymbolicLink = runCatching { Files.isSymbolicLink(file.toPath()) }.getOrDefault(true)
            if (
                !file.isAbsolute ||
                isSymbolicLink ||
                !file.isFile ||
                file.length() !in 1L..MAX_SECRET_FILE_BYTES
            ) {
                return IrisHttpSecuritySettingsLoadResult.Invalid("HTTP_ADMIN_SECRET_FILE_INVALID")
            }

            val secret = runCatching { file.readText().trim() }.getOrNull()
                ?: return IrisHttpSecuritySettingsLoadResult.Invalid("HTTP_ADMIN_SECRET_FILE_UNREADABLE")
            if (secret.length !in MIN_SECRET_CHARS..MAX_SECRET_CHARS) {
                return IrisHttpSecuritySettingsLoadResult.Invalid("HTTP_ADMIN_SECRET_INVALID")
            }

            return IrisHttpSecuritySettingsLoadResult.Ready(
                IrisHttpSecuritySettings(
                    host = LOOPBACK_HOST,
                    adminSecret = secret.toByteArray(StandardCharsets.UTF_8)
                )
            )
        }

        private const val LOOPBACK_HOST = "127.0.0.1"
    }
}

sealed interface IrisHttpSecuritySettingsLoadResult {
    data object Disabled : IrisHttpSecuritySettingsLoadResult
    data class Ready(val settings: IrisHttpSecuritySettings) : IrisHttpSecuritySettingsLoadResult
    /** A stable code only. Never include a secret value or filesystem path. */
    data class Invalid(val code: String) : IrisHttpSecuritySettingsLoadResult
}

class IrisHttpAuthenticator internal constructor(
    private val expectedSecret: ByteArray
) {
    fun isAuthorized(header: String?): Boolean {
        if (header == null || header.length > MAX_AUTHORIZATION_HEADER_CHARS) return false
        if (!header.startsWith(BEARER_PREFIX)) return false
        val supplied = header.removePrefix(BEARER_PREFIX).toByteArray(StandardCharsets.UTF_8)
        return supplied.size == expectedSecret.size && MessageDigest.isEqual(supplied, expectedSecret)
    }

    private companion object {
        const val BEARER_PREFIX = "Bearer "
        const val MAX_AUTHORIZATION_HEADER_CHARS = 1_024
    }
}
