package ai.coreline.heybot

import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RequestTraceStoreTest {
    @Test
    fun `ensure received never resets a coordinator stage`() {
        val store = RequestTraceStore.inMemory()
        val request = incoming(11L, 10L)
        store.ensureReceived(request, RequestTraceKind.VISION)
        store.record(request.traceId, RequestTraceStage.PROVIDER_STARTED)

        store.ensureReceived(request)

        assertEquals(RequestTraceKind.VISION, store.get(request.traceId)?.kind)
        assertEquals(RequestTraceStage.PROVIDER_STARTED, store.get(request.traceId)?.stage)
    }

    @Test
    fun `stores bounded metadata and excludes diagnostic command from recent lookup`() {
        var now = 1_000L
        val store = RequestTraceStore(maxEntries = 2, ttlMillis = 100L, nowMillis = { now })
        val first = incoming(1L, 10L)
        store.received(first, RequestTraceKind.WAKE_WORD)
        store.record(first.traceId, RequestTraceStage.PROVIDER_STARTED, engine = "codex")

        val diagnostic = incoming(2L, 10L)
        store.received(diagnostic, RequestTraceKind.DIAGNOSTICS)
        assertEquals(first.traceId, store.recent(10L)?.traceId)

        val third = incoming(3L, 20L)
        store.received(third, RequestTraceKind.GENERAL_CONVERSATION)
        assertEquals(2, store.snapshot().size)
        assertNull(store.get(first.traceId))

        now += 101L
        assertTrue(store.snapshot().isEmpty())
    }

    @Test
    fun `persists only safe trace metadata and quarantines corrupt state`() {
        val backend = RecordingTraceBackend()
        val store = RequestTraceStore(backend = backend)
        val incoming = incoming(7L, 99L)
        store.received(incoming, RequestTraceKind.WAKE_WORD)
        store.record(
            incoming.traceId,
            RequestTraceStage.PROVIDER_FAILED,
            reasonCode = "provider timeout\nBearer secret",
            engine = "Codex"
        )

        val raw = backend.bytes!!.toString(Charsets.UTF_8)
        assertFalse(raw.contains("Bearer secret"))
        assertFalse(raw.contains("질문 원문"))
        assertTrue(raw.contains("PROVIDER_TIMEOUT_BEARER_SECRET"))

        val restored = RequestTraceStore(backend = backend)
        assertEquals(RequestTraceStage.PROVIDER_FAILED, restored.get(incoming.traceId)?.stage)

        backend.bytes = "not-json".toByteArray()
        val corrupt = RequestTraceStore(backend = backend)
        assertTrue(backend.quarantined)
        assertFalse(corrupt.isPersistenceAvailable())
    }

    @Test
    fun `delivery tracker distinguishes dispatch timeout and late DB confirmation`() = runBlocking {
        val store = RequestTraceStore.inMemory()
        val request = incoming(8L, 30L)
        store.received(request, RequestTraceKind.WAKE_WORD)
        val tracker = TextDeliveryTracker(
            botId = 999L,
            traces = store,
            confirmTimeoutMillis = 10L,
            lateWindowMillis = 1_000L
        )

        tracker.enqueued(request.traceId, request.chatId, "안녕하세요", null)
        tracker.dispatched(request.chatId, "안녕하세요", Result.success(Unit))
        assertEquals(RequestTraceStage.DISPATCHED, store.get(request.traceId)?.stage)

        delay(30L)
        assertEquals(RequestTraceStage.UNCONFIRMED, store.get(request.traceId)?.stage)
        tracker.onIncoming(
            request.copy(logId = 9L, userId = 999L, message = "안녕하세요")
        )
        assertEquals(RequestTraceStage.DB_CONFIRMED_LATE, store.get(request.traceId)?.stage)
        tracker.close()
    }

    @Test
    fun `delivery tracker does not confirm a different reply thread`() = runBlocking {
        val store = RequestTraceStore.inMemory()
        val request = incoming(12L, 30L).copy(threadId = 700L)
        store.received(request, RequestTraceKind.WAKE_WORD)
        val tracker = TextDeliveryTracker(
            botId = 999L,
            traces = store,
            confirmTimeoutMillis = 1_000L,
            lateWindowMillis = 2_000L
        )

        tracker.enqueued(request.traceId, request.chatId, "같은 답변", request.threadId)
        tracker.onIncoming(
            request.copy(logId = 13L, userId = 999L, message = "같은 답변", threadId = 701L)
        )

        assertEquals(RequestTraceStage.ENQUEUED, store.get(request.traceId)?.stage)
        tracker.close()
    }

    @Test
    fun `delivery stages never erase the first terminal reason`() {
        val store = RequestTraceStore.inMemory()
        val request = incoming(14L, 30L)
        store.received(request, RequestTraceKind.VISION)
        store.record(
            request.traceId,
            RequestTraceStage.PROVIDER_FAILED,
            reasonCode = "VISION_TRANSPORT_UNAVAILABLE"
        )

        store.record(request.traceId, RequestTraceStage.ENQUEUED)
        store.record(request.traceId, RequestTraceStage.DB_CONFIRMED)

        val trace = store.get(request.traceId)!!
        assertEquals(RequestTraceStage.DB_CONFIRMED, trace.stage)
        assertNull(trace.reasonCode)
        assertEquals("VISION_TRANSPORT_UNAVAILABLE", trace.rootReasonCode)
        assertTrue(RequestTraceRenderer.render(trace, "R01").contains("VISION_TRANSPORT_UNAVAILABLE"))
    }

    @Test
    fun `loads schema one trace and writes it back as schema two`() {
        val request = incoming(15L, 30L)
        val backend = RecordingTraceBackend().apply {
            bytes = """{"schemaVersion":1,"updatedAtMillis":1000,"traces":[{"traceId":"${request.traceId}","logId":"15","chatId":"30","kind":"VISION","stage":"PROVIDER_FAILED","reasonCode":"VISION_CREATE_FAILED","engine":null,"startedAtMillis":900,"updatedAtMillis":1000}]}""".toByteArray()
        }

        val store = RequestTraceStore(backend = backend, nowMillis = { 1_000L })
        assertEquals("VISION_CREATE_FAILED", store.get(request.traceId)?.rootReasonCode)
        store.record(request.traceId, RequestTraceStage.ENQUEUED)

        assertTrue(backend.bytes!!.toString(Charsets.UTF_8).contains("\"schemaVersion\":2"))
        assertFalse(backend.quarantined)
    }

    private fun incoming(logId: Long, chatId: Long) = GlmIncomingMessage(
        logId = logId,
        chatId = chatId,
        userId = 55L,
        messageType = "1",
        message = "질문 원문",
        threadId = null
    )

    private class RecordingTraceBackend : ConversationMemoryBackend {
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
