package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ImageCommandParserTest {
    private val parser = ImageCommandParser("헤이봇", 100)

    @Test
    fun `parses generate status and cancel commands`() {
        assertEquals(
            ImageCommand.Generate("분홍색 로봇"),
            parser.parse("헤이봇 이미지 분홍색 로봇")
        )
        assertEquals(ImageCommand.Status, parser.parse("헤이봇: 이미지 상태"))
        assertEquals(ImageCommand.Cancel, parser.parse("헤이봇 이미지 취소"))
        assertEquals(ImageCommand.Retry, parser.parse("헤이봇 이미지 재전송"))
    }

    @Test
    fun `rejects non command and explains invalid prompts`() {
        assertNull(parser.parse("그냥 메시지"))
        assertEquals(
            ImageCommand.Invalid("만들 이미지 내용을 함께 입력해주세요."),
            parser.parse("헤이봇 이미지 ")
        )
        assertEquals(
            ImageCommand.Invalid("이미지 설명은 100자 이내로 입력해주세요."),
            parser.parse("헤이봇 이미지 " + "가".repeat(101))
        )
    }
}
