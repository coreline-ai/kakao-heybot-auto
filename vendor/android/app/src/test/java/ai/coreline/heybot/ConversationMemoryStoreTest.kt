package ai.coreline.heybot

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationMemoryStoreTest {
    @Test
    fun `persists IDs as strings and restores separated user histories`() = runBlocking {
        val backend = FakeBackend()
        val first = store(backend)
        first.initialize()
        first.append(KEY_A, turn("A 질문", 1_000L))
        first.append(KEY_B, turn("B 질문", 1_100L))

        val encoded = backend.bytes!!.toString(Charsets.UTF_8)
        assertTrue(encoded.contains("\"chatId\":\"100\""))
        assertTrue(encoded.contains("\"userId\":\"200\""))

        val restored = store(backend)
        restored.initialize()

        assertEquals(listOf("A 질문"), restored.history(KEY_A, 1_200L).map { it.userMessage })
        assertEquals(listOf("B 질문"), restored.history(KEY_B, 1_200L).map { it.userMessage })
    }

    @Test
    fun `keeps only recent configured turns and expires old context`() = runBlocking {
        val store = store(FakeBackend(), maxTurns = 2, ttl = 1_000L)
        store.initialize()
        store.append(KEY_A, turn("첫째", 0L))
        store.append(KEY_A, turn("둘째", 100L))
        store.append(KEY_A, turn("셋째", 200L))

        assertEquals(
            listOf("둘째", "셋째"),
            store.history(KEY_A, 999L).map { it.userMessage }
        )
        assertTrue(store.history(KEY_A, 1_201L).isEmpty())
    }

    @Test
    fun `self clear does not remove another user memory`() = runBlocking {
        val store = store(FakeBackend())
        store.initialize()
        store.append(KEY_A, turn("A", 1L))
        store.append(KEY_B, turn("B", 1L))

        store.clear(KEY_A)

        assertTrue(store.history(KEY_A, 2L).isEmpty())
        assertEquals(1, store.history(KEY_B, 2L).size)
    }

    @Test
    fun `quarantines malformed and oversized documents`() = runBlocking {
        val malformed = FakeBackend("{not-json".toByteArray())
        store(malformed).initialize()
        assertEquals(1, malformed.quarantineCount)

        val oversized = FakeBackend(ByteArray(200))
        store(oversized, maxBytes = 100).initialize()
        assertEquals(1, oversized.quarantineCount)

        val unsupported = FakeBackend(
            """{"version":99,"updatedAtMillis":0,"conversations":[]}""".toByteArray()
        )
        store(unsupported).initialize()
        assertEquals(1, unsupported.quarantineCount)
    }

    @Test
    fun `failed write leaves the previous complete document recoverable`() = runBlocking {
        val backend = FakeBackend()
        val first = store(backend)
        first.initialize()
        first.append(KEY_A, turn("저장 성공", 1L))
        backend.failWrites = true

        assertFalse(first.append(KEY_A, turn("저장 실패", 2L)))

        backend.failWrites = false
        val restored = store(backend)
        restored.initialize()
        assertEquals(
            listOf("저장 성공"),
            restored.history(KEY_A, 3L).map { it.userMessage }
        )
    }

    private fun store(
        backend: FakeBackend,
        maxTurns: Int = 4,
        ttl: Long = 30 * 60 * 1000L,
        maxBytes: Int = 1024 * 1024
    ) = AtomicJsonConversationMemoryStore(
        backend = backend,
        maxTurnsPerConversation = maxTurns,
        ttlMillis = ttl,
        maxBytes = maxBytes,
        maxConversations = 100,
        log = {}
    )

    private fun turn(message: String, at: Long) = ConversationTurn(
        userMessage = message,
        assistantMessage = "답변",
        updatedAtMillis = at
    )

    private class FakeBackend(initial: ByteArray? = null) : ConversationMemoryBackend {
        var bytes: ByteArray? = initial
        var quarantineCount = 0
        var failWrites = false

        override fun read(): ByteArray? = bytes

        override fun write(bytes: ByteArray) {
            if (failWrites) error("simulated write failure")
            this.bytes = bytes
        }

        override fun quarantine(nowMillis: Long) {
            quarantineCount += 1
            bytes = null
        }
    }

    private companion object {
        val KEY_A = ConversationKey(chatId = 100L, userId = 200L)
        val KEY_B = ConversationKey(chatId = 100L, userId = 201L)
    }
}
