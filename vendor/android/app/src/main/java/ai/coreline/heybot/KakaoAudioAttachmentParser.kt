package ai.coreline.heybot

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.net.URI

data class IncomingAudioAttachment(
    val sourceLogId: Long,
    val chatId: Long,
    val userId: Long,
    val sourceUrl: String,
    val declaredBytes: Long,
    val expiresAtMillis: Long,
    val declaredExtension: String
)

enum class AudioAttachmentParseFailure {
    UNSUPPORTED_MESSAGE_TYPE,
    INVALID_JSON,
    UNSUPPORTED_FIELD,
    MISSING_FIELD,
    INVALID_NUMBER,
    UNSUPPORTED_EXTENSION,
    INVALID_URL,
    FORBIDDEN_HOST,
    EXPIRED
}

sealed interface AudioAttachmentParseResult {
    data class Parsed(val attachment: IncomingAudioAttachment) : AudioAttachmentParseResult
    data class Rejected(val reason: AudioAttachmentParseFailure) : AudioAttachmentParseResult
}

/** Strictly reduces a generic Kakao file attachment to an audio source reference. */
class KakaoAudioAttachmentParser(
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    fun parse(
        sourceLogId: Long,
        chatId: Long,
        userId: Long,
        messageType: String,
        decryptedAttachment: String?
    ): AudioAttachmentParseResult {
        if (messageType != FILE_MESSAGE_TYPE) {
            return rejected(AudioAttachmentParseFailure.UNSUPPORTED_MESSAGE_TYPE)
        }
        val value = try {
            JSON.parseToJsonElement(decryptedAttachment.orEmpty()) as JsonObject
        } catch (_: Exception) {
            return rejected(AudioAttachmentParseFailure.INVALID_JSON)
        }
        if (value.keys.any { it !in ALLOWED_KEYS }) {
            return rejected(AudioAttachmentParseFailure.UNSUPPORTED_FIELD)
        }
        val rawUrl = value.string("url").orEmpty().trim()
        val rawName = value.string("name").orEmpty().trim()
            .ifEmpty { value.string("k").orEmpty().trim() }
        val declaredSizes = sequenceOf("size", "s")
            .mapNotNull { key -> value.strictPositiveLong(key) }
            .toList()
        if (declaredSizes.distinct().size > 1) {
            return rejected(AudioAttachmentParseFailure.INVALID_NUMBER)
        }
        val bytes = declaredSizes.firstOrNull()
        val rawExpiry = value.strictPositiveLong("expire")
        if (rawUrl.isBlank() || rawName.isBlank() || bytes == null || rawExpiry == null) {
            return rejected(AudioAttachmentParseFailure.MISSING_FIELD)
        }
        if (bytes !in 1..MAX_BYTES || rawName.length > MAX_NAME_CHARS ||
            rawName.any { it == '/' || it == '\\' || it == '\u0000' }
        ) {
            return rejected(AudioAttachmentParseFailure.INVALID_NUMBER)
        }
        val extension = rawName.substringAfterLast('.', "").lowercase()
        if (extension !in SUPPORTED_EXTENSIONS) {
            return rejected(AudioAttachmentParseFailure.UNSUPPORTED_EXTENSION)
        }
        val expiresAtMillis = if (rawExpiry < MILLIS_THRESHOLD) {
            runCatching { Math.multiplyExact(rawExpiry, 1_000L) }.getOrNull()
                ?: return rejected(AudioAttachmentParseFailure.INVALID_NUMBER)
        } else rawExpiry
        if (expiresAtMillis <= nowMillis()) {
            return rejected(AudioAttachmentParseFailure.EXPIRED)
        }
        val uri = runCatching { URI(rawUrl) }.getOrNull()
            ?: return rejected(AudioAttachmentParseFailure.INVALID_URL)
        if (
            !uri.scheme.equals("https", ignoreCase = true) ||
            uri.userInfo != null ||
            (uri.port != -1 && uri.port != 443) ||
            uri.rawPath.isNullOrBlank()
        ) {
            return rejected(AudioAttachmentParseFailure.INVALID_URL)
        }
        if (!uri.host.equals(ALLOWED_HOST, ignoreCase = true)) {
            return rejected(AudioAttachmentParseFailure.FORBIDDEN_HOST)
        }
        return AudioAttachmentParseResult.Parsed(
            IncomingAudioAttachment(
                sourceLogId = sourceLogId,
                chatId = chatId,
                userId = userId,
                sourceUrl = rawUrl,
                declaredBytes = bytes,
                expiresAtMillis = expiresAtMillis,
                declaredExtension = extension
            )
        )
    }

    private fun rejected(reason: AudioAttachmentParseFailure) =
        AudioAttachmentParseResult.Rejected(reason)

    private fun JsonObject.string(key: String): String? =
        get(key)?.jsonPrimitive?.contentOrNull

    private fun JsonObject.strictPositiveLong(key: String): Long? =
        get(key)?.jsonPrimitive?.contentOrNull
            ?.takeIf { it.matches(DECIMAL) }
            ?.toLongOrNull()
            ?.takeIf { it > 0L }

    companion object {
        const val FILE_MESSAGE_TYPE = "18"
        const val ALLOWED_HOST = "talk.kakaocdn.net"
        const val MAX_BYTES = 100L * 1024L * 1024L
        private const val MAX_NAME_CHARS = 255
        private const val MILLIS_THRESHOLD = 100_000_000_000L
        val SUPPORTED_EXTENSIONS = setOf("mp3", "m4a", "wav")
        private val ALLOWED_KEYS = setOf("cs", "expire", "k", "name", "s", "size", "url")
        private val DECIMAL = Regex("[1-9][0-9]{0,18}")
        private val JSON = Json { ignoreUnknownKeys = false }
    }
}
