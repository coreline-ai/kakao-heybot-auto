package ai.coreline.heybot

import org.junit.Assert.assertEquals
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
        assertFalse(store.status().persistenceConfigured)
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
    fun `persisted administrator intent survives store recreation`() {
        val backend = FakeBackend()
        val store = GeneralConversationModeStore(backend = backend, nowMillis = { 100L })
        store.start()
        assertEquals(
            """{"version":1,"enabled":true,"updatedAtMillis":100}""",
            backend.bytes?.toString(Charsets.UTF_8)
        )

        val restoredOn = GeneralConversationModeStore(backend = backend, nowMillis = { 200L })
        assertTrue(restoredOn.status().enabled)
        assertTrue(restoredOn.status().persistenceConfigured)
        assertEquals(true, restoredOn.status().lastPersistSucceeded)

        restoredOn.stop()
        val restoredOff = GeneralConversationModeStore(backend = backend, nowMillis = { 300L })
        assertFalse(restoredOff.status().enabled)
        assertEquals(true, restoredOff.status().lastPersistSucceeded)
    }

    @Test
    fun `close invalidates runtime work without erasing persisted on intent`() {
        val backend = FakeBackend()
        val store = GeneralConversationModeStore(backend = backend)
        store.start()
        val snapshot = requireNotNull(store.snapshotIfEnabled())

        store.close()

        assertFalse(store.status().enabled)
        assertFalse(store.isCurrent(snapshot))
        assertTrue(GeneralConversationModeStore(backend = backend).status().enabled)
    }

    @Test
    fun `failed on write keeps runtime disabled`() {
        val backend = FakeBackend(failWrite = true)
        val store = GeneralConversationModeStore(backend = backend)

        val status = store.start()

        assertFalse(status.enabled)
        assertEquals(false, status.lastPersistSucceeded)
        assertNull(store.snapshotIfEnabled())
    }

    @Test
    fun `failed off write quarantines stale on state for fail closed restart`() {
        val backend = FakeBackend()
        val store = GeneralConversationModeStore(backend = backend)
        assertTrue(store.start().enabled)
        backend.failWrite = true

        val stopped = store.stop()

        assertFalse(stopped.enabled)
        assertEquals(false, stopped.lastPersistSucceeded)
        assertEquals(1, backend.quarantineCount)
        backend.failWrite = false
        assertFalse(GeneralConversationModeStore(backend = backend).status().enabled)
    }

    @Test
    fun `corrupt unsupported and oversized documents are quarantined and disabled`() {
        listOf(
            ByteArray(0),
            "not-json".toByteArray(),
            """{"version":2,"enabled":true,"updatedAtMillis":1}""".toByteArray(),
            """{"version":1,"enabled":true,"updatedAtMillis":-1}""".toByteArray(),
            ByteArray(4 * 1024 + 1) { 'x'.code.toByte() }
        ).forEach { bytes ->
            val backend = FakeBackend(bytes = bytes)
            val store = GeneralConversationModeStore(backend = backend)

            assertFalse(store.status().enabled)
            assertEquals(false, store.status().lastPersistSucceeded)
            assertEquals(1, backend.quarantineCount)
        }
    }

    @Test
    fun `quarantine failure remains visible and runtime stays disabled`() {
        val logs = mutableListOf<String>()
        val backend = FakeBackend(
            bytes = """{"version":1,"enabled":true,"updatedAtMillis":1}""".toByteArray(),
            failWrite = true,
            failQuarantine = true
        )
        val store = GeneralConversationModeStore(backend = backend, log = logs::add)

        val status = store.stop()

        assertFalse(status.enabled)
        assertEquals(false, status.lastPersistSucceeded)
        assertTrue(logs.any { it.contains("quarantine failed") })
    }

    @Test
    fun `read failure starts disabled without exposing state`() {
        val logs = mutableListOf<String>()
        val backend = FakeBackend(failRead = true)
        val store = GeneralConversationModeStore(
            backend = backend,
            log = logs::add
        )

        assertFalse(store.status().enabled)
        assertEquals(false, store.status().lastPersistSucceeded)
        assertEquals(1, backend.quarantineCount)
        assertTrue(logs.any { it.contains("restore failed") })
    }

    private class FakeBackend(
        var bytes: ByteArray? = null,
        var failRead: Boolean = false,
        var failWrite: Boolean = false,
        var failQuarantine: Boolean = false
    ) : ConversationMemoryBackend {
        var quarantineCount = 0

        override fun read(): ByteArray? {
            if (failRead) error("read failed")
            return bytes
        }

        override fun write(bytes: ByteArray) {
            if (failWrite) error("write failed")
            this.bytes = bytes
        }

        override fun quarantine(nowMillis: Long) {
            if (failQuarantine) error("quarantine failed")
            quarantineCount += 1
            bytes = null
        }
    }
}
