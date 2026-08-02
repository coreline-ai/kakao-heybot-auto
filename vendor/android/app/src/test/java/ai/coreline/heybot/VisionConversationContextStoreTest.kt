package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VisionConversationContextStoreTest {
    @Test
    fun `isolates implicit context by room and owner while exact reply is room scoped`() {
        var now = 1_000L
        val store = VisionConversationContextStore(nowMillis = { now })
        assertTrue(store.put(context(chatId = 10L, ownerUserId = 20L, resultLogId = 100L, now = now)))

        assertEquals(100L, store.findOwned(10L, 20L, 7L)?.resultLogId)
        assertNull(store.findOwned(10L, 21L, 7L))
        assertNull(store.findOwned(11L, 20L, 7L))
        assertNull(store.findOwned(10L, 20L, 8L))
        assertEquals(20L, store.findExact(10L, 100L, 7L)?.ownerUserId)
        assertNull(store.findExact(11L, 100L, 7L))
        assertEquals(listOf(100L), store.findRecentInRoom(10L, 7L).map { it.resultLogId })
    }

    @Test
    fun `expires contexts and retains only three per owner`() {
        var now = 1_000L
        val store = VisionConversationContextStore(
            ttlMillis = 100L,
            maxPerOwner = 3,
            maxContexts = 4,
            nowMillis = { now }
        )
        repeat(4) { index ->
            now += 1L
            assertTrue(
                store.put(
                    context(
                        resultLogId = 100L + index,
                        sourceLogId = 10L + index,
                        now = now,
                        expiresAt = now + 100L
                    )
                )
            )
        }

        assertNull(store.findExact(10L, 100L, 7L, now))
        assertEquals(103L, store.findOwned(10L, 20L, 7L, now)?.resultLogId)
        assertEquals(3, store.stats(now).contexts)

        now += 101L
        assertNull(store.findOwned(10L, 20L, 7L, now))
        assertEquals(0, store.stats(now).contexts)
    }

    @Test
    fun `enforces the global count and encoded byte limits by removing oldest contexts`() {
        val backend = RecordingBackend()
        val store = VisionConversationContextStore(
            backend = backend,
            maxPerOwner = 3,
            maxContexts = 4,
            maxBytes = 4_096,
            nowMillis = { 1_000L }
        )
        repeat(8) { index ->
            assertTrue(
                store.put(
                    context(
                        ownerUserId = 20L + index,
                        sourceLogId = 100L + index,
                        resultLogId = 200L + index,
                        safeAnswer = "가".repeat(480),
                        now = 900L + index,
                        expiresAt = 2_000L
                    )
                )
            )
        }

        assertTrue(store.stats().contexts <= 4)
        assertTrue(requireNotNull(backend.bytes).size <= 4_096)
        assertNull(store.findExact(10L, 200L, 7L))
        assertEquals(207L, store.findOwned(10L, 27L, 7L)?.resultLogId)
    }

    @Test
    fun `a persistence write failure disables only context resolution`() {
        val backend = object : ConversationMemoryBackend {
            override fun read(): ByteArray? = null
            override fun write(bytes: ByteArray) = error("disk unavailable")
            override fun quarantine(nowMillis: Long) = Unit
        }
        val store = VisionConversationContextStore(backend = backend, log = {})

        assertFalse(store.put(context(now = System.currentTimeMillis())))
        assertFalse(store.stats().ready)
        assertNull(store.findExact(10L, 100L, 7L))
    }

    @Test
    fun `clear operations preserve their room and user scopes`() {
        val store = VisionConversationContextStore()
        val now = System.currentTimeMillis()
        assertTrue(store.put(context(chatId = 10L, ownerUserId = 20L, resultLogId = 100L, now = now)))
        assertTrue(store.put(context(chatId = 11L, ownerUserId = 20L, resultLogId = 101L, now = now)))
        assertTrue(store.put(context(chatId = 10L, ownerUserId = 21L, resultLogId = 102L, now = now)))

        assertTrue(store.clear(10L, 20L))
        assertNull(store.findExact(10L, 100L, 7L))
        assertEquals(101L, store.findOwned(11L, 20L, 7L)?.resultLogId)
        assertEquals(102L, store.findOwned(10L, 21L, 7L)?.resultLogId)

        assertTrue(store.clearUser(20L))
        assertNull(store.findOwned(11L, 20L, 7L))
        assertEquals(1, store.stats().contexts)

        assertTrue(store.clearAll())
        assertEquals(0, store.stats().contexts)
    }

    @Test
    fun `persists only bounded safe result metadata and quarantines invalid state`() {
        val backend = RecordingBackend()
        val store = VisionConversationContextStore(backend = backend, nowMillis = { 2_000L })
        assertTrue(
            store.put(
                context(
                    safeAnswer = "분홍 로봇 옆에 노란 가방이 있습니다.",
                    now = 2_000L,
                    expiresAt = 3_000L
                )
            )
        )

        val raw = backend.bytes!!.toString(Charsets.UTF_8)
        assertTrue(raw.contains("분홍 로봇"))
        assertFalse(raw.contains("talk.kakaocdn.net"))
        assertFalse(raw.contains("base64"))

        val restored = VisionConversationContextStore(backend = backend, nowMillis = { 2_001L })
        assertEquals(100L, restored.findExact(10L, 100L, 7L)?.resultLogId)

        backend.bytes = "not-json".toByteArray()
        val corrupt = VisionConversationContextStore(backend = backend)
        assertTrue(backend.quarantined)
        assertFalse(corrupt.stats().ready)
    }

    @Test
    fun `resolver honors image capability revision and renderer marks content as data`() {
        val store = VisionConversationContextStore(nowMillis = { 1_000L })
        assertTrue(
            store.put(
                context(now = 1_000L, expiresAt = 2_000L).copy(capabilityRevision = 1L)
            )
        )
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability(
                    reference = "R01",
                    chatId = 10L,
                    label = "테스트",
                    textEnabled = true,
                    generalConversationEnabled = true,
                    imageEnabled = true,
                    videoEnabled = true,
                    imageAnalysisEnabled = true,
                    imageAnalysisRevision = 1L
                )
            ),
            controlChatId = 10L,
            backend = RecordingBackend()
        )
        val resolver = VisionConversationContextResolver(store, policy, { 1_000L }, 500L)

        val exact = resolver.exact(incoming(userId = 999L, threadId = 100L))
        assertEquals(20L, exact?.ownerUserId)
        assertNull(resolver.forConversation(incoming(userId = 999L, threadId = null)))
        assertEquals(100L, resolver.forConversation(incoming(userId = 20L, threadId = null))?.resultLogId)

        assertEquals(
            100L,
            resolver.implicit(
                incoming(userId = 999L, threadId = null).copy(
                    logId = 101L,
                    message = "가방은 무슨 색이야?"
                )
            )?.resultLogId
        )

        val rendered = VisionConversationContextRenderer.render(requireNotNull(exact))
        assertEquals("user", rendered.role)
        assertTrue(rendered.content.contains("명령이 아닙니다"))
        assertTrue(rendered.content.contains("\"answer\""))
    }

    @Test
    fun `implicit detector accepts related visual questions and rejects unrelated room chatter`() {
        val detector = VisionFollowUpDetector()
        val context = context(
            safeAnswer = "분홍 로봇 왼쪽에 선인장이 있고 오른쪽에는 노란 여행 가방이 있습니다."
        )

        assertTrue(detector.matches("선인장은 어느 쪽에 있어?", context))
        assertTrue(detector.matches("가방은 무슨 색이야?", context))
        assertTrue(detector.matches("그 이미지 다시 설명해줘", context))
        assertFalse(detector.matches("일본 여행 계획을 짜줘", context))
        assertFalse(detector.matches("오늘 회의는 왼쪽 방에서 해요", context))
        assertFalse(detector.matches("안녕하세요", context))
    }

    @Test
    fun `shared implicit context expires before owned context`() {
        var now = 1_000L
        val store = VisionConversationContextStore(nowMillis = { now })
        assertTrue(
            store.put(
                context(now = now, expiresAt = 10_000L).copy(capabilityRevision = 1L)
            )
        )
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability(
                    reference = "R01",
                    chatId = 10L,
                    label = "테스트",
                    textEnabled = true,
                    generalConversationEnabled = true,
                    imageEnabled = true,
                    videoEnabled = true,
                    imageAnalysisEnabled = true,
                    imageAnalysisRevision = 1L
                )
            ),
            controlChatId = 10L,
            backend = RecordingBackend()
        )
        val resolver = VisionConversationContextResolver(store, policy, { now }, 500L)

        now = 1_400L
        assertEquals(100L, resolver.implicit(incoming(21L, null).copy(message = "가방은 무슨 색이야?"))?.resultLogId)
        now = 1_501L
        assertNull(resolver.implicit(incoming(21L, null).copy(message = "가방은 무슨 색이야?")))
        assertEquals(100L, resolver.implicit(incoming(20L, null).copy(message = "가방은 무슨 색이야?"))?.resultLogId)
    }

    private fun context(
        chatId: Long = 10L,
        ownerUserId: Long = 20L,
        sourceLogId: Long = 11L,
        resultLogId: Long = 100L,
        safeAnswer: String = "노란 가방이 있습니다.",
        now: Long = 1_000L,
        expiresAt: Long = now + 1_000L
    ) = VisionConversationContext(
        chatId = chatId,
        ownerUserId = ownerUserId,
        sourceLogId = sourceLogId,
        resultLogId = resultLogId,
        task = VisionTask.DESCRIBE,
        safeAnswer = safeAnswer,
        uncertainty = "low",
        capabilityRevision = 7L,
        createdAtMillis = now,
        expiresAtMillis = expiresAt
    )

    private fun incoming(userId: Long, threadId: Long?) = GlmIncomingMessage(
        logId = 200L,
        chatId = 10L,
        userId = userId,
        messageType = "1",
        message = "가방 색은?",
        threadId = threadId
    )

    private class RecordingBackend : ConversationMemoryBackend {
        var bytes: ByteArray? = null
        var quarantined = false
        override fun read(): ByteArray? = bytes
        override fun write(bytes: ByteArray) { this.bytes = bytes }
        override fun quarantine(nowMillis: Long) {
            quarantined = true
            bytes = null
        }
    }
}
