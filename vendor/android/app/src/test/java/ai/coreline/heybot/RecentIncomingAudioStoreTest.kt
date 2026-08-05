package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RecentIncomingAudioStoreTest {
    @Test
    fun `exact and recent selection are shared by room but isolated across rooms`() {
        var now = 1_000L
        val store = RecentIncomingAudioStore(retentionMillis = 120_000L, nowMillis = { now })
        store.put(audio(1, 10, 20, now + 500_000L))
        store.put(audio(2, 10, 20, now + 500_000L))
        store.put(audio(3, 10, 21, now + 500_000L))

        assertEquals(3L, store.findRecent(10, 0)?.sourceLogId)
        assertEquals(21L, store.findRecent(10, 0)?.userId)
        assertEquals(1L, store.findExact(10, 1)?.sourceLogId)
        assertNull(store.findRecent(11, 0))
        assertNull(store.findExact(11, 1))
        now += 120_001L
        assertNull(store.findRecent(10, 0))
    }

    private fun audio(log: Long, chat: Long, user: Long, expiry: Long) =
        IncomingAudioAttachment(
            log, chat, user, "https://talk.kakaocdn.net/fake/$log.m4a", 1_000L,
            expiry, "m4a"
        )
}
