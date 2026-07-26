package ai.coreline.heybot.model

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class ReplyTypeTest {
    @Test
    fun `video reply type decodes from the HTTP payload value`() {
        assertEquals(ReplyType.VIDEO, Json.decodeFromString<ReplyType>("\"video\""))
    }
}
