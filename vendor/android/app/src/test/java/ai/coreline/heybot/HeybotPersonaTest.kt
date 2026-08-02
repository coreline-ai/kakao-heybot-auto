package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HeybotPersonaTest {
    @Test
    fun `wake word and general conversation share one persona core`() {
        val wake = HeybotPersona.wakeWordPrompt()
        val general = HeybotPersona.generalConversationPrompt()

        assertEquals(HeybotPersona.CORE_PROMPT, wake)
        assertTrue(general.startsWith(HeybotPersona.CORE_PROMPT))
        assertEquals(1, Regex(Regex.escape(HeybotPersona.CORE_PROMPT)).findAll(general).count())
        assertFalse(wake.contains("REPLY|WAIT|IGNORE"))
        assertTrue(general.contains("REPLY|WAIT|IGNORE"))
    }

    @Test
    fun `persona identity and version are stable`() {
        assertEquals("heybot-persona-v2", HeybotPersona.VERSION)
        assertTrue(HeybotPersona.CORE_PROMPT.contains("핑크 로봇"))
        assertTrue(HeybotPersona.CORE_PROMPT.contains("완료하지 않았다면 완료했다고 말하지 않으며"))
        assertTrue(HeybotPersona.CORE_PROMPT.contains("항상 헤이봇으로 행동"))
    }
}
