package ai.coreline.heybot

sealed interface VideoCommand {
    data class Generate(val prompt: String) : VideoCommand
    data class Invalid(val reason: String) : VideoCommand
    data object Status : VideoCommand
    data object Cancel : VideoCommand
    data object Retry : VideoCommand
}

class VideoCommandParser(
    private val trigger: String,
    private val promptMaxChars: Int
) {
    fun parse(raw: String): VideoCommand? {
        val message = raw.trim()
        if (!message.startsWith(trigger)) return null
        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in DELIMITERS) return null
        val content = remainder.trimStart(*DELIMITERS).trim()
        // `헤이봇 영상 허용|불허용 R01`은 생성 프롬프트가 아니라 코어라인의
        // 방 권한 명령이다. 동일 메시지를 받는 BotCommandRouter가 처리하게 둔다.
        if (ROOM_CAPABILITY_COMMAND.matches(content)) return null
        return when (content) {
            "영상 상태" -> VideoCommand.Status
            "영상 취소" -> VideoCommand.Cancel
            "영상 재전송" -> VideoCommand.Retry
            "영상" -> VideoCommand.Invalid("만들 영상 내용을 함께 입력해주세요.")
            else -> {
                if (!content.startsWith(VIDEO_PREFIX)) return null
                val prompt = content.removePrefix(VIDEO_PREFIX).trim()
                when {
                    prompt.isBlank() ->
                        VideoCommand.Invalid("만들 영상 내용을 함께 입력해주세요.")
                    prompt.length > promptMaxChars ->
                        VideoCommand.Invalid("영상 설명은 ${promptMaxChars}자 이내로 입력해주세요.")
                    else -> VideoCommand.Generate(prompt)
                }
            }
        }
    }

    private companion object {
        const val VIDEO_PREFIX = "영상 "
        val ROOM_CAPABILITY_COMMAND = Regex(
            "^(?:방\\s+)?(?:텍스트|일반대화|이미지|영상|펜브러쉬)\\s+(?:허용|불허용)\\s+R\\d{2}$"
        )
        val DELIMITERS = charArrayOf(
            ' ', ',', '，', ':', '：',
            '!', '！', '?', '？', '~', '…', '.', '。'
        )
    }
}
