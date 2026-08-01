package ai.coreline.heybot

sealed interface BotCommand {
    data class GlmQuestion(val question: String) : BotCommand
    data object Help : BotCommand
    data object Status : BotCommand
    data object ClearMyMemory : BotCommand
    data object ShowSettings : BotCommand
    data object ClearAllMemory : BotCommand
    data class ClearUserMemory(val targetUserId: Long) : BotCommand
    data object StartGeneralConversation : BotCommand
    data object GeneralConversationStatus : BotCommand
    data object StopGeneralConversation : BotCommand
    data class SetConversationEngine(val engine: ConversationEngine) : BotCommand
    data class SelfTest(val mode: SelfTestMode) : BotCommand
    data object ShowCurrentRoom : BotCommand
    data object ListRoomCapabilities : BotCommand
    data class ShowRoomCapability(val reference: String) : BotCommand
    data class PreviewRoomCapability(
        val reference: String,
        val capability: RoomCapability,
        val enabled: Boolean
    ) : BotCommand
    data class ApplyRoomCapability(val nonce: String) : BotCommand
    data object CancelRoomCapability : BotCommand
    data class GenerateImage(val prompt: String) : BotCommand
    data object AnalyzeImage : BotCommand
    data object ImageStatus : BotCommand
    data object CancelImage : BotCommand
    data object RetryImage : BotCommand
    data class GenerateVideo(val prompt: String) : BotCommand
    data object VideoStatus : BotCommand
    data object CancelVideo : BotCommand
    data object RetryVideo : BotCommand
    data class GeneratePenBrush(val prompt: String) : BotCommand
    data object PenBrushStatus : BotCommand
    data object CancelPenBrush : BotCommand
    data object RetryPenBrush : BotCommand
    data class InvalidLocalCommand(val reason: String) : BotCommand
}

/**
 * Parses only commands that begin with the configured trigger. Local control
 * commands are resolved before a message is admitted to the GLM queue.
 */
class BotCommandRouter(private val trigger: String) {
    fun route(rawMessage: String): BotCommand? {
        val message = rawMessage.trim()
        val content = extractLeadingTriggeredContent(message)
        if (content == null) {
            // A mention anywhere in the sentence is an explicit call, but
            // administrative and image commands stay prefix-only below.
            return message.takeIf { it.contains(trigger) }?.let(BotCommand::GlmQuestion)
        }
        if (content.isBlank()) return BotCommand.GlmQuestion(message)

        return when (content) {
            "도움말" -> BotCommand.Help
            "상태" -> BotCommand.Status
            "내 기억 초기화" -> BotCommand.ClearMyMemory
            "설정 보기" -> BotCommand.ShowSettings
            "전체 기억 초기화" -> BotCommand.ClearAllMemory
            "대화 시작" -> BotCommand.StartGeneralConversation
            "대화 상태" -> BotCommand.GeneralConversationStatus
            "대화 종료" -> BotCommand.StopGeneralConversation
            "대화 기본" -> BotCommand.SetConversationEngine(ConversationEngine.GLM)
            "대화 코덱스" -> BotCommand.SetConversationEngine(ConversationEngine.CODEX)
            "대화 그록" -> BotCommand.SetConversationEngine(ConversationEngine.GROK)
            "자체진단" -> BotCommand.SelfTest(SelfTestMode.QUICK)
            "자체진단 빠른" -> BotCommand.SelfTest(SelfTestMode.QUICK)
            "자체진단 통합" -> BotCommand.SelfTest(SelfTestMode.INTEGRATION)
            "자체진단 기기" -> BotCommand.SelfTest(SelfTestMode.DEVICE)
            "자체진단 카나리" -> BotCommand.SelfTest(SelfTestMode.CANARY)
            "카톡방" -> BotCommand.ShowCurrentRoom
            "방 목록" -> BotCommand.ListRoomCapabilities
            "방 취소" -> BotCommand.CancelRoomCapability
            "이미지 상태" -> BotCommand.ImageStatus
            "이미지 분석" -> BotCommand.AnalyzeImage
            "이미지 취소" -> BotCommand.CancelImage
            "이미지 재전송" -> BotCommand.RetryImage
            "영상 상태" -> BotCommand.VideoStatus
            "영상 취소" -> BotCommand.CancelVideo
            "영상 재전송" -> BotCommand.RetryVideo
            "펜브러쉬 상태" -> BotCommand.PenBrushStatus
            "펜브러쉬 취소" -> BotCommand.CancelPenBrush
            "펜브러쉬 재전송" -> BotCommand.RetryPenBrush
            "영상" -> BotCommand.InvalidLocalCommand("만들 영상 내용을 함께 입력해주세요.")
            "이미지" -> BotCommand.InvalidLocalCommand("만들 이미지 내용을 함께 입력해주세요.")
            "펜브러쉬", "펜브러쉬 영상" ->
                BotCommand.InvalidLocalCommand("펜브러쉬로 만들 내용을 함께 입력해주세요.")
            else -> parseRoomCommand(content)
                ?: parseClearUserMemory(content)
                ?: BotCommand.GlmQuestion(content)
        }
            .let { command ->
                if (command is BotCommand.GlmQuestion && content.startsWith(IMAGE_PREFIX)) {
                    val prompt = content.removePrefix(IMAGE_PREFIX).trim()
                    if (prompt.isBlank()) {
                        BotCommand.InvalidLocalCommand("만들 이미지 내용을 함께 입력해주세요.")
                    } else {
                        BotCommand.GenerateImage(prompt)
                    }
                } else if (command is BotCommand.GlmQuestion && content.startsWith(VIDEO_PREFIX)) {
                    val prompt = content.removePrefix(VIDEO_PREFIX).trim()
                    if (prompt.isBlank()) {
                        BotCommand.InvalidLocalCommand("만들 영상 내용을 함께 입력해주세요.")
                    } else {
                        BotCommand.GenerateVideo(prompt)
                    }
                } else if (command is BotCommand.GlmQuestion && content.startsWith(PEN_BRUSH_PREFIX)) {
                    val prompt = content.removePrefix(PEN_BRUSH_PREFIX).trim()
                    if (prompt.isBlank()) {
                        BotCommand.InvalidLocalCommand("펜브러쉬로 만들 내용을 함께 입력해주세요.")
                    } else {
                        BotCommand.GeneratePenBrush(prompt)
                    }
                } else {
                    command
                }
            }
    }

