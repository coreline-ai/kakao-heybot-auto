package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.file.Files

class ConversationEngineModeStoreTest {
    @Test
    fun `defaults to GLM and persists an engine selection`() {
        val directory = Files.createTempDirectory("heybot-engine-mode").toFile()
        val file = directory.resolve("engine.conf")
        val first = ConversationEngineModeStore(file = file, nowMillis = { 1234L })
        assertEquals(ConversationEngine.GLM, first.snapshot().engine)
        assertEquals(ConversationEngine.CODEX, first.set(ConversationEngine.CODEX).engine)

        val restored = ConversationEngineModeStore(file = file)
        assertEquals(ConversationEngine.CODEX, restored.snapshot().engine)
    }

    @Test
    fun `corrupt mode file fails closed to GLM`() {
        val directory = Files.createTempDirectory("heybot-engine-corrupt").toFile()
        val file = directory.resolve("engine.conf")
        file.writeText("schemaVersion=1\nengine=GROK\n")
        assertEquals(ConversationEngine.GROK, ConversationEngineModeStore(file).snapshot().engine)
        file.writeText("engine=UNKNOWN\n")
        assertEquals(ConversationEngine.GLM, ConversationEngineModeStore(file).snapshot().engine)
    }
}
