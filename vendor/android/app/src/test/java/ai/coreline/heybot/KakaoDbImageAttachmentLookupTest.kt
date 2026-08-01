package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class KakaoDbImageAttachmentLookupTest {
    private val now = 2_000_000_000_000L

    @Test
    fun `exact reply ignores recent window but still validates room and CDN expiry`() {
        val rows = listOf(row(logId = 7, createdAt = now - 86_400_000L))
        val lookup = lookup(rows)

        assertEquals(7L, lookup.findExact(10, 7)?.sourceLogId)
        assertNull(lookup.findExact(11, 7))
        assertNull(lookup(listOf(row(logId = 8, expiry = now - 1))).findExact(10, 8))
    }

    @Test
    fun `latest fallback requires same room user and configured time window`() {
        val rows = listOf(
            row(logId = 9, userId = 21, createdAt = now - 1_000L),
            row(logId = 8, createdAt = now - 601_000L),
            row(logId = 7, createdAt = now - 5_000L)
        )
        val lookup = lookup(rows)

        assertEquals(7L, lookup.findLatest(10, 20, now - 600_000L)?.sourceLogId)
        assertNull(lookup.findLatest(10, 22, now - 600_000L))
        assertNull(lookup.findLatest(11, 20, now - 600_000L))
    }

    @Test
    fun `allows same-account image through exact and recent paths and rejects unsafe rows`() {
        val botRow = row(logId = 1, userId = 999)
        val botLookup = lookup(listOf(botRow))
        assertEquals(1L, botLookup.findExact(10, 1)?.sourceLogId)
        assertEquals(1L, botLookup.findLatest(10, 999, now - 600_000L)?.sourceLogId)
        assertNull(lookup(listOf(row(logId = 2, origin = "SYNCMSG"))).findExact(10, 2))
        assertNull(lookup(listOf(row(logId = 3, version = "{}"))).findExact(10, 3))
    }

    private fun lookup(rows: List<KakaoImageLogRow>): KakaoDbImageAttachmentLookup {
        val source = object : KakaoImageLogSource {
            override fun findExact(chatId: Long, sourceLogId: Long): KakaoImageLogRow? =
                rows.firstOrNull { it.chatId == chatId && it.sourceLogId == sourceLogId }

            override fun findRecent(
                chatId: Long,
                userId: Long,
                limit: Int
            ): List<KakaoImageLogRow> = rows.take(limit)
        }
        return KakaoDbImageAttachmentLookup(
            source = source,
            parser = KakaoImageAttachmentParser(nowMillis = { now }),
            decryptAttachment = { _, encrypted, _ -> encrypted },
            log = {}
        )
    }

    private fun row(
        logId: Long,
        chatId: Long = 10,
        userId: Long = 20,
        createdAt: Long = now,
        expiry: Long = now + 3_600_000L,
        origin: String = "MSG",
        version: String = "{\"enc\":1,\"origin\":\"$origin\"}"
    ) = KakaoImageLogRow(
        sourceLogId = logId,
        chatId = chatId,
        userId = userId,
        messageType = "2",
        attachment =
            """{"url":"https://talk.kakaocdn.net/fake/$logId.png","w":100,"h":100,"s":1000,"expire":$expiry,"mt":"image/png"}""",
        version = version,
        createdAt = createdAt
    )
}
