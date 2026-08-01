package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RecentIncomingImageStoreTest {
    @Test
    fun `separates exact room reference from same-user recent selection`() {
        var now = 1_000L
        val store = RecentIncomingImageStore(retentionMillis = 120_000L, nowMillis = { now })
        store.put(image(1, 10, 20, now + 500_000))
        store.put(image(2, 10, 20, now + 500_000))
        store.put(image(3, 10, 21, now + 500_000))

        assertEquals(2L, store.findRecent(10, 20, 0)?.sourceLogId)
        assertEquals(1L, store.findExact(10, 1)?.sourceLogId)
        assertNull(store.findRecent(11, 20, 0))
        assertEquals(3L, store.findExact(10, 3)?.sourceLogId)
        assertNull(store.findExact(11, 3))
        assertNull(store.findRecent(10, 20, now + 1))
        now += 120_001L
        assertNull(store.findRecent(10, 20, 0))
        assertNull(store.findExact(10, 1))
    }

    private fun image(log: Long, chat: Long, user: Long, expiry: Long) =
        IncomingImageAttachment(
            log, chat, user, "https://talk.kakaocdn.net/fake/$log.png", null,
            100, 100, 1_000, expiry, "image/png"
        )
}
