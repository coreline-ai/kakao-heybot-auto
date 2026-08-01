package ai.coreline.heybot

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.net.URI

data class IncomingImageAttachment(
    val sourceLogId: Long,
    val chatId: Long,
    val userId: Long,
    val url: String,
    val thumbnailUrl: String?,
    val width: Int,
    val height: Int,
    val declaredBytes: Long,
    val expiresAtMillis: Long,
    val mediaType: String?
)

enum class ImageAttachmentParseFailure {
    UNSUPPORTED_MESSAGE_TYPE,
    INVALID_JSON,
    MISSING_FIELD,
    INVALID_NUMBER,
    INVALID_URL,
    FORBIDDEN_HOST,
    EXPIRED
}

sealed interface ImageAttachmentParseResult {
    data class Parsed(val attachment: IncomingImageAttachment) : ImageAttachmentParseResult
    data class Rejected(val reason: ImageAttachmentParseFailure) : ImageAttachmentParseResult
}

/** Converts decrypted Kakao attachment JSON into a fail-closed, log-safe model. */
class KakaoImageAttachmentParser(
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    fun parse(
        sourceLogId: Long,
        chatId: Long,
        userId: Long,
        messageType: String,
        decryptedAttachment: String?
    ): ImageAttachmentParseResult {
        if (messageType !in IMAGE_MESSAGE_TYPES) {
            return ImageAttachmentParseResult.Rejected(
                ImageAttachmentParseFailure.UNSUPPORTED_MESSAGE_TYPE
            )
        }
        val json = try {
            JSON.parseToJsonElement(decryptedAttachment.orEmpty()) as JsonObject
        } catch (_: Exception) {
            return ImageAttachmentParseResult.Rejected(ImageAttachmentParseFailure.INVALID_JSON)
        }
        val url = json.string("url").orEmpty().trim()
        val width = json.strictPositiveInt("w")
        val height = json.strictPositiveInt("h")
        val bytes = json.strictPositiveLong("s")
        val rawExpiry = json.strictPositiveLong("expire")
        if (url.isBlank() || width == null || height == null || bytes == null || rawExpiry == null) {
            return ImageAttachmentParseResult.Rejected(ImageAttachmentParseFailure.MISSING_FIELD)
        }
        if (width !in 1..MAX_DIMENSION || height !in 1..MAX_DIMENSION || bytes > MAX_BYTES) {
            return ImageAttachmentParseResult.Rejected(ImageAttachmentParseFailure.INVALID_NUMBER)
        }
        val expiresAtMillis = if (rawExpiry < MILLIS_THRESHOLD) rawExpiry * 1_000L else rawExpiry
        if (expiresAtMillis <= nowMillis()) {
            return ImageAttachmentParseResult.Rejected(ImageAttachmentParseFailure.EXPIRED)
        }
        if (!isAllowedUrl(url)) {
            val reason = runCatching { URI(url).host }
                .getOrNull()
                ?.let { ImageAttachmentParseFailure.FORBIDDEN_HOST }
                ?: ImageAttachmentParseFailure.INVALID_URL
            return ImageAttachmentParseResult.Rejected(reason)
        }
        val thumbnail = json.string("thumbnailUrl").orEmpty().trim().takeIf(::isAllowedUrl)
        val mediaType = json.string("mt").orEmpty().trim().takeIf { it.length in 1..80 }
        return ImageAttachmentParseResult.Parsed(
            IncomingImageAttachment(
                sourceLogId = sourceLogId,
                chatId = chatId,
                userId = userId,
                url = url,
                thumbnailUrl = thumbnail,
                width = width,
                height = height,
                declaredBytes = bytes,
                expiresAtMillis = expiresAtMillis,
                mediaType = mediaType
            )
        )
    }

    private fun isAllowedUrl(raw: String): Boolean {
        if (raw.length !in 1..MAX_URL_CHARS) return false
        return runCatching {
            val uri = URI(raw)
            uri.scheme.equals("https", ignoreCase = true) &&
                uri.host.equals(ALLOWED_HOST, ignoreCase = true) &&
                uri.userInfo == null &&
                (uri.port == -1 || uri.port == 443) &&
                !uri.rawPath.isNullOrBlank()
        }.getOrDefault(false)
    }

    private fun JsonObject.string(key: String): String? =
        get(key)?.jsonPrimitive?.contentOrNull

    private fun JsonObject.strictPositiveInt(key: String): Int? =
        strictPositiveLong(key)?.takeIf { it <= Int.MAX_VALUE }?.toInt()

    private fun JsonObject.strictPositiveLong(key: String): Long? =
        get(key)?.jsonPrimitive?.contentOrNull
            ?.takeIf { it.matches(DECIMAL) }
            ?.toLongOrNull()
            ?.takeIf { it > 0L }

    private companion object {
        const val ALLOWED_HOST = "talk.kakaocdn.net"
        const val MAX_DIMENSION = 16_384
        const val MAX_BYTES = 10L * 1024L * 1024L
        const val MAX_URL_CHARS = 4_096
        const val MILLIS_THRESHOLD = 100_000_000_000L
        val IMAGE_MESSAGE_TYPES = setOf("2", "3")
        val DECIMAL = Regex("[1-9][0-9]{0,18}")
        val JSON = Json { ignoreUnknownKeys = false }
    }
}
