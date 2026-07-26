package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class GeneralConversationPolicyTest {
    @Test
    fun `separates global and room scoped exact blocks`() {
        val file = privateFile(
            """
            # global
            100
            1:200
            """.trimIndent()
        )
        val policy = GeneralConversationPolicy.load(settings(file)) { true }

        assertFalse(policy.allows(1L, 100L))
        assertFalse(policy.allows(2L, 100L))
        assertFalse(policy.allows(1L, 200L))
        assertTrue(policy.allows(2L, 200L))
        assertFalse(policy.allows(3L, 200L))
        assertEquals(GeneralConversationPolicyReason.READY, policy.status().reason)
    }

    @Test
    fun `malformed duplicate zero negative and overflow lines fail closed`() {
        val invalid = listOf(
            "100\n100",
            "0",
            "-1",
            "not-a-number",
            "1:0",
            "1:2:3",
            "999999999999999999999999"
        )
        invalid.forEach { content ->
            val policy = GeneralConversationPolicy.load(settings(privateFile(content))) { true }
            assertFalse(policy.status().ready)
            assertEquals(
                GeneralConversationPolicyReason.FILE_CONTENT_INVALID,
                policy.status().reason
            )
            assertFalse(policy.allows(1L, 300L))
        }
    }

    @Test
    fun `missing unreadable metadata and missing configuration are fail closed`() {
        assertEquals(
            GeneralConversationPolicyReason.NOT_CONFIGURED,
            GeneralConversationPolicy.load(null).status().reason
        )
        val missing = File.createTempFile("general-blocks", ".txt").apply { delete() }
        assertEquals(
            GeneralConversationPolicyReason.FILE_UNAVAILABLE,
            GeneralConversationPolicy.load(settings(missing)) { true }.status().reason
        )
        val invalidMetadata = privateFile("")
        assertEquals(
            GeneralConversationPolicyReason.FILE_METADATA_INVALID,
            GeneralConversationPolicy.load(settings(invalidMetadata)) { false }.status().reason
        )
    }

    private fun settings(file: File) = GeneralConversationSettings(
        allowedChatIds = setOf(1L, 2L),
        blockFile = file,
        modeFile = File("/tmp/iris-general-conversation-mode-test.json"),
        circuitWindowMillis = 300_000L,
        circuitFailureThreshold = 3
    )

    private fun privateFile(content: String): File =
        File.createTempFile("general-blocks", ".txt").apply {
            writeText(content)
            deleteOnExit()
        }
}
