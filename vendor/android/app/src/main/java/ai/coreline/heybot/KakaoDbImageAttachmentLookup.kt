package ai.coreline.heybot

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

interface ImageAttachmentLookup {
    fun findExact(chatId: Long, sourceLogId: Long): IncomingImageAttachment?
    fun findLatestInRoom(chatId: Long, notBeforeMillis: Long): IncomingImageAttachment?
}

object EmptyImageAttachmentLookup : ImageAttachmentLookup {
    override fun findExact(chatId: Long, sourceLogId: Long): IncomingImageAttachment? = null
    override fun findLatestInRoom(
        chatId: Long,
        notBeforeMillis: Long
    ): IncomingImageAttachment? = null
}

data class KakaoImageLogRow(
    val sourceLogId: Long,
    val chatId: Long,
    val userId: Long,
    val messageType: String,
    val attachment: String?,
    val version: String?,
    val createdAt: Long?
)

interface KakaoImageLogSource {
    fun findExact(chatId: Long, sourceLogId: Long): KakaoImageLogRow?
    fun findRecentInRoom(chatId: Long, limit: Int): List<KakaoImageLogRow>
}

/** Read-only adapter around KakaoTalk's attached chat_logs table. */
class KakaoDbImageLogSource(private val db: KakaoDB) : KakaoImageLogSource {
    override fun findExact(chatId: Long, sourceLogId: Long): KakaoImageLogRow? =
        db.executeQuery(
            """
            SELECT _id, chat_id, user_id, type, attachment, v, created_at
            FROM chat_logs
            WHERE chat_id = ? AND _id = ? AND type IN ('2', '3')
            LIMIT 1
            """.trimIndent(),
            arrayOf(chatId.toString(), sourceLogId.toString())
        ).firstOrNull()?.toImageLogRow()

    override fun findRecentInRoom(chatId: Long, limit: Int): List<KakaoImageLogRow> =
        db.executeQuery(
            """
            SELECT _id, chat_id, user_id, type, attachment, v, created_at
            FROM chat_logs
            WHERE chat_id = ? AND type IN ('2', '3')
            ORDER BY _id DESC
            LIMIT $limit
            """.trimIndent(),
            arrayOf(chatId.toString())
        ).mapNotNull { it.toImageLogRow() }

    private fun Map<String, String?>.toImageLogRow(): KakaoImageLogRow? {
        val sourceLogId = this["_id"]?.toLongOrNull() ?: return null
        val chatId = this["chat_id"]?.toLongOrNull() ?: return null
        val userId = this["user_id"]?.toLongOrNull() ?: return null
        val messageType = this["type"] ?: return null
        return KakaoImageLogRow(
            sourceLogId = sourceLogId,
            chatId = chatId,
            userId = userId,
            messageType = messageType,
            attachment = this["attachment"],
            version = this["v"],
            createdAt = this["created_at"]?.toLongOrNull()
        )
    }
}

/**
 * Restores a strictly validated image reference from KakaoTalk DB after a cache miss.
 * Decrypted attachment JSON and signed URLs are never logged or retained by this class.
 */
class KakaoDbImageAttachmentLookup(
    private val source: KakaoImageLogSource,
    private val parser: KakaoImageAttachmentParser,
    private val decryptAttachment: (enc: Int, encrypted: String, userId: Long) -> String =
        { enc, encrypted, userId -> KakaoDecrypt.decrypt(enc, encrypted, userId) },
    private val log: (String) -> Unit = ::println
) : ImageAttachmentLookup {
    override fun findExact(chatId: Long, sourceLogId: Long): IncomingImageAttachment? =
        runCatching { source.findExact(chatId, sourceLogId) }
            .onFailure { log("Vision DB exact lookup failed: ${it::class.simpleName}") }
            .getOrNull()
            ?.takeIf { it.chatId == chatId && it.sourceLogId == sourceLogId }
            ?.toAttachment()

    override fun findLatestInRoom(
        chatId: Long,
        notBeforeMillis: Long
    ): IncomingImageAttachment? {
        val rows = runCatching { source.findRecentInRoom(chatId, RECENT_SCAN_LIMIT) }
            .onFailure { log("Vision DB recent lookup failed: ${it::class.simpleName}") }
            .getOrDefault(emptyList())
        for (row in rows) {
            if (row.chatId != chatId) continue
            val createdAtMillis = normalizeEpochMillis(row.createdAt) ?: continue
            if (createdAtMillis < notBeforeMillis) continue
            row.toAttachment()?.let { return it }
        }
        return null
    }

    private fun KakaoImageLogRow.toAttachment(): IncomingImageAttachment? {
        val metadata = runCatching {
            JSON.parseToJsonElement(version.orEmpty()) as JsonObject
        }.getOrNull() ?: return null
        if (metadata["origin"]?.jsonPrimitive?.contentOrNull in BLOCKED_ORIGINS) return null
        val enc = metadata["enc"]?.jsonPrimitive?.intOrNull ?: return null
        val encrypted = attachment?.takeIf { it.isNotBlank() && it != "{}" } ?: return null
        val decrypted = runCatching {
            decryptAttachment(enc, encrypted, userId)
        }.getOrNull() ?: return null
        return when (
            val result = parser.parse(
                sourceLogId = sourceLogId,
                chatId = chatId,
                userId = userId,
                messageType = messageType,
                decryptedAttachment = decrypted
            )
        ) {
            is ImageAttachmentParseResult.Parsed -> result.attachment
            is ImageAttachmentParseResult.Rejected -> null
        }
    }

    private fun normalizeEpochMillis(raw: Long?): Long? {
        val value = raw?.takeIf { it > 0L } ?: return null
        return if (value < MILLIS_THRESHOLD) value * 1_000L else value
    }

    private companion object {
        const val RECENT_SCAN_LIMIT = 20
        const val MILLIS_THRESHOLD = 100_000_000_000L
        val BLOCKED_ORIGINS = setOf("SYNCMSG", "MCHATLOGS")
        val JSON = Json { ignoreUnknownKeys = true }
    }
}
