package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class RoomCapabilityPolicyTest {
    @Test
    fun `preview does not mutate and apply persists an immediate text and general disable`() {
        var now = 1_000L
        val backend = MemoryBackend()
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = backend,
            nowMillis = { now }
        )

        val preview = policy.preview(ADMIN_ID, "R02", RoomCapability.TEXT, false)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.allows(TARGET_ROOM, RoomCapability.TEXT))
        assertTrue(policy.allows(TARGET_ROOM, RoomCapability.GENERAL_CONVERSATION))

        val applied = policy.apply(ADMIN_ID, preview.preview.nonce)
        assertTrue(applied is RoomCapabilityMutationResult.Applied)
        assertFalse(policy.allows(TARGET_ROOM, RoomCapability.TEXT))
        assertFalse(policy.allows(TARGET_ROOM, RoomCapability.GENERAL_CONVERSATION))
        assertFalse(policy.isCurrent(1L, TARGET_ROOM, RoomCapability.TEXT))
        assertTrue(backend.bytes != null)
    }

    @Test
    fun `control room text cannot be disabled and general requires text`() {
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = MemoryBackend()
        )

        assertTrue(
            policy.preview(ADMIN_ID, "R01", RoomCapability.TEXT, false)
                is RoomCapabilityMutationResult.Rejected
        )

        val disabledText = policy.preview(ADMIN_ID, "R02", RoomCapability.TEXT, false)
            as RoomCapabilityMutationResult.PreviewReady
        policy.apply(ADMIN_ID, disabledText.preview.nonce)
        assertTrue(
            policy.preview(ADMIN_ID, "R02", RoomCapability.GENERAL_CONVERSATION, true)
                is RoomCapabilityMutationResult.Rejected
        )
    }

    @Test
    fun `only the preview creator can apply the nonce and expiry is enforced`() {
        var now = 1_000L
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = MemoryBackend(),
            nowMillis = { now }
        )
        val preview = policy.preview(ADMIN_ID, "R02", RoomCapability.IMAGE, false)
            as RoomCapabilityMutationResult.PreviewReady

        assertTrue(policy.apply(OTHER_ADMIN_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Rejected)
        now += 120_001L
        assertTrue(policy.apply(ADMIN_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Rejected)
        assertTrue(policy.allows(TARGET_ROOM, RoomCapability.IMAGE))
    }

    @Test
    fun `a policy update invalidates only work for the changed room`() {
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = MemoryBackend()
        )
        val controlRevision = policy.snapshot().capabilityRevision(CONTROL_ROOM, RoomCapability.IMAGE)!!
        val targetRevision = policy.snapshot().capabilityRevision(TARGET_ROOM, RoomCapability.IMAGE)!!

        val preview = policy.preview(ADMIN_ID, "R02", RoomCapability.IMAGE, false)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.apply(ADMIN_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)

        assertTrue(policy.isCurrent(controlRevision, CONTROL_ROOM, RoomCapability.IMAGE))
        assertFalse(policy.isCurrent(targetRevision, TARGET_ROOM, RoomCapability.IMAGE))
    }

    @Test
    fun `video is deny by default and has an independent revision`() {
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = MemoryBackend()
        )
        val imageRevision = policy.snapshot().capabilityRevision(TARGET_ROOM, RoomCapability.IMAGE)!!
        val videoRevision = policy.snapshot().capabilityRevision(TARGET_ROOM, RoomCapability.VIDEO)!!

        assertFalse(policy.allows(TARGET_ROOM, RoomCapability.VIDEO))
        val preview = policy.preview(ADMIN_ID, "R02", RoomCapability.VIDEO, true)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.apply(ADMIN_ID, preview.preview.nonce) is RoomCapabilityMutationResult.Applied)

        assertTrue(policy.allows(TARGET_ROOM, RoomCapability.VIDEO))
        assertTrue(policy.isCurrent(imageRevision, TARGET_ROOM, RoomCapability.IMAGE))
        assertFalse(policy.isCurrent(videoRevision, TARGET_ROOM, RoomCapability.VIDEO))
    }

    @Test
    fun `version two policy migrates image analysis to deny with independent revision`() {
        val file = File.createTempFile("room-policy-v2", ".json")
        val backend = MemoryBackend().apply {
            bytes = """{"version":2,"revision":6,"rooms":[{"reference":"R01","chatId":"$CONTROL_ROOM","label":"코어라인 AI 연구소","textEnabled":true,"generalConversationEnabled":true,"imageEnabled":true,"videoEnabled":true,"penBrushEnabled":true}]}""".toByteArray()
        }
        val policy = RoomCapabilityPolicyStore.load(
            settings = RoomCapabilitySettings(file),
            managedChatIds = setOf(CONTROL_ROOM),
            controlChatId = CONTROL_ROOM,
            backend = backend,
            metadataVerifier = { true }
        )
        assertFalse(policy.allows(CONTROL_ROOM, RoomCapability.IMAGE_ANALYSIS))
        assertEquals(6L, policy.snapshot().capabilityRevision(CONTROL_ROOM, RoomCapability.IMAGE_ANALYSIS))
        file.delete()
    }

    @Test
    fun `legacy policy migrates audio to deny and auto requires text plus audio`() {
        val file = File.createTempFile("room-policy-audio", ".json")
        val backend = MemoryBackend().apply {
            bytes = """{"version":3,"revision":6,"rooms":[{"reference":"R01","chatId":"$CONTROL_ROOM","label":"코어라인 AI 연구소","textEnabled":true,"generalConversationEnabled":true,"imageEnabled":true,"videoEnabled":true,"penBrushEnabled":true,"imageAnalysisEnabled":true}]}""".toByteArray()
        }
        val policy = RoomCapabilityPolicyStore.load(
            settings = RoomCapabilitySettings(file),
            managedChatIds = setOf(CONTROL_ROOM),
            controlChatId = CONTROL_ROOM,
            backend = backend,
            metadataVerifier = { true }
        )
        assertFalse(policy.allows(CONTROL_ROOM, RoomCapability.AUDIO_ANALYSIS))
        assertFalse(policy.allows(CONTROL_ROOM, RoomCapability.AUDIO_AUTO_ANALYSIS))
        assertTrue(
            policy.preview(ADMIN_ID, "R01", RoomCapability.AUDIO_AUTO_ANALYSIS, true)
                is RoomCapabilityMutationResult.Rejected
        )
        val audio = policy.preview(ADMIN_ID, "R01", RoomCapability.AUDIO_ANALYSIS, true)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.apply(ADMIN_ID, audio.preview.nonce) is RoomCapabilityMutationResult.Applied)
        val auto = policy.preview(ADMIN_ID, "R01", RoomCapability.AUDIO_AUTO_ANALYSIS, true)
            as RoomCapabilityMutationResult.PreviewReady
        assertTrue(policy.apply(ADMIN_ID, auto.preview.nonce) is RoomCapabilityMutationResult.Applied)
        assertTrue(policy.allows(CONTROL_ROOM, RoomCapability.AUDIO_AUTO_ANALYSIS))
        file.delete()
    }

    @Test
    fun `current room response identifies the entered Kakao room by title and reference`() {
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = rooms(),
            controlChatId = CONTROL_ROOM,
            backend = MemoryBackend()
        )

        assertEquals(
            "현재 카톡방\n" +
                "R01. 코어라인 AI 연구소\n" +
                "텍스트: 허용 | 일반대화: 허용\n" +
                "이미지: 허용 | 영상: 불허용 | 펜브러쉬: 불허용 | 이미지분석: 불허용\n" +
                "음성: 불허용 | 음성자동: 불허용",
            policy.renderCurrentRoom(CONTROL_ROOM)
        )
        assertEquals("이 카톡방은 헤이봇 관리 대상이 아니에요.", policy.renderCurrentRoom(99L))
    }

    private fun rooms() = listOf(
        ManagedRoomCapability("R01", CONTROL_ROOM, "코어라인 AI 연구소", true, true, true),
        ManagedRoomCapability("R02", TARGET_ROOM, "테스트 방", true, true, true)
    )

    private class MemoryBackend : ConversationMemoryBackend {
        var bytes: ByteArray? = null
        override fun read(): ByteArray? = bytes
        override fun write(bytes: ByteArray) {
            this.bytes = bytes
        }
        override fun quarantine(nowMillis: Long) = Unit
    }

    private companion object {
        const val CONTROL_ROOM = 18480337854645134L
        const val TARGET_ROOM = 18393359886930036L
        const val ADMIN_ID = 100L
        const val OTHER_ADMIN_ID = 101L
    }
}
