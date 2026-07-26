package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class RequestAdmissionControllerTest {
    @Test
    fun `blocks duplicate log ID and normalized message for eight seconds`() {
        var now = 0L
        val controller = controller(now = { now })

        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(1, 10, 20, "헤이봇  안녕")))
        assertEquals(
            AdmissionResult.DuplicateLog,
            controller.admit(incoming(1, 10, 20, "헤이봇 다른 질문"))
        )
        now = 7_999L
        assertEquals(
            AdmissionResult.DuplicateMessage,
            controller.admit(incoming(2, 10, 20, "  헤이봇 안녕  "))
        )
        now = 8_000L
        assertEquals(
            AdmissionResult.Accepted,
            controller.admit(incoming(3, 10, 20, "헤이봇 안녕"))
        )
    }

    @Test
    fun `same text from another room or user is independent`() {
        val controller = controller()

        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(1, 10, 20, "같은 내용")))
        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(2, 11, 20, "같은 내용")))
        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(3, 10, 21, "같은 내용")))
    }

    @Test
    fun `enforces room sliding window and reopens at the boundary`() {
        var now = 0L
        val controller = controller(
            roomMax = 3,
            userMax = 100,
            now = { now }
        )

        repeat(3) {
            assertEquals(
                AdmissionResult.Accepted,
                controller.admit(incoming((it + 1).toLong(), 10, (20 + it).toLong(), "질문 $it"))
            )
        }
        val limited = controller.admit(incoming(4, 10, 30, "네 번째"))
        assertTrue(limited is AdmissionResult.RoomRateLimited)

        now = 30_000L
        assertEquals(
            AdmissionResult.Accepted,
            controller.admit(incoming(5, 10, 30, "다시"))
        )
    }

    @Test
    fun `enforces user sliding window across rooms`() {
        val controller = controller(roomMax = 100, userMax = 2)

        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(1, 10, 20, "첫째")))
        assertEquals(AdmissionResult.Accepted, controller.admit(incoming(2, 11, 20, "둘째")))
        assertTrue(
            controller.admit(incoming(3, 12, 20, "셋째")) is AdmissionResult.UserRateLimited
        )
    }

    @Test
    fun `concurrent requests cannot exceed the room limit`() {
        val controller = controller(roomMax = 3, userMax = 100)
        val start = CountDownLatch(1)
        val done = CountDownLatch(20)
        val results = Collections.synchronizedList(mutableListOf<AdmissionResult>())
        val executor = Executors.newFixedThreadPool(8)

        repeat(20) { index ->
            executor.execute {
                start.await()
                results += controller.admit(
                    incoming(
                        logId = (index + 1).toLong(),
                        chatId = 10L,
                        userId = (100 + index).toLong(),
                        message = "질문 $index"
                    )
                )
                done.countDown()
            }
        }
        start.countDown()
        assertTrue(done.await(2, TimeUnit.SECONDS))
        executor.shutdownNow()

        assertEquals(3, results.count { it == AdmissionResult.Accepted })
        assertEquals(17, results.count { it is AdmissionResult.RoomRateLimited })
    }

    private fun controller(
        roomMax: Int = 100,
        userMax: Int = 100,
        now: () -> Long = { 0L }
    ) = RequestAdmissionController(
        roomWindowMillis = 30_000L,
        roomMaxRequests = roomMax,
        userWindowMillis = 60_000L,
        userMaxRequests = userMax,
        duplicateWindowMillis = 8_000L,
        nowMillis = now
    )

    private fun incoming(
        logId: Long,
        chatId: Long,
        userId: Long,
        message: String
    ) = GlmIncomingMessage(logId, chatId, userId, "1", message, null)
}
