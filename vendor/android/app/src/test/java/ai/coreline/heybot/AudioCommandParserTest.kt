package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioCommandParserTest {
    private val parser = AudioCommandParser("헤이봇")

    @Test
    fun `parses default and explicit summary profiles`() {
        assertEquals(
            AudioCommand.Summarize(AudioSummaryProfile()),
            parser.parse("헤이봇 음성 요약")
        )
        assertEquals(
            AudioCommand.Summarize(
                AudioSummaryProfile(AudioSummaryPattern.MEETING, AudioSummaryView.MINUTES)
            ),
            parser.parse("헤이봇! 음성 요약 회의 회의록")
        )
    }

    @Test
    fun `parses control and bounded page commands`() {
        assertEquals(AudioCommand.Status, parser.parse("헤이봇 음성 상태"))
        assertEquals(AudioCommand.Resend, parser.parse("헤이봇 음성 재전송"))
        assertEquals(AudioCommand.Transcript(2), parser.parse("헤이봇 음성 원문 2"))
        assertEquals(AudioCommand.Evidence(3), parser.parse("헤이봇 음성 근거 3"))
        assertTrue(parser.parse("헤이봇 음성 원문 0") is AudioCommand.Invalid)
    }
}
