package ai.coreline.heybot

import android.util.Base64
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.Serializable
import org.json.JSONObject
import java.io.File

/**
 * Explicit one-shot canary for Kakao MP3/M4A/WAV upload -> DB attachment parsing ->
 * proxy STT -> selected LLM summary -> Kakao text delivery.
 *
 * The bot-authored source is accepted only by this isolated coordinator
 * (`botId = 0`); the continuously running production observer keeps its normal
 * self-message exclusion, so this cannot create an audio response loop.
 */
class LiveAudioCanaryRunner(
    private val db: KakaoDB,
    private val notificationReferer: String,
    private val audioFile: File = File(AUDIO_PATH),
    private val environment: Map<String, String> = System.getenv(),
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    suspend fun run(): LiveAudioCanaryReport {
        val sourceFile = environment[FILE_ENV]?.let(::File) ?: audioFile
        val format = KakaoAudioShareFormat.parse(environment[FORMAT_ENV] ?: sourceFile.extension)
            ?: return LiveAudioCanaryReport(status = "blocked", code = "LIVE_AUDIO_FORMAT_INVALID")
        val command = environment[COMMAND_BASE64_ENV]
            ?.let { encoded ->
                runCatching { String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8) }
                    .getOrNull()
            }
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: COMMAND
        if (BotCommandRouter("헤이봇").route(command) !is BotCommand.SummarizeAudio) {
            return LiveAudioCanaryReport(status = "blocked", code = "LIVE_AUDIO_COMMAND_INVALID")
        }
        validate(sourceFile, format)?.let {
            return LiveAudioCanaryReport(
                status = "blocked",
                code = it,
                sourceExtension = format.extension
            )
        }

        var coordinator: AudioAnalysisCoordinator? = null
        return try {
            val audioSettings = (AudioAnalysisSettings.load(environment)
                as? AudioAnalysisSettingsLoadResult.Ready)?.settings
                ?: return LiveAudioCanaryReport("blocked", "LIVE_AUDIO_SETTINGS_NOT_READY")
            val glmSettings = (GlmSettings.load(environment)
                as? GlmSettingsLoadResult.Ready)?.settings
                ?: return LiveAudioCanaryReport("blocked", "LIVE_AUDIO_GLM_NOT_READY")
            val policy = RoomCapabilityPolicyStore.load(
                settings = glmSettings.roomCapabilities,
                managedChatIds = glmSettings.allowedChatIds,
                controlChatId = glmSettings.adminControlChatId
            )
            if (!policy.allows(CHAT_ID, RoomCapability.TEXT) ||
                !policy.allows(CHAT_ID, RoomCapability.AUDIO_ANALYSIS)
            ) {
                return LiveAudioCanaryReport("blocked", "LIVE_AUDIO_ROOM_POLICY_DENIED")
            }

            val proxySettings = ConversationProxySettings.load(environment)
            val readyProxySettings = (proxySettings as? ConversationProxySettingsLoadResult.Ready)?.settings
            val modeStore = ConversationEngineModeStore(
                file = readyProxySettings?.modeFile
                    ?: File(ConversationProxySettings.DEFAULT_MODE_FILE)
            )
            val glm = GlmClient(glmSettings)
            val conversation = ConversationGatewayRouter(
                modeStore = modeStore,
                glm = glm,
                codex = readyProxySettings?.let { ConversationProxyClient(it, ConversationEngine.CODEX) },
                grok = readyProxySettings?.let { ConversationProxyClient(it, ConversationEngine.GROK) }
            )
            val gateway = AudioAnalysisProxyClient(audioSettings)
            val stateStore = InMemoryAudioAnalysisStateStore()
            coordinator = AudioAnalysisCoordinator(
                settings = audioSettings,
                trigger = glmSettings.trigger,
                // This one-shot canary deliberately owns the bot-authored source.
                botId = 0L,
                gateway = gateway,
                summaryGenerator = AudioSummaryGenerator(conversation, glmSettings.model),
                engineModeStore = modeStore,
                replySender = AudioAnalysisReplySender { chatId, message, threadId ->
                    Replier.sendMessage(notificationReferer, chatId, message, threadId)
                },
                roomCapabilityPolicy = policy,
                stateStore = stateStore
            )

            val reuseLatest = environment[REUSE_ENV].equals("true", ignoreCase = true)
            val baselineLogId = latestLogId()
            val audioRow = if (reuseLatest) {
                latestBotAudioRow()
                    ?: return LiveAudioCanaryReport("failed", "LIVE_AUDIO_REUSABLE_FILE_NOT_FOUND")
            } else {
                dispatchAudio(sourceFile.readBytes(), format)
                waitForRow(baselineLogId, AUDIO_CONFIRM_TIMEOUT_MILLIS) {
                    it.userId == Configurable.botId && it.audioAttachment != null
                }
            }

            // Preflight exposes the precise route/validation error in this
            // one-shot report. The normal coordinator then reuses the same
            // idempotent requestId without creating a second STT job.
            val source = audioRow.audioAttachment
                ?: return LiveAudioCanaryReport("failed", "LIVE_AUDIO_ATTACHMENT_INVALID")
            gateway.create("audio:$CHAT_ID:${audioRow.logId}", CHAT_ID, source).getOrElse { error ->
                val reason = (error as? AudioProxyException)?.reasonCode ?: "AUDIO_CREATE_FAILED"
                return LiveAudioCanaryReport(
                    status = "failed",
                    code = "LIVE_AUDIO_CREATE_FAILED",
                    chatId = CHAT_ID.toString(),
                    baselineLogId = baselineLogId,
                    audioLogId = audioRow.logId,
                    failureText = reason
                )
            }

            dispatchText(command)
            val commandRow = waitForRow(
                maxOf(audioRow.logId, baselineLogId),
                TEXT_CONFIRM_TIMEOUT_MILLIS
            ) {
                it.userId == Configurable.botId && it.message == command
            }

            coordinator.onIncoming(
                GlmIncomingMessage(
                    logId = audioRow.logId,
                    chatId = CHAT_ID,
                    userId = audioRow.userId,
                    messageType = KakaoAudioAttachmentParser.FILE_MESSAGE_TYPE,
                    message = "",
                    threadId = null,
                    audioAttachment = audioRow.audioAttachment
                )
            )
            coordinator.onIncoming(
                GlmIncomingMessage(
                    logId = commandRow.logId,
                    chatId = CHAT_ID,
                    userId = commandRow.userId,
                    messageType = "1",
                    message = command,
                    threadId = null
                )
            )

            if (environment[CANCEL_AFTER_START_ENV].equals("true", ignoreCase = true)) {
                waitForBotReply(commandRow.logId, TEXT_CONFIRM_TIMEOUT_MILLIS) {
                    it.startsWith("음성 분석을 시작했어요.")
                }
                dispatchCommand(coordinator, "헤이봇 음성 취소") {
                    it == "음성 분석을 취소했어요."
                }
                val cancelled = stateStore.latest(CHAT_ID, Configurable.botId)
                return LiveAudioCanaryReport(
                    status = "passed",
                    code = "LIVE_KAKAO_AUDIO_CANCEL_CONFIRMED",
                    sourceExtension = source.declaredExtension,
                    chatId = CHAT_ID.toString(),
                    baselineLogId = baselineLogId,
                    audioLogId = audioRow.logId,
                    commandLogId = commandRow.logId,
                    engine = cancelled?.engine?.displayName,
                    summaryPattern = cancelled?.profile?.pattern?.wireValue,
                    summaryView = cancelled?.profile?.view?.wireValue,
                    controlsVerified = listOf("cancel")
                )
            }

            val resultRow = waitForTerminalResult(commandRow.logId)
            val local = stateStore.latest(CHAT_ID, Configurable.botId)
            val transcript = local?.let { gateway.status(it.jobId, CHAT_ID).getOrNull()?.result }
            val controlsVerified = if (
                environment[VERIFY_CONTROLS_ENV].equals("true", ignoreCase = true)
            ) {
                verifyControls(coordinator, stateStore)
            } else emptyList()
            LiveAudioCanaryReport(
                status = "passed",
                code = "LIVE_KAKAO_AUDIO_TURN_CONFIRMED",
                sourceExtension = source.declaredExtension,
                chatId = CHAT_ID.toString(),
                baselineLogId = baselineLogId,
                audioLogId = audioRow.logId,
                commandLogId = commandRow.logId,
                resultReplyLogId = resultRow.logId,
                engine = local?.engine?.displayName,
                summaryPattern = local?.profile?.pattern?.wireValue,
                summaryView = local?.profile?.view?.wireValue,
                durationMillis = transcript?.durationMs,
                segmentCount = transcript?.segments?.size,
                transcriptText = transcript?.segments?.joinToString(" ") { it.text }?.take(MAX_RESULT_CHARS),
                resultText = resultRow.message.take(MAX_RESULT_CHARS),
                controlsVerified = controlsVerified
            )
        } catch (error: LiveAudioCanaryFailure) {
            LiveAudioCanaryReport(
                status = "failed",
                code = error.code,
                failureText = error.safeDetail?.take(MAX_RESULT_CHARS)
            )
        } catch (_: Throwable) {
            LiveAudioCanaryReport(status = "failed", code = "LIVE_AUDIO_CANARY_EXCEPTION")
        } finally {
            coordinator?.close()
            db.closeConnection()
        }
    }

    private fun validate(sourceFile: File, format: KakaoAudioShareFormat): String? {
        if (environment[CONFIRM_ENV] != CONFIRM_VALUE) return "LIVE_AUDIO_CONFIRMATION_REQUIRED"
        if (!sourceFile.isFile) return "LIVE_AUDIO_FILE_MISSING"
        if (sourceFile.length() !in 1L..MAX_AUDIO_BYTES) return "LIVE_AUDIO_FILE_SIZE_INVALID"
        val header = runCatching {
            sourceFile.inputStream().use { input ->
                ByteArray(12).also { bytes ->
                    var offset = 0
                    while (offset < bytes.size) {
                        val count = input.read(bytes, offset, bytes.size - offset)
                        if (count < 0) return@use bytes.copyOf(offset)
                        offset += count
                    }
                }
            }
        }.getOrNull() ?: return "LIVE_AUDIO_FILE_UNREADABLE"
        if (!format.matchesMagic(header)) {
            return "LIVE_AUDIO_FILE_MAGIC_MISMATCH"
        }
        return null
    }

    private suspend fun dispatchAudio(bytes: ByteArray, format: KakaoAudioShareFormat) {
        val dispatched = CompletableDeferred<Result<Unit>>()
        Replier.sendAudioBytes(CHAT_ID, bytes, format) {
            if (!dispatched.isCompleted) dispatched.complete(it)
        }
        try {
            withTimeout(DISPATCH_TIMEOUT_MILLIS) { dispatched.await() }.getOrThrow()
        } catch (_: Throwable) {
            throw LiveAudioCanaryFailure("LIVE_AUDIO_DISPATCH_FAILED")
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
            throw LiveAudioCanaryFailure("LIVE_AUDIO_COMMAND_DISPATCH_FAILED")
        }
    }

    private suspend fun verifyControls(
        coordinator: AudioAnalysisCoordinator,
        stateStore: AudioAnalysisStateStore
    ): List<String> {
        val verified = mutableListOf<String>()
        dispatchCommand(coordinator, "헤이봇 음성 상태") {
            it.startsWith("음성 분석 상태:")
        }
        verified += "status"
        dispatchCommand(coordinator, "헤이봇 음성 원문 1") {
            it.startsWith("음성 원문 1/")
        }
        verified += "transcript"
        dispatchCommand(coordinator, "헤이봇 음성 근거 1") {
            it.startsWith("음성 근거 1/")
        }
        verified += "evidence"
        dispatchCommand(coordinator, "헤이봇 음성 재요약", RESULT_TIMEOUT_MILLIS) {
            it.startsWith(RESULT_PREFIX)
        }
        verified += "resummarize"
        dispatchCommand(coordinator, "헤이봇 음성 삭제") {
            it == "저장된 음성 전사 기록을 삭제했어요."
        }
        check(stateStore.latest(CHAT_ID, Configurable.botId) == null) {
            "LIVE_AUDIO_DELETE_STATE_RETAINED"
        }
        verified += "delete"
        dispatchCommand(coordinator, "헤이봇 음성 상태") {
            it == "확인할 음성 분석 작업이 없어요."
        }
        verified += "post-delete-status"
        return verified
    }

    private suspend fun dispatchCommand(
        coordinator: AudioAnalysisCoordinator,
        command: String,
        timeoutMillis: Long = TEXT_CONFIRM_TIMEOUT_MILLIS,
        expectedReply: (String) -> Boolean
    ): CanaryAudioRow {
        val baseline = latestLogId()
        dispatchText(command)
        val commandRow = waitForRow(baseline, TEXT_CONFIRM_TIMEOUT_MILLIS) {
            it.userId == Configurable.botId && it.message == command
        }
        coordinator.onIncoming(
            GlmIncomingMessage(
                logId = commandRow.logId,
                chatId = CHAT_ID,
                userId = commandRow.userId,
                messageType = "1",
                message = command,
                threadId = null
            )
        )
        return waitForBotReply(commandRow.logId, timeoutMillis, expectedReply)
    }

    private suspend fun waitForBotReply(
        afterLogId: Long,
        timeoutMillis: Long,
        predicate: (String) -> Boolean
    ): CanaryAudioRow {
        val deadline = nowMillis() + timeoutMillis
        while (nowMillis() < deadline) {
            rowsAfter(afterLogId)
                .firstOrNull { it.userId == Configurable.botId && predicate(it.message) }
                ?.let { return it }
            delay(POLL_INTERVAL_MILLIS)
        }
        throw LiveAudioCanaryFailure("LIVE_AUDIO_CONTROL_DB_TIMEOUT")
    }

    private suspend fun waitForTerminalResult(afterLogId: Long): CanaryAudioRow {
        val deadline = nowMillis() + RESULT_TIMEOUT_MILLIS
        while (nowMillis() < deadline) {
            val rows = rowsAfter(afterLogId).filter { it.userId == Configurable.botId }
            rows.firstOrNull { it.message.startsWith(RESULT_PREFIX) }?.let { return it }
            rows.firstOrNull { row ->
                TERMINAL_FAILURE_PREFIXES.any { row.message.startsWith(it) }
            }?.let { row ->
                throw LiveAudioCanaryFailure(
                    "LIVE_AUDIO_SUMMARY_FAILED",
                    row.message
                )
            }
            // A previous one-shot process can finish dispatching its generic
            // failure reply after this run's command row. Such text has no
            // correlation identifier, so it must not terminate the current
            // canary. Only the expected result heading is a positive terminal
            // signal; timeout diagnostics use the scoped proxy job instead.
            delay(POLL_INTERVAL_MILLIS)
        }
        throw LiveAudioCanaryFailure("LIVE_AUDIO_RESULT_DB_TIMEOUT")
    }

    private suspend fun waitForRow(
        afterLogId: Long,
        timeoutMillis: Long,
        predicate: (CanaryAudioRow) -> Boolean
    ): CanaryAudioRow {
        val deadline = nowMillis() + timeoutMillis
        while (nowMillis() < deadline) {
            rowsAfter(afterLogId).firstOrNull(predicate)?.let { return it }
            delay(POLL_INTERVAL_MILLIS)
        }
        val code = if (timeoutMillis == AUDIO_CONFIRM_TIMEOUT_MILLIS) {
            "LIVE_AUDIO_FILE_DB_TIMEOUT"
        } else {
            "LIVE_AUDIO_COMMAND_DB_TIMEOUT"
        }
        throw LiveAudioCanaryFailure(code)
    }

    private fun latestLogId(): Long = db.connection.rawQuery(
        "SELECT COALESCE(MAX(_id), 0) FROM chat_logs",
        null
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else 0L }

    private fun latestBotAudioRow(): CanaryAudioRow? = db.connection.rawQuery(
        "SELECT _id, user_id, type, message, attachment, v FROM chat_logs " +
            "WHERE chat_id = ? AND user_id = ? AND type = ? ORDER BY _id DESC LIMIT 30",
        arrayOf(
            CHAT_ID.toString(),
            Configurable.botId.toString(),
            KakaoAudioAttachmentParser.FILE_MESSAGE_TYPE
        )
    ).use { cursor ->
        val idIndex = cursor.getColumnIndexOrThrow("_id")
        val userIndex = cursor.getColumnIndexOrThrow("user_id")
        val typeIndex = cursor.getColumnIndexOrThrow("type")
        val messageIndex = cursor.getColumnIndexOrThrow("message")
        val attachmentIndex = cursor.getColumnIndexOrThrow("attachment")
        val versionIndex = cursor.getColumnIndexOrThrow("v")
        while (cursor.moveToNext()) {
            val id = cursor.getLong(idIndex)
            val userId = cursor.getLong(userIndex)
            val version = runCatching { JSONObject(cursor.getString(versionIndex)) }.getOrNull()
            if (version?.optString("origin") in IGNORED_ORIGINS) continue
            val enc = version?.optInt("enc", 0) ?: 0
            val attachment = (KakaoAudioAttachmentParser().parse(
                sourceLogId = id,
                chatId = CHAT_ID,
                userId = userId,
                messageType = cursor.getString(typeIndex).orEmpty(),
                decryptedAttachment = decrypt(cursor.getString(attachmentIndex), enc, userId)
            ) as? AudioAttachmentParseResult.Parsed)?.attachment ?: continue
            return@use CanaryAudioRow(
                id,
                userId,
                decrypt(cursor.getString(messageIndex), enc, userId),
                attachment
            )
        }
        null
    }

    private fun rowsAfter(logId: Long): List<CanaryAudioRow> = db.connection.rawQuery(
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
                val attachmentJson = decrypt(cursor.getString(attachmentIndex), enc, userId)
                val attachment = (KakaoAudioAttachmentParser().parse(
                    sourceLogId = id,
                    chatId = CHAT_ID,
                    userId = userId,
                    messageType = type,
                    decryptedAttachment = attachmentJson
                ) as? AudioAttachmentParseResult.Parsed)?.attachment
                add(CanaryAudioRow(id, userId, message, attachment))
            }
        }
    }

    private fun decrypt(value: String?, enc: Int, userId: Long): String {
        if (value.isNullOrEmpty() || value == "{}") return value.orEmpty()
        return runCatching { KakaoDecrypt.decrypt(enc, value, userId) }.getOrDefault(value)
    }

    private class LiveAudioCanaryFailure(
        val code: String,
        val safeDetail: String? = null
    ) : IllegalStateException()

    private data class CanaryAudioRow(
        val logId: Long,
        val userId: Long,
        val message: String,
        val audioAttachment: IncomingAudioAttachment?
    )

    companion object {
        const val ARGUMENT = "--live-audio-canary"
        const val CONFIRM_ENV = "IRIS_LIVE_AUDIO_CANARY_CONFIRM"
        const val CONFIRM_VALUE = "SEND_R01_AUDIO_AND_ANALYZE"
        const val REUSE_ENV = "IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST"
        const val FORMAT_ENV = "IRIS_LIVE_AUDIO_CANARY_FORMAT"
        const val FILE_ENV = "IRIS_LIVE_AUDIO_CANARY_FILE"
        const val COMMAND_BASE64_ENV = "IRIS_LIVE_AUDIO_CANARY_COMMAND_B64"
        const val VERIFY_CONTROLS_ENV = "IRIS_LIVE_AUDIO_CANARY_VERIFY_CONTROLS"
        const val CANCEL_AFTER_START_ENV = "IRIS_LIVE_AUDIO_CANARY_CANCEL_AFTER_START"
        const val AUDIO_PATH = "/data/local/tmp/heybot-audio-canary.m4a"
        const val CHAT_ID = 18_480_337_854_645_134L
        const val COMMAND = "헤이봇 음성 요약"
        const val RESULT_PREFIX = "음성 요약 ·"
        private const val MAX_AUDIO_BYTES = 25L * 1024L * 1024L
        private const val MAX_RESULT_CHARS = 2_000
        private const val DISPATCH_TIMEOUT_MILLIS = 10_000L
        private const val AUDIO_CONFIRM_TIMEOUT_MILLIS = 120_000L
        private const val TEXT_CONFIRM_TIMEOUT_MILLIS = 30_000L
        private const val RESULT_TIMEOUT_MILLIS = 8 * 60_000L
        private const val POLL_INTERVAL_MILLIS = 250L
        private val IGNORED_ORIGINS = setOf("SYNCMSG", "MCHATLOGS")
        private val TERMINAL_FAILURE_PREFIXES = listOf(
            "음성 전사는 완료했지만 요약에 실패했어요.",
            "음성 요약 결과를 안전하게 표시할 수 없어요.",
            "음성 분석에 실패했어요.",
            "음성 분석 서버에 연결하지 못했어요."
        )
    }
}

@Serializable
data class LiveAudioCanaryReport(
    val status: String,
    val code: String,
    val sourceExtension: String? = null,
    val chatId: String? = null,
    val baselineLogId: Long? = null,
    val audioLogId: Long? = null,
    val commandLogId: Long? = null,
    val resultReplyLogId: Long? = null,
    val engine: String? = null,
    val summaryPattern: String? = null,
    val summaryView: String? = null,
    val durationMillis: Long? = null,
    val segmentCount: Int? = null,
    val transcriptText: String? = null,
    val resultText: String? = null,
    val controlsVerified: List<String> = emptyList(),
    val failureText: String? = null
)
