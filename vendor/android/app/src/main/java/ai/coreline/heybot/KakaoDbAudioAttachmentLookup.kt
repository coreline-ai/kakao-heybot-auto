package ai.coreline.heybot

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

interface AudioAttachmentLookup {
    fun findExact(chatId: Long, sourceLogId: Long): IncomingAudioAttachment?
    fun findLatest(chatId: Long, notBeforeMillis: Long): IncomingAudioAttachment?
}

object EmptyAudioAttachmentLookup : AudioAttachmentLookup {
    override fun findExact(chatId: Long, sourceLogId: Long) = null
    override fun findLatest(chatId: Long, notBeforeMillis: Long) = null
}

data class KakaoAudioLogRow(
    val sourceLogId: Long,
    val chatId: Long,
    val userId: Long,
    val messageType: String,
    val attachment: String?,
    val version: String?,
    val createdAt: Long?
)

interface KakaoAudioLogSource {
    fun findExact(chatId: Long, sourceLogId: Long): KakaoAudioLogRow?
    fun findRecent(chatId: Long, limit: Int): List<KakaoAudioLogRow>
}

class KakaoDbAudioLogSource(private val db: KakaoDB) : KakaoAudioLogSource {
    override fun findExact(chatId: Long, sourceLogId: Long): KakaoAudioLogRow? =
        db.executeQuery(
            """
            SELECT _id, chat_id, user_id, type, attachment, v, created_at
            FROM chat_logs
            WHERE chat_id = ? AND _id = ? AND type = '18'
            LIMIT 1
            """.trimIndent(),
            arrayOf(chatId.toString(), sourceLogId.toString())
        ).firstOrNull()?.toAudioRow()

    override fun findRecent(chatId: Long, limit: Int): List<KakaoAudioLogRow> =
        db.executeQuery(
            """
            SELECT _id, chat_id, user_id, type, attachment, v, created_at
            FROM chat_logs
            WHERE chat_id = ? AND type = '18'
            ORDER BY _id DESC
            LIMIT $limit
            """.trimIndent(),
            arrayOf(chatId.toString())
        ).mapNotNull { it.toAudioRow() }

    private fun Map<String, String?>.toAudioRow(): KakaoAudioLogRow? {
        return KakaoAudioLogRow(
            sourceLogId = this["_id"]?.toLongOrNull() ?: return null,
            chatId = this["chat_id"]?.toLongOrNull() ?: return null,
            userId = this["user_id"]?.toLongOrNull() ?: return null,
            messageType = this["type"] ?: return null,
            attachment = this["attachment"],
            version = this["v"],
            createdAt = this["created_at"]?.toLongOrNull()
        )
    }
}

/** Read-only, same-room DB fallback for room-shared audio source selection. */
class KakaoDbAudioAttachmentLookup(
    private val source: KakaoAudioLogSource,
    private val parser: KakaoAudioAttachmentParser,
    private val decryptAttachment: (enc: Int, encrypted: String, userId: Long) -> String =
        { enc, encrypted, userId -> KakaoDecrypt.decrypt(enc, encrypted, userId) },
    private val log: (String) -> Unit = ::println
) : AudioAttachmentLookup {
    override fun findExact(
        chatId: Long,
        sourceLogId: Long
    ): IncomingAudioAttachment? = runCatching { source.findExact(chatId, sourceLogId) }
        .onFailure { log("Audio DB exact lookup failed: ${it::class.simpleName}") }
        .getOrNull()
        ?.takeIf { it.chatId == chatId }
        ?.toAttachment()

    override fun findLatest(
        chatId: Long,
        notBeforeMillis: Long
    ): IncomingAudioAttachment? {
        val rows = runCatching { source.findRecent(chatId, RECENT_SCAN_LIMIT) }
            .onFailure { log("Audio DB recent lookup failed: ${it::class.simpleName}") }
            .getOrDefault(emptyList())
        for (row in rows) {
            if (row.chatId != chatId) continue
            val createdAtMillis = normalizeEpochMillis(row.createdAt) ?: continue
            if (createdAtMillis < notBeforeMillis) continue
            row.toAttachment()?.let { return it }
        }
        return null
    }

    private fun KakaoAudioLogRow.toAttachment(): IncomingAudioAttachment? {
        val metadata = runCatching { JSON.parseToJsonElement(version.orEmpty()) as JsonObject }
            .getOrNull() ?: return null
        if (metadata["origin"]?.jsonPrimitive?.contentOrNull in BLOCKED_ORIGINS) return null
        val enc = metadata["enc"]?.jsonPrimitive?.intOrNull ?: return null
        val encrypted = attachment?.takeIf { it.isNotBlank() && it != "{}" } ?: return null
        val decrypted = runCatching { decryptAttachment(enc, encrypted, userId) }.getOrNull()
            ?: return null
        return (parser.parse(sourceLogId, chatId, userId, messageType, decrypted)
            as? AudioAttachmentParseResult.Parsed)?.attachment
    }

    private fun normalizeEpochMillis(raw: Long?): Long? {
        val value = raw?.takeIf { it > 0L } ?: return null
        return if (value < MILLIS_THRESHOLD) value * 1_000L else value
    }

    private companion object {
        const val RECENT_SCAN_LIMIT = 30
        const val MILLIS_THRESHOLD = 100_000_000_000L
        val BLOCKED_ORIGINS = setOf("SYNCMSG", "MCHATLOGS")
        val JSON = Json { ignoreUnknownKeys = true }
    }
}
