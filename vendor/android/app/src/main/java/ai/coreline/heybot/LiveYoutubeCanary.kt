package ai.coreline.heybot

import android.util.Base64
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.Serializable
import org.json.JSONObject

/**
 * Explicit one-shot R01 test of the YouTube command path.
 *
 * The normal observer excludes bot-authored rows to prevent loops. This runner
 * therefore sends one visible command as the bot and feeds that exact confirmed
 * DB row to a separate, in-memory coordinator with botId=0. It exercises the
 * production parser, room policy, proxy, MP4 delivery and Kakao DB confirmation
 * without writing a fake inbound row or changing the running observer.
 */
class LiveYoutubeCanaryRunner(
    private val db: KakaoDB,
    private val notificationReferer: String,
    private val environment: Map<String, String> = System.getenv(),
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    suspend fun run(): LiveYoutubeCanaryReport {
        val command = environment[COMMAND_BASE64_ENV]
            ?.let { encoded -> runCatching { String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8) }.getOrNull() }
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: DEFAULT_COMMAND
        val commandRoute = BotCommandRouter(TRIGGER).route(command)
        if (commandRoute !is BotCommand.DownloadYoutube) {
            return LiveYoutubeCanaryReport(status = "blocked", code = "LIVE_YOUTUBE_COMMAND_INVALID")
        }
        if (environment[CONFIRM_ENV] != CONFIRM_VALUE) {
            return LiveYoutubeCanaryReport(status = "blocked", code = "LIVE_YOUTUBE_CONFIRMATION_REQUIRED")
        }

        var coordinator: YoutubeDownloadJobCoordinator? = null
        return try {
            val youtubeSettings = (YoutubeDownloadProxySettings.load(environment)
                as? YoutubeDownloadProxySettingsLoadResult.Ready)?.settings
                ?: return LiveYoutubeCanaryReport(status = "blocked", code = "LIVE_YOUTUBE_PROXY_SETTINGS_NOT_READY")
            val glmSettings = (GlmSettings.load(environment) as? GlmSettingsLoadResult.Ready)?.settings
                ?: return LiveYoutubeCanaryReport(status = "blocked", code = "LIVE_YOUTUBE_GLM_SETTINGS_NOT_READY")
            val policy = RoomCapabilityPolicyStore.load(
                settings = glmSettings.roomCapabilities,
                managedChatIds = glmSettings.allowedChatIds,
                controlChatId = glmSettings.adminControlChatId
            )
            if (!policy.allows(CHAT_ID, RoomCapability.TEXT) ||
                !policy.allows(CHAT_ID, RoomCapability.YOUTUBE_DOWNLOAD)
            ) {
                return LiveYoutubeCanaryReport(status = "blocked", code = "LIVE_YOUTUBE_ROOM_POLICY_DENIED")
            }

            val delegate = YoutubeDownloadProxyClient(youtubeSettings)
            val reuseJobId = environment[REUSE_JOB_ID_ENV]?.trim()?.takeIf { it.isNotEmpty() }
            val gateway: YoutubeDownloadProxyGateway = if (reuseJobId == null) {
                delegate
            } else {
                // The server canary already validated this chat-scoped artifact.
                // Reuse is test-only and avoids paying/downloading twice while
                // still exercising the Android coordinator and Kakao delivery.
                object : YoutubeDownloadProxyGateway {
                    override suspend fun create(
                        requestId: String,
                        chatId: Long,
                        userId: Long,
                        logId: Long,
                        url: String
                    ): Result<YoutubeDownloadProxyJob> = delegate.status(reuseJobId, chatId)

                    override suspend fun status(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob> =
                        if (jobId == reuseJobId) delegate.status(jobId, chatId)
                        else Result.failure(IllegalArgumentException("LIVE_YOUTUBE_REUSE_JOB_MISMATCH"))

                    override suspend fun cancel(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob> =
                        if (jobId == reuseJobId) delegate.status(jobId, chatId)
                        else Result.failure(IllegalArgumentException("LIVE_YOUTUBE_REUSE_JOB_MISMATCH"))

                    override suspend fun download(jobId: String, chatId: Long): Result<ByteArray> =
                        if (jobId == reuseJobId) delegate.download(jobId, chatId)
                        else Result.failure(IllegalArgumentException("LIVE_YOUTUBE_REUSE_JOB_MISMATCH"))
                }
            }
            val state = InMemoryYoutubeDownloadJobStateStore()
            coordinator = YoutubeDownloadJobCoordinator(
                settings = youtubeSettings,
                trigger = glmSettings.trigger,
                // The isolated canary owns its one bot-authored command row.
                botId = 0L,
                gateway = gateway,
                textSender = YoutubeDownloadTextReplySender { chatId, message, threadId ->
                    Replier.sendMessage(notificationReferer, chatId, message, threadId)
                },
                youtubeDownloadSender = YoutubeDownloadBytesReplySender { chatId, bytes ->
                    Replier.sendVideoBytes(chatId, bytes)
                },
                stateStore = state,
                roomCapabilityPolicy = policy,
                requestTraceStore = RequestTraceStore.inMemory(nowMillis)
            )

            val baselineLogId = latestLogId()
            dispatchText(command)
            val commandRow = waitForRow(baselineLogId, TEXT_CONFIRM_TIMEOUT_MILLIS) {
                it.userId == Configurable.botId && it.message == command && it.type == "1"
            }
            coordinator.onIncoming(
                GlmIncomingMessage(
                    logId = commandRow.logId,
                    chatId = CHAT_ID,
                    userId = commandRow.userId,
                    messageType = commandRow.type,
                    message = command,
                    threadId = null
                )
            )
            val startRow = waitForRow(commandRow.logId, TEXT_CONFIRM_TIMEOUT_MILLIS) {
                it.userId == Configurable.botId && it.message == START_MESSAGE && it.type == "1"
            }
            val videoRow = waitForVideoOrTerminal(state, commandRow.userId, startRow.logId)
            // This is the same confirmed outgoing-row signal that the live
            // coordinator receives from ObserverHelper in normal operation.
            coordinator.onIncoming(
                GlmIncomingMessage(
                    logId = videoRow.logId,
                    chatId = CHAT_ID,
                    userId = videoRow.userId,
                    messageType = videoRow.type,
                    message = "",
                    threadId = null
                )
            )
            val local = waitForDelivered(state, commandRow.userId)
            LiveYoutubeCanaryReport(
                status = "passed",
                code = "LIVE_KAKAO_YOUTUBE_TURN_CONFIRMED",
                chatId = CHAT_ID.toString(),
                baselineLogId = baselineLogId,
                commandLogId = commandRow.logId,
                startReplyLogId = startRow.logId,
                videoReplyLogId = videoRow.logId,
                jobId = local.jobId,
                deliveryStatus = local.status
            )
        } catch (error: LiveYoutubeCanaryFailure) {
            LiveYoutubeCanaryReport(status = "failed", code = error.code)
        } catch (_: Throwable) {
            LiveYoutubeCanaryReport(status = "failed", code = "LIVE_YOUTUBE_CANARY_EXCEPTION")
        } finally {
            coordinator?.close()
            db.closeConnection()
        }
    }

    private suspend fun dispatchText(message: String) {
        val dispatched = CompletableDeferred<Result<Unit>>()
        Replier.sendMessage(notificationReferer, CHAT_ID, message, null) {
            if (!dispatched.isCompleted) dispatched.complete(it)
        }
        try {
            withTimeout(DISPATCH_TIMEOUT_MILLIS) { dispatched.await() }.getOrThrow()
        } catch (_: Throwable) {
            throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_COMMAND_DISPATCH_FAILED")
        }
    }

    private suspend fun waitForDelivered(
        state: YoutubeDownloadJobStateStore,
        userId: Long
    ): LocalYoutubeDownloadJob {
        val deadline = nowMillis() + DELIVERY_STATE_TIMEOUT_MILLIS
        while (nowMillis() < deadline) {
            val job = state.latest(CHAT_ID, userId)
            if (job?.status == "delivered") return job
            delay(POLL_INTERVAL_MILLIS)
        }
        throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_DELIVERY_STATE_TIMEOUT")
    }

    private suspend fun waitForVideoOrTerminal(
        state: YoutubeDownloadJobStateStore,
        userId: Long,
        afterLogId: Long
    ): CanaryRow {
        val deadline = nowMillis() + VIDEO_CONFIRM_TIMEOUT_MILLIS
        while (nowMillis() < deadline) {
            rowsAfter(afterLogId)
                .firstOrNull { it.userId == Configurable.botId && it.type in VIDEO_TYPES }
                ?.let { return it }
            when (state.latest(CHAT_ID, userId)?.status) {
                "failed" -> throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_DELIVERY_FAILED")
                "awaiting_unlock" -> throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_DELIVERY_UNCONFIRMED")
                "cancelled" -> throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_DELIVERY_CANCELLED")
            }
            delay(POLL_INTERVAL_MILLIS)
        }
        throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_VIDEO_DB_TIMEOUT")
    }

    private suspend fun waitForRow(
        afterLogId: Long,
        timeoutMillis: Long,
        predicate: (CanaryRow) -> Boolean
    ): CanaryRow {
        val deadline = nowMillis() + timeoutMillis
        while (nowMillis() < deadline) {
            rowsAfter(afterLogId).firstOrNull(predicate)?.let { return it }
            delay(POLL_INTERVAL_MILLIS)
        }
        throw LiveYoutubeCanaryFailure("LIVE_YOUTUBE_TEXT_DB_TIMEOUT")
    }

    private fun latestLogId(): Long = db.connection.rawQuery(
        "SELECT COALESCE(MAX(_id), 0) FROM chat_logs",
        null
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else 0L }

    private fun rowsAfter(logId: Long): List<CanaryRow> = db.connection.rawQuery(
        "SELECT _id, user_id, type, message, v FROM chat_logs " +
            "WHERE _id > ? AND chat_id = ? ORDER BY _id ASC",
        arrayOf(logId.toString(), CHAT_ID.toString())
    ).use { cursor ->
        buildList {
            val idIndex = cursor.getColumnIndexOrThrow("_id")
            val userIndex = cursor.getColumnIndexOrThrow("user_id")
            val typeIndex = cursor.getColumnIndexOrThrow("type")
            val messageIndex = cursor.getColumnIndexOrThrow("message")
            val versionIndex = cursor.getColumnIndexOrThrow("v")
            while (cursor.moveToNext()) {
                val userId = cursor.getLong(userIndex)
                val version = runCatching { JSONObject(cursor.getString(versionIndex)) }.getOrNull()
                if (version?.optString("origin") in IGNORED_ORIGINS) continue
                val enc = version?.optInt("enc", 0) ?: 0
                add(
                    CanaryRow(
                        logId = cursor.getLong(idIndex),
                        userId = userId,
                        type = cursor.getString(typeIndex).orEmpty(),
                        message = decrypt(cursor.getString(messageIndex), enc, userId)
                    )
                )
            }
        }
    }

    private fun decrypt(value: String?, enc: Int, userId: Long): String {
        if (value.isNullOrEmpty() || value == "{}") return value.orEmpty()
        return runCatching { KakaoDecrypt.decrypt(enc, value, userId) }.getOrDefault(value)
    }

    private class LiveYoutubeCanaryFailure(val code: String) : IllegalStateException()

    private data class CanaryRow(
        val logId: Long,
        val userId: Long,
        val type: String,
        val message: String
    )

    companion object {
        const val ARGUMENT = "--live-youtube-canary"
        const val CONFIRM_ENV = "IRIS_LIVE_YOUTUBE_CANARY_CONFIRM"
        const val CONFIRM_VALUE = "SEND_R01_YOUTUBE_DOWNLOAD"
        const val COMMAND_BASE64_ENV = "IRIS_LIVE_YOUTUBE_CANARY_COMMAND_B64"
        const val REUSE_JOB_ID_ENV = "IRIS_LIVE_YOUTUBE_CANARY_REUSE_JOB_ID"
        const val CHAT_ID = 18_480_337_854_645_134L
        const val TRIGGER = "헤이봇"
        const val DEFAULT_COMMAND =
            "헤이봇 유튜브 다운로드 https://www.youtube.com/watch?v=-Yzp92fX_aU"
        const val START_MESSAGE = "유튜브 다운로드를 시작했어요. 완료되면 이 방으로 보내드릴게요."
        private const val DISPATCH_TIMEOUT_MILLIS = 10_000L
        private const val TEXT_CONFIRM_TIMEOUT_MILLIS = 30_000L
        private const val VIDEO_CONFIRM_TIMEOUT_MILLIS = 10 * 60_000L
        private const val DELIVERY_STATE_TIMEOUT_MILLIS = 10_000L
        private const val POLL_INTERVAL_MILLIS = 250L
        private val VIDEO_TYPES = setOf("3", "16")
        private val IGNORED_ORIGINS = setOf("SYNCMSG", "MCHATLOGS")
    }
}

@Serializable
data class LiveYoutubeCanaryReport(
    val status: String,
    val code: String,
    val chatId: String? = null,
    val baselineLogId: Long? = null,
    val commandLogId: Long? = null,
    val startReplyLogId: Long? = null,
    val videoReplyLogId: Long? = null,
    val jobId: String? = null,
    val deliveryStatus: String? = null
)
