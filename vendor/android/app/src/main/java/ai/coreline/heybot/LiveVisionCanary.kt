package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.Serializable
import org.json.JSONObject
import java.io.File

/**
 * Explicit, one-shot production canary for the complete Kakao image-analysis turn.
 *
 * This is deliberately narrower than the HTTP management API: the room, command,
 * source path and confirmation value are fixed, and no listener remains running.
 */
class LiveVisionCanaryRunner(
    private val db: KakaoDB,
    private val notificationReferer: String,
    private val imageFile: File = File(IMAGE_PATH),
    private val environment: Map<String, String> = System.getenv(),
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    suspend fun run(): LiveVisionCanaryReport {
        val validation = validate()
        if (validation != null) {
            return LiveVisionCanaryReport(status = "blocked", code = validation)
        }

        return try {
            val baselineLogId = latestLogId()
            dispatchImage(imageFile.readBytes())
            val imageRow = waitForRow(
                afterLogId = baselineLogId,
                timeoutMillis = IMAGE_CONFIRM_TIMEOUT_MILLIS
            ) { row -> row.userId == Configurable.botId && row.isImage }

            dispatchText(COMMAND)
            val commandRow = waitForRow(
                afterLogId = imageRow.logId,
                timeoutMillis = TEXT_CONFIRM_TIMEOUT_MILLIS
            ) { row -> row.userId == Configurable.botId && row.message == COMMAND }

            val resultRow = waitForRow(
                afterLogId = commandRow.logId,
                timeoutMillis = RESULT_TIMEOUT_MILLIS
            ) { row ->
                row.userId == Configurable.botId && row.message.startsWith(RESULT_PREFIX)
            }
            val startRow = rowsAfter(commandRow.logId).firstOrNull { row ->
                row.userId == Configurable.botId && row.message == START_MESSAGE
            }

            LiveVisionCanaryReport(
                status = "passed",
                code = "LIVE_KAKAO_VISION_TURN_CONFIRMED",
                chatId = CHAT_ID.toString(),
                baselineLogId = baselineLogId,
                imageLogId = imageRow.logId,
                commandLogId = commandRow.logId,
                startReplyLogId = startRow?.logId,
                resultReplyLogId = resultRow.logId,
                resultText = resultRow.message.take(MAX_RESULT_CHARS)
            )
        } catch (error: LiveVisionCanaryFailure) {
            LiveVisionCanaryReport(status = "failed", code = error.code)
        } catch (_: Throwable) {
            LiveVisionCanaryReport(status = "failed", code = "LIVE_CANARY_EXCEPTION")
        } finally {
            db.closeConnection()
        }
    }

    private fun validate(): String? {
        if (environment[CONFIRM_ENV] != CONFIRM_VALUE) return "LIVE_CANARY_CONFIRMATION_REQUIRED"
        if (!imageFile.isFile) return "LIVE_CANARY_IMAGE_MISSING"
        if (imageFile.length() !in 1L..MAX_IMAGE_BYTES) return "LIVE_CANARY_IMAGE_SIZE_INVALID"
        val header = runCatching {
            imageFile.inputStream().use { input ->
                ByteArray(PNG_MAGIC.size).also { bytes ->
                    var offset = 0
                    while (offset < bytes.size) {
                        val count = input.read(bytes, offset, bytes.size - offset)
                        if (count < 0) return@use bytes.copyOf(offset)
                        offset += count
                    }
                }
            }
        }.getOrNull() ?: return "LIVE_CANARY_IMAGE_UNREADABLE"
        if (!header.contentEquals(PNG_MAGIC)) return "LIVE_CANARY_IMAGE_NOT_PNG"
        return null
    }

    private suspend fun dispatchImage(bytes: ByteArray) {
        val dispatched = CompletableDeferred<Result<Unit>>()
        Replier.sendPhotoBytes(CHAT_ID, bytes) { result ->
            if (!dispatched.isCompleted) dispatched.complete(result)
        }
        try {
            withTimeout(DISPATCH_TIMEOUT_MILLIS) { dispatched.await() }.getOrThrow()
        } catch (_: Throwable) {
            throw LiveVisionCanaryFailure("LIVE_CANARY_IMAGE_DISPATCH_FAILED")
        }
    }

    private suspend fun dispatchText(message: String) {
        val dispatched = CompletableDeferred<Result<Unit>>()
        Replier.sendMessage(notificationReferer, CHAT_ID, message, null) { result ->
            if (!dispatched.isCompleted) dispatched.complete(result)
        }
        try {
            withTimeout(DISPATCH_TIMEOUT_MILLIS) { dispatched.await() }.getOrThrow()
        } catch (_: Throwable) {
            throw LiveVisionCanaryFailure("LIVE_CANARY_COMMAND_DISPATCH_FAILED")
        }
    }

    private suspend fun waitForRow(
        afterLogId: Long,
        timeoutMillis: Long,
        predicate: (CanaryChatRow) -> Boolean
    ): CanaryChatRow {
        val deadline = nowMillis() + timeoutMillis
        while (nowMillis() < deadline) {
            rowsAfter(afterLogId).firstOrNull(predicate)?.let { return it }
            delay(POLL_INTERVAL_MILLIS)
        }
        val code = when (timeoutMillis) {
            IMAGE_CONFIRM_TIMEOUT_MILLIS -> "LIVE_CANARY_IMAGE_DB_TIMEOUT"
            TEXT_CONFIRM_TIMEOUT_MILLIS -> "LIVE_CANARY_COMMAND_DB_TIMEOUT"
            else -> "LIVE_CANARY_RESULT_DB_TIMEOUT"
        }
        throw LiveVisionCanaryFailure(code)
    }

    private fun latestLogId(): Long = db.connection.rawQuery(
        "SELECT COALESCE(MAX(_id), 0) FROM chat_logs",
        null
    ).use { cursor ->
        if (cursor.moveToFirst()) cursor.getLong(0) else 0L
    }

    private fun rowsAfter(logId: Long): List<CanaryChatRow> = db.connection.rawQuery(
        "SELECT _id, user_id, type, message, attachment, v " +
            "FROM chat_logs WHERE _id > ? AND chat_id = ? ORDER BY _id ASC",
        arrayOf(logId.toString(), CHAT_ID.toString())
    ).use { cursor ->
        buildList {
            val idIndex = cursor.getColumnIndexOrThrow("_id")
            val userIndex = cursor.getColumnIndexOrThrow("user_id")
            val typeIndex = cursor.getColumnIndexOrThrow("type")
            val messageIndex = cursor.getColumnIndexOrThrow("message")
            val attachmentIndex = cursor.getColumnIndexOrThrow("attachment")
            val versionIndex = cursor.getColumnIndexOrThrow("v")
            while (cursor.moveToNext()) {
                val id = cursor.getLong(idIndex)
                val userId = cursor.getLong(userIndex)
                val type = cursor.getString(typeIndex).orEmpty()
                val version = runCatching { JSONObject(cursor.getString(versionIndex)) }.getOrNull()
                if (version?.optString("origin") in IGNORED_ORIGINS) continue
                val enc = version?.optInt("enc", 0) ?: 0
                val message = decrypt(cursor.getString(messageIndex), enc, userId)
                val attachment = decrypt(cursor.getString(attachmentIndex), enc, userId)
                val isImage = KakaoImageAttachmentParser().parse(
                    sourceLogId = id,
                    chatId = CHAT_ID,
                    userId = userId,
                    messageType = type,
                    decryptedAttachment = attachment
                ) is ImageAttachmentParseResult.Parsed
                add(CanaryChatRow(id, userId, message, isImage))
            }
        }
    }

    private fun decrypt(value: String?, enc: Int, userId: Long): String {
        if (value.isNullOrEmpty() || value == "{}") return value.orEmpty()
        return runCatching { KakaoDecrypt.decrypt(enc, value, userId) }.getOrDefault(value)
    }

    private class LiveVisionCanaryFailure(val code: String) : IllegalStateException()

    private data class CanaryChatRow(
        val logId: Long,
        val userId: Long,
        val message: String,
        val isImage: Boolean
    )

    companion object {
        const val ARGUMENT = "--live-vision-canary"
        const val CONFIRM_ENV = "IRIS_LIVE_VISION_CANARY_CONFIRM"
        const val CONFIRM_VALUE = "SEND_R01_IMAGE_AND_ANALYZE"
        const val IMAGE_PATH = "/data/local/tmp/heybot-vision-canary.png"
        const val CHAT_ID = 18_480_337_854_645_134L
        const val COMMAND = "헤이봇 이미지 분석"
        const val START_MESSAGE = "이미지 분석을 시작했어요. 완료되면 이 방에 설명해드릴게요."
        const val RESULT_PREFIX = "이미지 분석 결과\n"
        private const val MAX_IMAGE_BYTES = 10L * 1024L * 1024L
        private const val MAX_RESULT_CHARS = 1_000
        private const val DISPATCH_TIMEOUT_MILLIS = 10_000L
        private const val IMAGE_CONFIRM_TIMEOUT_MILLIS = 60_000L
        private const val TEXT_CONFIRM_TIMEOUT_MILLIS = 30_000L
        private const val RESULT_TIMEOUT_MILLIS = 180_000L
        private const val POLL_INTERVAL_MILLIS = 250L
        private val PNG_MAGIC = byteArrayOf(
            0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A
        )
        private val IGNORED_ORIGINS = setOf("SYNCMSG", "MCHATLOGS")
    }
}

@Serializable
data class LiveVisionCanaryReport(
    val status: String,
    val code: String,
    val chatId: String? = null,
    val baselineLogId: Long? = null,
    val imageLogId: Long? = null,
    val commandLogId: Long? = null,
    val startReplyLogId: Long? = null,
    val resultReplyLogId: Long? = null,
    val resultText: String? = null
)
