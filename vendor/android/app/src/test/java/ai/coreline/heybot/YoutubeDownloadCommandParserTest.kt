package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class YoutubeDownloadCommandParserTest {
    private val parser = YoutubeDownloadCommandParser("헤이봇")

    @Test fun `accepts a single YouTube watch and short URL`() {
        assertEquals(
            YoutubeDownloadCommand.Download("https://youtu.be/AbCdEfGhI_1"),
            parser.parse("헤이봇 유튜브 다운로드 https://youtu.be/AbCdEfGhI_1")
        )
        assertTrue(parser.parse("헤이봇 유튜브 다운로드 https://www.youtube.com/shorts/AbCdEfGhI_1") is YoutubeDownloadCommand.Download)
    }

    @Test fun `rejects playlist and non YouTube URL`() {
        assertTrue(parser.parse("헤이봇 유튜브 다운로드 https://www.youtube.com/watch?v=AbCdEfGhI_1&list=PL1") is YoutubeDownloadCommand.Invalid)
        assertTrue(parser.parse("헤이봇 유튜브 다운로드 https://example.com/AbCdEfGhI_1") is YoutubeDownloadCommand.Invalid)
    }
}