    private fun extractLeadingTriggeredContent(message: String): String? {
        if (!message.startsWith(trigger)) return null

        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in TRIGGER_DELIMITERS) {
            return null
        }

        return remainder.trimStart(*TRIGGER_DELIMITERS).trim()
    }

    private fun parseClearUserMemory(content: String): BotCommand? {
        if (!content.startsWith(CLEAR_USER_PREFIX)) return null

        val rawUserId = content.removePrefix(CLEAR_USER_PREFIX).trim()
        val userId = rawUserId.toLongOrNull()
        return if (userId != null && userId > 0L) {
            BotCommand.ClearUserMemory(userId)
        } else {
            BotCommand.InvalidLocalCommand("사용자 ID는 양의 숫자로 입력해주세요.")
        }
    }

    private fun parseRoomCommand(content: String): BotCommand? {
        val parts = content.split(Regex("\\s+")).filter(String::isNotBlank)
        if (parts.isEmpty()) return null
        if (parts.first() != "방") {
            // R번호를 확인한 뒤에는 "방"을 생략한 짧은 운영 명령도 허용한다.
            // 예: 헤이봇 영상 불허용 R01
            if (parts.size != 3) return null
            val capability = RoomCapability.entries.firstOrNull { it.commandName == parts[0] }
                ?: return null
            val enabled = when (parts[1]) {
                "허용" -> true
                "불허용" -> false
                else -> return null
            }
            return BotCommand.PreviewRoomCapability(parts[2], capability, enabled)
        }
        if (parts.size == 3 && parts[1] == "상태") {
            return BotCommand.ShowRoomCapability(parts[2])
        }
        if (parts.size == 3 && parts[1] == "적용") {
            return BotCommand.ApplyRoomCapability(parts[2])
        }
        if (parts.size == 4) {
            val capability = RoomCapability.entries.firstOrNull { it.commandName == parts[1] }
                ?: return BotCommand.InvalidLocalCommand(ROOM_HELP)
            val enabled = when (parts[2]) {
                "허용" -> true
                "불허용" -> false
                else -> return BotCommand.InvalidLocalCommand(ROOM_HELP)
            }
            return BotCommand.PreviewRoomCapability(parts[3], capability, enabled)
        }
        return BotCommand.InvalidLocalCommand(ROOM_HELP)
    }

    private companion object {
        const val CLEAR_USER_PREFIX = "사용자 기억 초기화"
        const val IMAGE_PREFIX = "이미지 "
        const val VIDEO_PREFIX = "영상 "
        const val PEN_BRUSH_PREFIX = "펜브러쉬 "
        const val ROOM_HELP = "먼저 ‘헤이봇 카톡방’으로 방 이름과 R번호를 확인해주세요. 이후 ‘헤이봇 방 상태 R번호’ 또는 ‘헤이봇 영상 불허용 R01’처럼 입력할 수 있어요."
        val TRIGGER_DELIMITERS = charArrayOf(
            ' ', ',', '，', ':', '：',
            '!', '！', '?', '？', '~', '…', '.', '。'
        )
    }
}
