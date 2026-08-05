package ai.coreline.heybot

sealed interface YoutubeDownloadCommand {
    data class Download(val url: String) : YoutubeDownloadCommand
    data class Invalid(val reason: String) : YoutubeDownloadCommand
    data object Status : YoutubeDownloadCommand
    data object Cancel : YoutubeDownloadCommand
    data object Retry : YoutubeDownloadCommand
    data object Delete : YoutubeDownloadCommand
}

/** Strictly accepts one canonical YouTube video link; the server repeats the
 * validation before any downloader process is started. */
class YoutubeDownloadCommandParser(
    private val trigger: String,
    private val urlMaxChars: Int = 2_048
) {
    fun parse(raw: String): YoutubeDownloadCommand? {
        val message = raw.trim()
        if (!message.startsWith(trigger)) return null
        val remainder = message.removePrefix(trigger)
        if (remainder.isNotEmpty() && remainder.first() !in DELIMITERS) return null
        val content = remainder.trimStart(*DELIMITERS).trim()
        if (ROOM_CAPABILITY_COMMAND.matches(content)) return null
        return when (content) {
            "유튜브 상태" -> YoutubeDownloadCommand.Status
            "유튜브 취소" -> YoutubeDownloadCommand.Cancel
            "유튜브 재전송" -> YoutubeDownloadCommand.Retry
            "유튜브 삭제" -> YoutubeDownloadCommand.Delete
            "유튜브", "유튜브 다운로드" -> YoutubeDownloadCommand.Invalid(HELP)
            else -> {
                if (!content.startsWith(PREFIX)) return null
                val url = content.removePrefix(PREFIX).trim()
                when {
                    url.isBlank() -> YoutubeDownloadCommand.Invalid(HELP)
                    url.length > urlMaxChars -> YoutubeDownloadCommand.Invalid("유튜브 링크는 ${urlMaxChars}자 이내로 입력해주세요.")
                    canonicalVideoId(url) == null -> YoutubeDownloadCommand.Invalid("YouTube 단일 영상 링크만 지원해요. 재생목록·채널 링크는 사용할 수 없어요.")
                    else -> YoutubeDownloadCommand.Download(url)
                }
            }
        }
    }

    companion object {
        const val PREFIX = "유튜브 다운로드 "
        const val HELP = "‘헤이봇 유튜브 다운로드 <YouTube 링크>’처럼 입력해주세요."
        private val ROOM_CAPABILITY_COMMAND = Regex("^(?:방\\s+)?(?:텍스트|일반대화|이미지|영상|펜브러쉬|이미지분석|음성|음성자동|유튜브)\\s+(?:허용|불허용)\\s+R\\d{2}$")
        private val DELIMITERS = charArrayOf(' ', ',', '，', ':', '：', '!', '！', '?', '？', '~', '…', '.', '。')

        fun canonicalVideoId(raw: String): String? = runCatching {
            val url = java.net.URI(raw.trim())
            val scheme = url.scheme?.lowercase() ?: return null
            if (scheme != "https") return null
            val host = url.host?.lowercase()?.removePrefix("www.") ?: return null
            if (url.query.orEmpty().split('&').any { it.substringBefore('=') == "list" }) return null
            val path = url.path?.trim('/') ?: ""
            val query = url.query.orEmpty().split('&').mapNotNull {
                val (key, value) = it.split('=', limit = 2).let { pair -> pair[0] to pair.getOrNull(1) }
                if (key == "v") value else null
            }.firstOrNull()
            val id = when (host) {
                "youtu.be" -> path.substringBefore('/')
                "youtube.com", "m.youtube.com" -> when {
                    path == "watch" -> query
                    path.startsWith("shorts/") -> path.removePrefix("shorts/").substringBefore('/')
                    else -> null
                }
                else -> null
            }
            id?.takeIf { it.matches(Regex("^[A-Za-z0-9_-]{11}$")) }
        }.getOrNull()
    }
}
