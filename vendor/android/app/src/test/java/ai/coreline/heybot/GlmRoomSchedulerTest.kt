package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger

class GlmRoomSchedulerTest {
    @Test
    fun `slow room does not block another room and same room stays FIFO`() = runBlocking {
        val firstStarted = CompletableDeferred<Unit>()
        val releaseFirst = CompletableDeferred<Unit>()
        val otherFinished = CompletableDeferred<Unit>()
        val allFinished = CompletableDeferred<Unit>()
        val events = Collections.synchronizedList(mutableListOf<String>())
        val completed = AtomicInteger()
        val scheduler = scheduler { request ->
            val question = request.question
            events += "$question:start"
            if (question == "A1") {
                firstStarted.complete(Unit)
                releaseFirst.await()
            }
            events += "$question:end"
            if (question == "B1") otherFinished.complete(Unit)
            if (completed.incrementAndGet() == 3) allFinished.complete(Unit)
        }

        assertTrue(scheduler.submit(request(1L, 1L, "A1")) is GlmQueueSubmitResult.Accepted)
        firstStarted.await()
        assertTrue(scheduler.submit(request(2L, 1L, "A2")) is GlmQueueSubmitResult.Accepted)
        assertTrue(scheduler.submit(request(3L, 2L, "B1")) is GlmQueueSubmitResult.Accepted)

        withTimeout(1_000L) { otherFinished.await() }
        assertTrue("B room should finish before A1 is released", "B1:end" in events)
        assertTrue("A2 must not overtake A1", "A2:start" !in events)

        releaseFirst.complete(Unit)
        withTimeout(1_000L) { allFinished.await() }
        assertTrue(events.indexOf("A1:end") < events.indexOf("A2:start"))
        scheduler.close()
    }

    @Test
    fun `global concurrency never exceeds configured limit`() = runBlocking {
        val active = AtomicInteger()
        val maximum = AtomicInteger()
        val finished = CompletableDeferred<Unit>()
        val count = AtomicInteger()
        val scheduler = scheduler(maxConcurrency = 2) {
            val current = active.incrementAndGet()
            maximum.updateAndGet { previous -> maxOf(previous, current) }
            delay(50L)
            active.decrementAndGet()
            if (count.incrementAndGet() == 6) finished.complete(Unit)
        }

        repeat(6) {
            scheduler.submit(request((it + 1).toLong(), (it + 1).toLong(), "Q$it"))
        }

        withTimeout(2_000L) { finished.await() }
        assertEquals(2, maximum.get())
        scheduler.close()
    }

    @Test
    fun `rejects requests at room and total queue boundaries`() = runBlocking {
        val activeStarted = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val scheduler = scheduler(
            roomCapacity = 1,
            totalCapacity = 2,
            maxConcurrency = 1
        ) { request ->
            if (request.question == "active") {
                activeStarted.complete(Unit)
                release.await()
            }
        }

        scheduler.submit(request(1, 1, "active"))
        activeStarted.await()
        assertTrue(scheduler.submit(request(2, 1, "room pending")) is GlmQueueSubmitResult.Accepted)
        assertEquals(
            GlmQueueSubmitResult.RoomQueueFull,
            scheduler.submit(request(3, 1, "room overflow"))
        )
        assertTrue(scheduler.submit(request(4, 2, "other pending")) is GlmQueueSubmitResult.Accepted)
        assertEquals(
            GlmQueueSubmitResult.TotalQueueFull,
            scheduler.submit(request(5, 3, "total overflow"))
        )

        release.complete(Unit)
        scheduler.close()
    }

    @Test
    fun `worker failure is isolated and close rejects new work`() = runBlocking {
        val healthyFinished = CompletableDeferred<Unit>()
        val scheduler = scheduler { request ->
            if (request.question == "fail") error("simulated")
            healthyFinished.complete(Unit)
        }

        scheduler.submit(request(1, 1, "fail"))
        scheduler.submit(request(2, 2, "healthy"))
        withTimeout(1_000L) { healthyFinished.await() }

        scheduler.close()
        assertEquals(
            GlmQueueSubmitResult.Closed,
            scheduler.submit(request(3, 3, "after close"))
        )
    }

    private fun scheduler(
        roomCapacity: Int = 8,
        totalCapacity: Int = 24,
        maxConcurrency: Int = 2,
        process: suspend (QueuedGlmRequest) -> Unit
    ) = GlmRoomScheduler(
        roomQueueCapacity = roomCapacity,
        totalQueueCapacity = totalCapacity,
        maxConcurrency = maxConcurrency,
        parentScope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
        process = process,
        log = {}
    )

    private fun request(logId: Long, chatId: Long, question: String) = QueuedGlmRequest(
        incoming = GlmIncomingMessage(logId, chatId, 99L, "1", "헤이봇 $question", null),
        question = question
    )
}
