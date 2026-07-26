package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplySafetyPolicyTest {
    private val policy = ReplySafetyPolicy()

    @Test
    fun `removes thinking and code fences and normalizes one line`() {
        val result = policy.apply(
            "<think>internal</think>\n```json\n안녕하세요.   반가워요.\n```"
        ) as ReplySafetyResult.Safe

        assertEquals("json 안녕하세요. 반가워요.", result.text)
        assertTrue(result.redactions.isEmpty())
    }

    @Test
    fun `blocks secret authorization private path and root credential forms`() {
        val inputs = listOf(
            "Authorization: Bearer abcdefghijklmnop",
            "api_key=abcdefghijk",
            "token: abcdefghijk",
            "IRIS_GLM_API_KEY_FILE=/tmp/key",
            "/data/local/private/iris-glm.token",
            "owner root:root",
            "password=super-secret-value"
        )
        inputs.forEach {
            assertEquals(
                ReplySafetyBlockReason.SECRET_LIKE,
                (policy.apply(it) as ReplySafetyResult.Blocked).reason
            )
        }
    }

    @Test
    fun `redacts PII with deterministic fixed labels`() {
        val result = policy.apply(
            "메일 test.user@example.com 전화 010-1234-5678 " +
                "주민 900101-1234567 카드 1234-5678-9012-3456"
        ) as ReplySafetyResult.Safe

        assertTrue(result.text.contains("[이메일 마스킹]"))
        assertTrue(result.text.contains("[전화번호 마스킹]"))
        assertTrue(result.text.contains("[주민번호 마스킹]"))
        assertTrue(result.text.contains("[카드번호 마스킹]"))
        assertFalse(result.text.contains("test.user@example.com"))
        assertFalse(result.text.contains("010-1234-5678"))
        assertEquals(
            setOf(
                ReplyRedaction.EMAIL,
                ReplyRedaction.PHONE,
                ReplyRedaction.RESIDENT_ID,
                ReplyRedaction.CARD
            ),
            result.redactions
        )
    }

    @Test
    fun `enforces empty and 480 character boundaries`() {
        assertEquals(
            ReplySafetyBlockReason.EMPTY,
            (policy.apply("<think>only hidden</think> ```") as ReplySafetyResult.Blocked).reason
        )
        val result = policy.apply("가".repeat(481)) as ReplySafetyResult.Safe
        assertEquals(480, result.text.length)
    }
}
