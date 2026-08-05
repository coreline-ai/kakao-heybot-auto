package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class KakaoDbAudioAttachmentLookupTest {
    private val now = 2_000_000_000_000L

    @Test
    fun `fallback accepts any sender in the same room and rejects blocked origins`() {
        val lookup = lookup(listOf(row(2, userId = 21), row(3, origin = "SYNCMSG"), row(1)))
        assertEquals(1L, lookup.findExact(10, 1)?.sourceLogId)
        assertEquals(2L, lookup.findExact(10, 2)?.sourceLogId)
        assertNull(lookup.findExact(11, 2))
        assertNull(lookup.findExact(10, 3))
        assertEquals(2L, lookup.findLatest(10, now - 60_000L)?.sourceLogId)
    }

    private fun lookup(rows: List<KakaoAudioLogRow>): KakaoDbAudioAttachmentLookup {
        val source = object : KakaoAudioLogSource {
            override fun findExact(chatId: Long, sourceLogId: Long) =
                rows.firstOrNull { it.chatId == chatId && it.sourceLogId == sourceLogId }
            override fun findRecent(chatId: Long, limit: Int) =
                rows.filter { it.chatId == chatId }.take(limit)
        }
        return KakaoDbAudioAttachmentLookup(
            source,
            KakaoAudioAttachmentParser { now },
            decryptAttachment = { _, value, _ -> value },
            log = {}
        )
    }

    private fun row(
        logId: Long,
        userId: Long = 20,
        origin: String = "MSG"
    ) = KakaoAudioLogRow(
        logId, 10, userId, "18",
        """{"cs":"x","expire":${now + 60_000},"k":"x","name":"fixture.m4a","s":1000,"size":1000,"url":"https://talk.kakaocdn.net/fake/$logId"}""",
        """{"enc":1,"origin":"$origin"}""",
        now
    )
}
