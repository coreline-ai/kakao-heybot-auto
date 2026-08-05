package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MultipartTextDeliveryTest {
    @Test
    fun `splits long Korean text without breaking surrogate pairs or Kakao limit`() {
        val parts = MultipartTextDelivery.split(("회의 결과😀 다음 단계입니다. ").repeat(120))
        assertTrue(parts.size in 2..8)
        assertTrue(parts.all { it.length <= MultipartTextDelivery.MAX_PART_CHARS })
        parts.forEach { part ->
            part.forEachIndexed { index, char ->
                if (Character.isHighSurrogate(char)) {
                    assertTrue(index + 1 < part.length && Character.isLowSurrogate(part[index + 1]))
                }
            }
        }
    }

    @Test
    fun `resend preserves part identity and Kakao bound`() {
        val resent = MultipartTextDelivery.resend("[2/3] " + "😀가".repeat(300))
        assertTrue(resent.startsWith("[2/3·재전송] "))
        assertTrue(resent.length <= MultipartTextDelivery.MAX_PART_CHARS)
        resent.forEachIndexed { index, char ->
            if (Character.isHighSurrogate(char)) {
                assertTrue(index + 1 < resent.length && Character.isLowSurrogate(resent[index + 1]))
            }
        }
    }

    @Test
    fun `short text remains a single unnumbered part`() {
        assertEquals(listOf("짧은 결과"), MultipartTextDelivery.split(" 짧은 결과 "))
    }
}
