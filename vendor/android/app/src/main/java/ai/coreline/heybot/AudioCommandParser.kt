package ai.coreline.heybot

enum class AudioSummaryPattern(val wireValue: String, val displayName: String) {
    AUTO("AUTO", "자동"),
    GENERAL("GENERAL", "일반"),
    MEETING("MEETING", "회의"),
    INTERVIEW("INTERVIEW", "인터뷰"),
    LECTURE("LECTURE", "강의"),
    CALL("CALL", "통화"),
    COUNSELING("COUNSELING", "상담"),
    BUSINESS_REPORT("BUSINESS_REPORT", "업무보고"),
    QNA("QNA", "질의응답");

    companion object {
        fun fromDisplay(value: String): AudioSummaryPattern? =
            entries.firstOrNull { it.displayName == value }
    }
}

enum class AudioSummaryView(val wireValue: String, val displayName: String) {
    BRIEF("BRIEF", "짧게"),
    DEFAULT("DEFAULT", "기본"),
    DETAIL("DETAIL", "상세"),
    ACTIONS("ACTIONS", "액션"),
    TIMELINE("TIMELINE", "타임라인"),
    MINUTES("MINUTES", "회의록");

    companion object {
        fun fromDisplay(value: String): AudioSummaryView? =
            entries.firstOrNull { it.displayName == value }
    }
}

data class AudioSummaryProfile(
    val pattern: AudioSummaryPattern = AudioSummaryPattern.AUTO,
    val view: AudioSummaryView = AudioSummaryView.DEFAULT
)

sealed interface AudioCommand {
    data class Summarize(val profile: AudioSummaryProfile) : AudioCommand
    data object Status : AudioCommand
    data object Cancel : AudioCommand
    data object Resummarize : AudioCommand
    data object Resend : AudioCommand
    data class Transcript(val page: Int) : AudioCommand
    data class Evidence(val page: Int) : AudioCommand
    data object Delete : AudioCommand
    data class Invalid(val reason: String) : AudioCommand
}

class AudioCommandParser(private val trigger: String) {
    fun parse(raw: String): AudioCommand? {
        val message = raw.trim()
        if (!message.startsWith(trigger)) return null
        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in DELIMITERS) return null
        return parseContent(remainder.trimStart(*DELIMITERS).trim())
    }

    fun parseContent(content: String): AudioCommand? {
        return when (content) {
            "음성 요약" -> AudioCommand.Summarize(AudioSummaryProfile())
            "음성 상태" -> AudioCommand.Status
            "음성 취소" -> AudioCommand.Cancel
            "음성 재요약" -> AudioCommand.Resummarize
            "음성 재전송" -> AudioCommand.Resend
            "음성 원문" -> AudioCommand.Transcript(1)
            "음성 근거" -> AudioCommand.Evidence(1)
            "음성 삭제" -> AudioCommand.Delete
            "음성" -> AudioCommand.Invalid(AUDIO_HELP)
            else -> parseSummary(content)
                ?: parsePage(content, "음성 원문", AudioCommand::Transcript)
                ?: parsePage(content, "음성 근거", AudioCommand::Evidence)
        }
    }

    private fun parseSummary(content: String): AudioCommand? {
        if (!content.startsWith("음성 요약 ")) return null
        val parts = content.removePrefix("음성 요약 ").trim()
            .split(Regex("\\s+")).filter(String::isNotBlank)
        if (parts.size !in 1..2) return AudioCommand.Invalid(AUDIO_HELP)
        val pattern = AudioSummaryPattern.fromDisplay(parts[0])
            ?: return AudioCommand.Invalid(AUDIO_HELP)
        val view = parts.getOrNull(1)?.let(AudioSummaryView::fromDisplay)
            ?: if (parts.size == 1) AudioSummaryView.DEFAULT else return AudioCommand.Invalid(AUDIO_HELP)
        if (pattern == AudioSummaryPattern.AUTO && view == AudioSummaryView.MINUTES) {
            return AudioCommand.Invalid("회의록은 ‘헤이봇 음성 요약 회의 회의록’처럼 유형을 회의로 지정해주세요.")
        }
        return AudioCommand.Summarize(AudioSummaryProfile(pattern, view))
    }

    private fun parsePage(
        content: String,
        prefix: String,
        create: (Int) -> AudioCommand
    ): AudioCommand? {
        if (!content.startsWith("$prefix ")) return null
        val page = content.removePrefix("$prefix ").trim().toIntOrNull()
        return if (page != null && page in 1..MAX_PAGE) create(page)
        else AudioCommand.Invalid("페이지는 1부터 ${MAX_PAGE}까지 입력해주세요.")
    }

    companion object {
        const val AUDIO_HELP =
            "‘헤이봇 음성 요약 [자동|일반|회의|인터뷰|강의|통화|상담|업무보고|질의응답] [짧게|기본|상세|액션|타임라인|회의록]’처럼 입력해주세요."
        private const val MAX_PAGE = 100
        private val DELIMITERS = charArrayOf(
            ' ', ',', '，', ':', '：', '!', '！', '?', '？', '~', '…', '.', '。'
        )
    }
}
