package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue

/** Correlates queued text with the later bot-authored Kakao DB row by digest. */
class TextDeliveryTracker(
    private val botId: Long,
    private val traces: RequestTraceStore,
    private val confirmTimeoutMillis: Long = DEFAULT_CONFIRM_TIMEOUT_MILLIS,
    private val lateWindowMillis: Long = DEFAULT_LATE_WINDOW_MILLIS,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val pendingByRoom = ConcurrentHashMap<Long, ConcurrentLinkedQueue<PendingTextDelivery>>()
    private val confirmations = ConcurrentHashMap<String, CompletableDeferred<Long?>>()

    fun enqueued(traceId: String, chatId: Long, message: String, threadId: Long?) {
        if (traces.get(traceId) == null) return
        // A coordinator can send progress and final text under one request
        // trace. Only the newest text may satisfy an awaiting final delivery;
        // superseded progress rows must not time out and overwrite that trace.
        pendingByRoom[chatId]?.filter { it.traceId == traceId && !it.confirmed }
            ?.forEach { previous ->
                previous.confirmed = true
                previous.confirmation.complete(null)
                remove(chatId, previous)
            }
        confirmations.remove(traceId)?.complete(null)
        val pending = PendingTextDelivery(
            traceId = traceId,
            digest = digest(message),
            threadId = threadId,
            enqueuedAtMillis = nowMillis(),
            confirmation = CompletableDeferred()
        )
        confirmations[traceId] = pending.confirmation
        pendingByRoom.computeIfAbsent(chatId) { ConcurrentLinkedQueue() }.add(pending)
        traces.record(traceId, RequestTraceStage.ENQUEUED)
        scope.launch {
            delay(confirmTimeoutMillis)
            if (!pending.confirmed) {
                pending.timedOut = true
                traces.record(traceId, RequestTraceStage.UNCONFIRMED, reasonCode = "KAKAO_DB_TIMEOUT")
                pending.confirmation.complete(null)
                delay((lateWindowMillis - confirmTimeoutMillis).coerceAtLeast(0L))
                remove(chatId, pending)
                confirmations.remove(traceId, pending.confirmation)
            } else {
                confirmations.remove(traceId, pending.confirmation)
            }
        }
    }

    fun dispatched(chatId: Long, message: String, result: Result<Unit>) {
        val pending = findPending(chatId, message) ?: return
        traces.record(
            pending.traceId,
            if (result.isSuccess) RequestTraceStage.DISPATCHED else RequestTraceStage.DISPATCH_FAILED,
            reasonCode = result.exceptionOrNull()?.let { "KAKAO_SERVICE_EXCEPTION" }
        )
        if (result.isFailure) {
            pending.confirmed = true
            pending.confirmation.complete(null)
            remove(chatId, pending)
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (botId == 0L || incoming.userId != botId || incoming.messageType != "1") return
        val queue = pendingByRoom[incoming.chatId] ?: return
        val hash = digest(incoming.message)
        val pending = queue.firstOrNull {
            !it.confirmed &&
                it.digest == hash &&
                (it.threadId == null || it.threadId == incoming.threadId)
        } ?: return
        pending.confirmed = true
        traces.record(
            pending.traceId,
            if (pending.timedOut) RequestTraceStage.DB_CONFIRMED_LATE else RequestTraceStage.DB_CONFIRMED
        )
        pending.confirmation.complete(incoming.logId)
        remove(incoming.chatId, pending)
    }

    /** Waits for the matching outgoing Kakao DB row, not merely service dispatch. */
    suspend fun awaitConfirmation(traceId: String): Boolean {
        return awaitConfirmedLogId(traceId) != null
    }

    /** Returns the actual outgoing Kakao DB log ID after a digest/thread match. */
    suspend fun awaitConfirmedLogId(traceId: String): Long? {
        val deferred = confirmations[traceId] ?: return null
        return try {
            deferred.await()
        } finally {
            confirmations.remove(traceId, deferred)
        }
    }

    fun close() {
        pendingByRoom.clear()
        confirmations.values.forEach { it.complete(null) }
        confirmations.clear()
        scope.cancel()
    }

    private fun findPending(chatId: Long, message: String): PendingTextDelivery? {
        val hash = digest(message)
        return pendingByRoom[chatId]?.firstOrNull { !it.confirmed && it.digest == hash }
    }

    private fun remove(chatId: Long, pending: PendingTextDelivery) {
        pendingByRoom[chatId]?.let { queue ->
            queue.remove(pending)
            if (queue.isEmpty()) pendingByRoom.remove(chatId, queue)
        }
    }

    private fun digest(message: String): String = MessageDigest.getInstance("SHA-256")
        .digest(message.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    private data class PendingTextDelivery(
        val traceId: String,
        val digest: String,
        val threadId: Long?,
        val enqueuedAtMillis: Long,
        val confirmation: CompletableDeferred<Long?>,
        @Volatile var timedOut: Boolean = false,
        @Volatile var confirmed: Boolean = false
    )

    companion object {
        const val DEFAULT_CONFIRM_TIMEOUT_MILLIS = 15_000L
        const val DEFAULT_LATE_WINDOW_MILLIS = 10L * 60L * 1_000L
    }
}
