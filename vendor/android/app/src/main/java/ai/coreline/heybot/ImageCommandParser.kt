package ai.coreline.heybot

sealed interface ImageCommand {
    data class Generate(val prompt: String) : ImageCommand
    data class Invalid(val reason: String) : ImageCommand
    data object Status : ImageCommand
    data object Cancel : ImageCommand
    data object Retry : ImageCommand
}

class ImageCommandParser(
    private val trigger: String,
    private val promptMaxChars: Int
) {
    fun parse(raw: String): ImageCommand? {
        val message = raw.trim()
        if (!message.startsWith(trigger)) return null
        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in DELIMITERS) return null
        val content = remainder.trimStart(*DELIMITERS).trim()
        // 권한 변경 문장을 이미지 생성 프롬프트로 오인하지 않는다.
        if (ROOM_CAPABILITY_COMMAND.matches(content)) return null
        return when (content) {
            "이미지 상태" -> ImageCommand.Status
            "이미지 취소" -> ImageCommand.Cancel
            "이미지 재전송" -> ImageCommand.Retry
            "이미지" -> ImageCommand.Invalid("만들 이미지 내용을 함께 입력해주세요.")
            else -> {
                if (!content.startsWith(IMAGE_PREFIX)) return null
                val prompt = content.removePrefix(IMAGE_PREFIX).trim()
                when {
                    prompt.isBlank() ->
                        ImageCommand.Invalid("만들 이미지 내용을 함께 입력해주세요.")
                    prompt.length > promptMaxChars ->
                        ImageCommand.Invalid("이미지 설명은 ${promptMaxChars}자 이내로 입력해주세요.")
                    else -> ImageCommand.Generate(prompt)
                }
            }
        }
    }

    private companion object {
        const val IMAGE_PREFIX = "이미지 "
        val ROOM_CAPABILITY_COMMAND = Regex(
            "^(?:방\\s+)?(?:텍스트|일반대화|이미지|영상|펜브러쉬)\\s+(?:허용|불허용)\\s+R\\d{2}$"
        )
        val DELIMITERS = charArrayOf(' ', ',', '，', ':', '：')
    }
}
