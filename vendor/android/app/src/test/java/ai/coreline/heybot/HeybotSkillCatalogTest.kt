package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HeybotSkillCatalogTest {
    @Test
    fun `skill identifiers aliases and examples are unique and complete`() {
        val ids = HeybotSkillCatalog.skills.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        assertTrue(ids.all { it.matches(Regex("[a-z][a-z0-9_.]+")) })
        assertTrue(HeybotSkillCatalog.skills.all { it.examples.isNotEmpty() })
        assertNotNull(HeybotSkillCatalog.find("이미지"))
        assertNotNull(HeybotSkillCatalog.find("펜 브러쉬"))
    }

    @Test
    fun `ordinary help does not expose administrator commands`() {
        val help = HeybotSkillCatalog.userHelpMessages().joinToString("\n")
        assertTrue(help.contains("헤이봇 기능"))
        assertTrue(help.contains("헤이봇 이미지 <설명>"))
        assertTrue(help.contains("자동·일반·회의·인터뷰·강의·통화·상담·업무보고·질의응답"))
        assertTrue(help.contains("헤이봇 음성 재요약"))
        assertTrue(help.contains("헤이봇 음성 재전송"))
        assertTrue(help.contains("30분 동안 바로 이어 질문"))
        assertTrue(help.contains("같은 방에 최근 30분 안에 올라온 최신 음성"))
        assertFalse(help.contains("헤이봇 전체 기억 초기화"))

        val admin = HeybotSkillCatalog.adminHelpMessages().joinToString("\n")
        assertTrue(admin.contains("헤이봇 대화 코덱스"))
        assertTrue(admin.contains("헤이봇 방 적용 <코드>"))
        assertTrue(admin.contains("헤이봇 최근 진단"))
        assertTrue(admin.contains("헤이봇 음성자동 허용|불허용 <R번호>"))
        assertTrue(HeybotSkillCatalog.userHelpMessages().all { it.length <= 480 })
        assertTrue(HeybotSkillCatalog.adminHelpMessages().all { it.length <= 480 })
    }
}
