package ai.coreline.heybot

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GeneralConversationModeStoreTest {
    @Test
    fun `starts disabled and invalidates outstanding work after stop`() {
        val store = GeneralConversationModeStore()

        assertFalse(store.status().enabled)
        assertNull(store.snapshotIfEnabled())

        store.start()
        val snapshot = requireNotNull(store.snapshotIfEnabled())
        assertNotNull(snapshot)
        assertTrue(store.isCurrent(snapshot))

        store.stop()
        assertFalse(store.isCurrent(snapshot))
        assertFalse(store.status().enabled)
    }

    @Test
    fun `close returns the mode to safe disabled state`() {
        val store = GeneralConversationModeStore()
        store.start()
        store.close()

        assertFalse(store.status().enabled)
    }
}
