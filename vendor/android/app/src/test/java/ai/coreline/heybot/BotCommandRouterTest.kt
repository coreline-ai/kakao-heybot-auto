package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BotCommandRouterTest {
    private val router = BotCommandRouter("헤이봇")

    @Test
    fun `routes local commands before GLM questions`() {
        assertEquals(BotCommand.Help, router.route("헤이봇 도움말"))
        assertEquals(BotCommand.ListSkills, router.route("헤이봇 기능"))
        assertEquals(BotCommand.ShowSkill("이미지"), router.route("헤이봇 기능 이미지"))
        assertEquals(BotCommand.RecentDiagnostics(null), router.route("헤이봇 최근 진단"))
        assertEquals(BotCommand.RecentDiagnostics("R03"), router.route("헤이봇 최근 진단 r03"))
        assertEquals(BotCommand.Status, router.route("헤이봇: 상태"))
        assertEquals(BotCommand.ClearMyMemory, router.route("헤이봇 내 기억 초기화"))
        assertEquals(BotCommand.ShowSettings, router.route("헤이봇 설정 보기"))
        assertEquals(BotCommand.ClearAllMemory, router.route("헤이봇 전체 기억 초기화"))
        assertEquals(BotCommand.StartGeneralConversation, router.route("헤이봇 대화 시작"))
        assertEquals(BotCommand.GeneralConversationStatus, router.route("헤이봇 대화 상태"))
        assertEquals(BotCommand.StopGeneralConversation, router.route("헤이봇 대화 종료"))
        assertEquals(BotCommand.SetConversationEngine(ConversationEngine.GLM), router.route("헤이봇 대화 기본"))
        assertEquals(BotCommand.SetConversationEngine(ConversationEngine.CODEX), router.route("헤이봇 대화 코덱스"))
        assertEquals(BotCommand.SetConversationEngine(ConversationEngine.GROK), router.route("헤이봇 대화 그록"))
        assertEquals(BotCommand.SelfTest(SelfTestMode.QUICK), router.route("헤이봇 자체진단"))
        assertEquals(BotCommand.SelfTest(SelfTestMode.INTEGRATION), router.route("헤이봇 자체진단 통합"))
        assertEquals(BotCommand.SelfTest(SelfTestMode.DEVICE), router.route("헤이봇 자체진단 기기"))
        assertEquals(BotCommand.SelfTest(SelfTestMode.CANARY), router.route("헤이봇 자체진단 카나리"))
        assertEquals(BotCommand.ShowCurrentRoom, router.route("헤이봇 카톡방"))
        assertEquals(
            BotCommand.ClearUserMemory(123L),
            router.route("헤이봇 사용자 기억 초기화 123")
        )
        assertEquals(
            BotCommand.GenerateImage("분홍색 로봇"),
            router.route("헤이봇 이미지 분홍색 로봇")
        )
        assertEquals(BotCommand.ImageStatus, router.route("헤이봇 이미지 상태"))
        assertEquals(BotCommand.AnalyzeImage(VisionTask.DESCRIBE), router.route("헤이봇 이미지 분석"))
        assertEquals(BotCommand.AnalyzeImage(VisionTask.OCR), router.route("헤이봇 이미지 글자 추출"))
        assertEquals(BotCommand.AnalyzeImage(VisionTask.TRANSLATE_KO), router.route("헤이봇 이미지 글자 번역"))
        assertEquals(BotCommand.CancelImage, router.route("헤이봇 이미지 취소"))
        assertEquals(BotCommand.RetryImage, router.route("헤이봇 이미지 재전송"))
        assertEquals(
            BotCommand.InvalidLocalCommand("만들 이미지 내용을 함께 입력해주세요."),
            router.route("헤이봇 이미지 ")
        )
        assertEquals(
            BotCommand.GenerateVideo("웃으며 손을 흔드는 분홍 로봇"),
            router.route("헤이봇 영상 웃으며 손을 흔드는 분홍 로봇")
        )
        assertEquals(
            BotCommand.GenerateVideo("분홍 로봇"),
            router.route("헤이봇! 영상 분홍 로봇")
        )
        assertEquals(BotCommand.VideoStatus, router.route("헤이봇 영상 상태"))
        assertEquals(BotCommand.CancelVideo, router.route("헤이봇 영상 취소"))
        assertEquals(BotCommand.RetryVideo, router.route("헤이봇 영상 재전송"))
        assertEquals(
            BotCommand.InvalidLocalCommand("만들 영상 내용을 함께 입력해주세요."),
            router.route("헤이봇 영상 ")
        )
        assertEquals(
            BotCommand.GeneratePenBrush("웃으며 손을 흔드는 분홍 로봇"),
            router.route("헤이봇 펜브러쉬 웃으며 손을 흔드는 분홍 로봇")
        )
        assertEquals(BotCommand.PenBrushStatus, router.route("헤이봇 펜브러쉬 상태"))
        assertEquals(BotCommand.CancelPenBrush, router.route("헤이봇 펜브러쉬 취소"))
        assertEquals(BotCommand.RetryPenBrush, router.route("헤이봇 펜브러쉬 재전송"))
        assertEquals(
            BotCommand.InvalidLocalCommand("펜브러쉬로 만들 내용을 함께 입력해주세요."),
            router.route("헤이봇 펜브러쉬 ")
        )
    }

    @Test
    fun `routes regular content and any trigger mention to GLM`() {
        assertEquals(BotCommand.GlmQuestion("안녕"), router.route("헤이봇, 안녕"))
        assertEquals(BotCommand.GlmQuestion("헤이봇!"), router.route("헤이봇!"))
        assertEquals(BotCommand.GlmQuestion("너헤이봇이야?"), router.route("너헤이봇이야?"))
        assertEquals(BotCommand.GlmQuestion("헤이봇에게 안녕"), router.route("헤이봇에게 안녕"))
        assertEquals(
            BotCommand.GlmQuestion("도움말 알려줘, 헤이봇"),
            router.route("도움말 알려줘, 헤이봇")
        )
        assertNull(router.route("그냥 메시지"))
    }

    @Test
    fun `does not run local commands when the trigger is only mentioned mid sentence`() {
        assertEquals(
            BotCommand.GlmQuestion("도움말 좀 알려줘"),
            router.route("헤이봇 도움말 좀 알려줘")
        )
        assertEquals(
            BotCommand.GlmQuestion("누가 헤이봇 대화 시작 하래?"),
            router.route("누가 헤이봇 대화 시작 하래?")
        )
    }

    @Test
    fun `routes room capability commands only in their exact prefix form`() {
        assertEquals(BotCommand.ListRoomCapabilities, router.route("헤이봇 방 목록"))
        assertEquals(BotCommand.ShowRoomCapability("R02"), router.route("헤이봇 방 상태 R02"))
        assertEquals(
            BotCommand.PreviewRoomCapability("R02", RoomCapability.IMAGE, false),
            router.route("헤이봇 방 이미지 불허용 R02")
        )
        assertEquals(
            BotCommand.PreviewRoomCapability("R02", RoomCapability.VIDEO, true),
            router.route("헤이봇 방 영상 허용 R02")
        )
        assertEquals(
            BotCommand.PreviewRoomCapability("R01", RoomCapability.VIDEO, false),
            router.route("헤이봇 영상 불허용 R01")
        )
        assertEquals(
            BotCommand.PreviewRoomCapability("R02", RoomCapability.PEN_BRUSH, true),
            router.route("헤이봇 방 펜브러쉬 허용 R02")
        )
        assertEquals(
            BotCommand.PreviewRoomCapability("R01", RoomCapability.IMAGE_ANALYSIS, true),
            router.route("헤이봇 이미지분석 허용 R01")
        )
        assertEquals(BotCommand.ApplyRoomCapability("ABC123"), router.route("헤이봇 방 적용 ABC123"))
        assertEquals(BotCommand.CancelRoomCapability, router.route("헤이봇 방 취소"))
        assertTrue(router.route("누가 헤이봇 방 목록을 보래?") is BotCommand.GlmQuestion)
    }

    @Test
    fun `rejects malformed target user IDs locally`() {
        assertTrue(
            router.route("헤이봇 사용자 기억 초기화 abc") is BotCommand.InvalidLocalCommand
        )
    }
}
