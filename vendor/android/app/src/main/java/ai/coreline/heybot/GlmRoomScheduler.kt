package ai.coreline.heybot

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

data class QueuedGlmRequest(
    val incoming: GlmIncomingMessage,
    val question: String,
    val generalConversation: GeneralConversationModeSnapshot? = null,
    val roomCapabilityRevision: Long = 0L,
    /** Non-null only for a same-room exact reply to a stored Vision result. */
    val visionResultLogId: Long? = null
)

sealed interface GlmQueueSubmitResult {
    data class Accepted(
        val roomPosition: Int,
        val totalPending: Int
    ) : GlmQueueSubmitResult

    data object RoomQueueFull : GlmQueueSubmitResult
    data object TotalQueueFull : GlmQueueSubmitResult
    data object Closed : GlmQueueSubmitResult
}

data class GlmQueueSnapshot(
    val active: Int,
    val maxConcurrency: Int,
    val totalPending: Int,
    val roomPending: Map<Long, Int>
)

/**
 * One FIFO worker is created per room while a global semaphore limits external
 * GLM calls. A slow request therefore blocks only its own room plus one global
 * slot, not every allowed room.
 */
class GlmRoomScheduler(
    private val roomQueueCapacity: Int,
    private val totalQueueCapacity: Int,
    private val maxConcurrency: Int,
    parentScope: CoroutineScope,
    private val process: suspend (QueuedGlmRequest) -> Unit,
    private val log: (String) -> Unit = ::println
) {
    private val schedulerJob = SupervisorJob(parentScope.coroutineContext[Job])
    private val scope = CoroutineScope(parentScope.coroutineContext + schedulerJob)
    private val semaphore = Semaphore(maxConcurrency)
    private val workers = ConcurrentHashMap<Long, RoomWorker>()
    private val totalPending = AtomicInteger(0)
    private val active = AtomicInteger(0)

    @Volatile
    private var closed = false

    @Synchronized
    fun submit(request: QueuedGlmRequest): GlmQueueSubmitResult {
        if (closed) return GlmQueueSubmitResult.Closed

        val reservedTotal = totalPending.incrementAndGet()
        if (reservedTotal > totalQueueCapacity) {
            totalPending.decrementAndGet()
            return GlmQueueSubmitResult.TotalQueueFull
        }

        val worker = workers.computeIfAbsent(request.incoming.chatId, ::createWorker)
        val roomPosition = worker.pending.incrementAndGet()
        if (roomPosition > roomQueueCapacity) {
            worker.pending.decrementAndGet()
            totalPending.decrementAndGet()
            return GlmQueueSubmitResult.RoomQueueFull
        }

        val result = worker.channel.trySend(request)
        if (result.isFailure) {
            worker.pending.decrementAndGet()
            totalPending.decrementAndGet()
            return if (closed) GlmQueueSubmitResult.Closed else GlmQueueSubmitResult.RoomQueueFull
        }

        return GlmQueueSubmitResult.Accepted(roomPosition, reservedTotal)
    }

    fun snapshot(): GlmQueueSnapshot = GlmQueueSnapshot(
        active = active.get(),
        maxConcurrency = maxConcurrency,
        totalPending = totalPending.get(),
        roomPending = workers.mapValues { it.value.pending.get() }.filterValues { it > 0 }
    )

    @Synchronized
    fun close() {
        closed = true
        workers.values.forEach { it.channel.close() }
        scope.cancel()
        workers.clear()
    }

    private fun createWorker(chatId: Long): RoomWorker {
        val channel = Channel<QueuedGlmRequest>(roomQueueCapacity)
        val pending = AtomicInteger(0)
        scope.launch {
            for (request in channel) {
                var releasedFromPending = false
                try {
                    semaphore.withPermit {
                        pending.decrementAndGet()
                        totalPending.decrementAndGet()
                        releasedFromPending = true
                        active.incrementAndGet()
                        try {
                            process(request)
                        } catch (failure: CancellationException) {
                            throw failure
                        } catch (failure: Throwable) {
                            log("GLM room worker failed: ${failure::class.simpleName}")
                        } finally {
                            active.decrementAndGet()
                        }
                    }
                } finally {
                    if (!releasedFromPending) {
                        pending.decrementAndGet()
                        totalPending.decrementAndGet()
                    }
                }
            }
        }
        return RoomWorker(channel, pending)
    }

    private data class RoomWorker(
        val channel: Channel<QueuedGlmRequest>,
        val pending: AtomicInteger
    )
}
