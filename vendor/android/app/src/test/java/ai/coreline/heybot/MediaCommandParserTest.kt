package ai.coreline.heybot

import org.junit.Assert.assertNull
import org.junit.Test

class MediaCommandParserTest {
    @Test
    fun `room capability aliases never become media generation prompts`() {
        assertNull(VideoCommandParser("헤이봇", 100).parse("헤이봇 영상 불허용 R01"))
        assertNull(PenBrushCommandParser("헤이봇", 100).parse("헤이봇 펜브러쉬 허용 R01"))
        assertNull(ImageCommandParser("헤이봇", 100).parse("헤이봇 이미지 불허용 R01"))
    }
}
