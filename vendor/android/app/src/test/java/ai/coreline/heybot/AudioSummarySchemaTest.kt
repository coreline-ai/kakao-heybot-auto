package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioSummarySchemaTest {
    private val profile = AudioSummaryProfile(AudioSummaryPattern.MEETING, AudioSummaryView.MINUTES)
    private val ids = setOf("S0001", "S0002")

    @Test
    fun `accepts only claims grounded in the current transcript`() {
        val document = AudioSummarySchema.decodeSummary(
            """{"version":1,"pattern":"MEETING","view":"MINUTES","title":"회의","oneLine":"결정을 확인합니다.","oneLineEvidence":["S0001"],"keyPoints":[{"text":"논의","evidence":["S0001"]}],"decisions":[{"text":"결정","evidence":["S0002"]}],"actionItems":[{"text":"후속 확인","owner":null,"dueAt":null,"evidence":["S0002"]}],"openQuestions":[],"warnings":[]}""",
            profile,
            ids
        )
        assertEquals("회의", document.title)
        assertTrue(AudioSummaryRenderer.render(document).contains("[S0002]"))
    }

    @Test
    fun `rejects unknown keys invalid evidence and markdown fences`() {
        val valid = """{"version":1,"pattern":"MEETING","view":"MINUTES","title":"회의","oneLine":"요약","oneLineEvidence":["S0001"],"keyPoints":[{"text":"논의","evidence":["S0001"]}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}"""
        fun invalid(value: String) = runCatching {
            AudioSummarySchema.decodeSummary(value, profile, ids)
        }.exceptionOrNull()?.message == "SUMMARY_OUTPUT_INVALID"
        assertTrue(invalid(valid.dropLast(1) + ",\"extra\":true}"))
        assertTrue(invalid(valid.replace("S0001", "S9999")))
        assertTrue(invalid("```json\n$valid\n```"))
    }
}
