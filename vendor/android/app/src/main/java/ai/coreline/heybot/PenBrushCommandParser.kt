package ai.coreline.heybot

sealed interface PenBrushCommand {
    data class Generate(val prompt: String) : PenBrushCommand
    data class Invalid(val reason: String) : PenBrushCommand
    data object Status : PenBrushCommand
    data object Cancel : PenBrushCommand
    data object Retry : PenBrushCommand
}

class PenBrushCommandParser(
    private val trigger: String,
    private val promptMaxChars: Int
) {
    fun parse(raw: String): PenBrushCommand? {
        val message = raw.trim()
        if (!message.startsWith(trigger)) return null
        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in DELIMITERS) return null
        val content = remainder.trimStart(*DELIMITERS).trim()
        // 권한 변경 문장을 생성 요청으로 오인하지 않는다.
        if (ROOM_CAPABILITY_COMMAND.matches(content)) return null
        return when (content) {
            "펜브러쉬 상태" -> PenBrushCommand.Status
            "펜브러쉬 취소" -> PenBrushCommand.Cancel
            "펜브러쉬 재전송" -> PenBrushCommand.Retry
            "펜브러쉬", "펜브러쉬 영상" -> PenBrushCommand.Invalid("펜브러쉬로 만들 내용을 함께 입력해주세요.")
            else -> {
                if (!content.startsWith(PREFIX)) return null
                val prompt = content.removePrefix(PREFIX).trim()
                when {
                    prompt.isBlank() -> PenBrushCommand.Invalid("펜브러쉬로 만들 내용을 함께 입력해주세요.")
                    prompt.length > promptMaxChars -> PenBrushCommand.Invalid("펜브러쉬 설명은 ${promptMaxChars}자 이내로 입력해주세요.")
                    else -> PenBrushCommand.Generate(prompt)
                }
            }
        }
    }
    private companion object {
        const val PREFIX = "펜브러쉬 "
        val ROOM_CAPABILITY_COMMAND = Regex(
            "^(?:방\\s+)?(?:텍스트|일반대화|이미지|영상|펜브러쉬)\\s+(?:허용|불허용)\\s+R\\d{2}$"
        )
        val DELIMITERS = charArrayOf(' ', ',', '，', ':', '：', '!', '！', '?', '？', '~', '…', '.', '。')
    }
}
