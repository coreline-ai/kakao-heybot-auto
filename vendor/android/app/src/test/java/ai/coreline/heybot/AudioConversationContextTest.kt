package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioConversationContextTest {
    @Test
    fun `owner and exact reply resolve while shared requires an explicit audio reference`() {
        val now = 1_000_000L
        val store = AudioConversationContextStore(nowMillis = { now })
        val policy = policy()
        assertTrue(
            store.put(
                AudioConversationContext(
                    chatId = CHAT_ID,
                    ownerUserId = OWNER_ID,
                    jobId = "audio-job-1",
                    sourceLogId = 20L,
                    resultLogIds = listOf(30L, 31L),
                    profile = AudioSummaryProfile(),
                    safeSummary = "회의에서 다음 주 화요일 재검토를 결정했습니다.",
                    evidenceIds = listOf("S0001"),
                    capabilityRevision = 0L,
                    createdAtMillis = now,
                    expiresAtMillis = now + 30 * 60_000L
                )
            )
        )
        val resolver = AudioConversationContextResolver(store, policy, nowMillis = { now })
        assertNotNull(resolver.forConversation(message(32L, OWNER_ID, "헤이봇 다음 단계는 뭐야?")))
        assertNotNull(resolver.implicit(message(32L, OWNER_ID, "다음 단계는 뭐야?")))
        assertNotNull(resolver.exact(message(33L, OTHER_ID, "그건 누구 담당이야?", threadId = 31L)))
        assertNotNull(resolver.implicit(message(34L, OTHER_ID, "그 음성 요약의 결정은 뭐야?")))
        assertNull(resolver.implicit(message(35L, OTHER_ID, "다음 단계는 뭐야?")))
        assertTrue(store.removeJob(CHAT_ID, "audio-job-1"))
        assertNull(resolver.exact(message(36L, OTHER_ID, "그거?", threadId = 30L)))
    }

    @Test
    fun `persistence failure fails closed without changing the in-memory context`() {
        val store = AudioConversationContextStore(
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = error("disk unavailable")
                override fun quarantine(nowMillis: Long) = Unit
            },
            nowMillis = { 1L }
        )
        val context = AudioConversationContext(
            chatId = CHAT_ID, ownerUserId = OWNER_ID, jobId = "audio-job-failure", sourceLogId = 1L,
            resultLogIds = listOf(2L), profile = AudioSummaryProfile(), safeSummary = "안전한 요약",
            evidenceIds = listOf("S0001"), capabilityRevision = 0L,
            createdAtMillis = 1L, expiresAtMillis = 2L
        )
        assertTrue(!store.put(context))
        assertEquals(0, store.stats(now = 1L).contexts)
        assertTrue(!store.stats(now = 1L).ready)
    }

    @Test
    fun `renderer contains only safe summary metadata and no source media`() {
        val context = AudioConversationContext(
            chatId = CHAT_ID,
            ownerUserId = OWNER_ID,
            jobId = "audio-job-2",
            sourceLogId = 45L,
            resultLogIds = listOf(46L),
            profile = AudioSummaryProfile(AudioSummaryPattern.MEETING, AudioSummaryView.MINUTES),
            safeSummary = "안전한 요약",
            evidenceIds = listOf("S0001"),
            capabilityRevision = 0L,
            createdAtMillis = 1L,
            expiresAtMillis = 2L
        )
        val rendered = AudioConversationContextRenderer.render(context).content
        assertTrue(rendered.contains("안전한 요약"))
        assertTrue(!rendered.contains("sourceLogId") && !rendered.contains("45"))
    }

    private fun message(logId: Long, userId: Long, text: String, threadId: Long? = null) =
        GlmIncomingMessage(logId, CHAT_ID, userId, "1", text, threadId)

    private fun policy() = RoomCapabilityPolicyStore.forTesting(
        rooms = listOf(
            ManagedRoomCapability(
                reference = "R01", chatId = CHAT_ID, label = "연구소",
                textEnabled = true, generalConversationEnabled = true, imageEnabled = true,
                audioAnalysisEnabled = true
            )
        ),
        controlChatId = CHAT_ID,
        backend = object : ConversationMemoryBackend {
            override fun read(): ByteArray? = null
            override fun write(bytes: ByteArray) = Unit
            override fun quarantine(nowMillis: Long) = Unit
        }
    )

    private companion object {
        const val CHAT_ID = 10L
        const val OWNER_ID = 20L
        const val OTHER_ID = 21L
    }
}
