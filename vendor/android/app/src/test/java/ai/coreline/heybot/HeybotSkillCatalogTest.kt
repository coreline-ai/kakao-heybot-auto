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
        assertFalse(help.contains("헤이봇 전체 기억 초기화"))

        val admin = HeybotSkillCatalog.adminHelpMessages().joinToString("\n")
        assertTrue(admin.contains("헤이봇 대화 코덱스"))
        assertTrue(admin.contains("헤이봇 방 적용 <코드>"))
        assertTrue(admin.contains("헤이봇 최근 진단"))
        assertTrue(HeybotSkillCatalog.userHelpMessages().all { it.length <= 480 })
        assertTrue(HeybotSkillCatalog.adminHelpMessages().all { it.length <= 480 })
    }
}
