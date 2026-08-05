package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission
import java.util.UUID

enum class RoomCapability(val commandName: String, val statusName: String) {
    TEXT("텍스트", "텍스트"),
    GENERAL_CONVERSATION("일반대화", "일반대화"),
    IMAGE("이미지", "이미지"),
    /** Video starts deny-by-default, even for policy files created before it existed. */
    VIDEO("영상", "영상"),
    /** YouTube downloader is independent from generated video and deny-by-default. */
    YOUTUBE_DOWNLOAD("유튜브", "유튜브다운로드"),
    /** Pen-brush rendering is billable and starts deny-by-default. */
    PEN_BRUSH("펜브러쉬", "펜브러쉬"),
    /** Analysis of user-provided images is independent from image generation. */
    IMAGE_ANALYSIS("이미지분석", "이미지분석"),
    /** Manual STT and summary command for a supported Kakao file attachment. */
    AUDIO_ANALYSIS("음성", "음성분석"),
    /** Automatic STT starts deny-by-default and additionally requires TEXT+AUDIO_ANALYSIS. */
    AUDIO_AUTO_ANALYSIS("음성자동", "음성자동분석")
}

data class ManagedRoomCapability(
    val reference: String,
    val chatId: Long,
    val label: String,
    val textEnabled: Boolean,
    val generalConversationEnabled: Boolean,
    val imageEnabled: Boolean,
    val videoEnabled: Boolean = false,
    val youtubeDownloadEnabled: Boolean = false,
    val penBrushEnabled: Boolean = false,
    val imageAnalysisEnabled: Boolean = false,
    val audioAnalysisEnabled: Boolean = false,
    val audioAutoAnalysisEnabled: Boolean = false,
    /** Independent tokens keep a text/general update from cancelling an image job. */
    val textRevision: Long = 0L,
    val generalConversationRevision: Long = 0L,
    val imageRevision: Long = 0L,
    val videoRevision: Long = 0L,
    val youtubeDownloadRevision: Long = 0L,
    val penBrushRevision: Long = 0L,
    val imageAnalysisRevision: Long = 0L,
    val audioAnalysisRevision: Long = 0L,
    val audioAutoAnalysisRevision: Long = 0L
)

data class RoomCapabilitySnapshot(
    val ready: Boolean,
    val revision: Long,
    val rooms: List<ManagedRoomCapability>
) {
    val textRoomCount: Int get() = rooms.count { it.textEnabled }
    val generalConversationRoomCount: Int get() = rooms.count { it.generalConversationEnabled }
    val imageRoomCount: Int get() = rooms.count { it.imageEnabled }
    val videoRoomCount: Int get() = rooms.count { it.videoEnabled }
    val youtubeDownloadRoomCount: Int get() = rooms.count { it.youtubeDownloadEnabled }
    val penBrushRoomCount: Int get() = rooms.count { it.penBrushEnabled }
    val imageAnalysisRoomCount: Int get() = rooms.count { it.imageAnalysisEnabled }
    val audioAnalysisRoomCount: Int get() = rooms.count { it.audioAnalysisEnabled }
    val audioAutoAnalysisRoomCount: Int get() = rooms.count { it.audioAutoAnalysisEnabled }

    fun capabilityRevision(chatId: Long, capability: RoomCapability): Long? =
        rooms.firstOrNull { it.chatId == chatId }?.let { room ->
            when (capability) {
                RoomCapability.TEXT -> room.textRevision
                RoomCapability.GENERAL_CONVERSATION -> room.generalConversationRevision
                RoomCapability.IMAGE -> room.imageRevision
                RoomCapability.VIDEO -> room.videoRevision
                RoomCapability.YOUTUBE_DOWNLOAD -> room.youtubeDownloadRevision
                RoomCapability.PEN_BRUSH -> room.penBrushRevision
                RoomCapability.IMAGE_ANALYSIS -> room.imageAnalysisRevision
                RoomCapability.AUDIO_ANALYSIS -> room.audioAnalysisRevision
                RoomCapability.AUDIO_AUTO_ANALYSIS -> room.audioAutoAnalysisRevision
            }
        }
}

data class RoomCapabilityPreview(
    val nonce: String,
    val reference: String,
    val label: String,
    val capability: RoomCapability,
    val enabled: Boolean,
    val expiresAtMillis: Long
)

sealed interface RoomCapabilityMutationResult {
    data class PreviewReady(val preview: RoomCapabilityPreview) : RoomCapabilityMutationResult
    data class Applied(val preview: RoomCapabilityPreview) : RoomCapabilityMutationResult
    data object Cancelled : RoomCapabilityMutationResult
    data class Rejected(val reason: String) : RoomCapabilityMutationResult
}

/**
 * Runtime source of truth for room features. Only numeric chat IDs enter the
 * authorization decision; the user-facing reference and label are catalog
 * metadata for the control-room UI.
 */
class RoomCapabilityPolicyStore private constructor(
    initial: RoomCapabilitySnapshot,
    private val backend: ConversationMemoryBackend?,
    private val managedChatIds: Set<Long>,
    private val controlChatId: Long?,
    private val nowMillis: () -> Long,
    private val log: (String) -> Unit
) {
    private val lock = Any()

    @Volatile
    private var current = initial
    private val pendingByUserId = mutableMapOf<Long, PendingMutation>()

    fun snapshot(): RoomCapabilitySnapshot = current

    fun allows(chatId: Long, capability: RoomCapability): Boolean {
        val room = current.rooms.firstOrNull { it.chatId == chatId } ?: return false
        return when (capability) {
            RoomCapability.TEXT -> room.textEnabled
            RoomCapability.GENERAL_CONVERSATION -> room.generalConversationEnabled
            RoomCapability.IMAGE -> room.imageEnabled
            RoomCapability.VIDEO -> room.videoEnabled
            RoomCapability.YOUTUBE_DOWNLOAD -> room.youtubeDownloadEnabled
            RoomCapability.PEN_BRUSH -> room.penBrushEnabled
            RoomCapability.IMAGE_ANALYSIS -> room.imageAnalysisEnabled
            RoomCapability.AUDIO_ANALYSIS -> room.audioAnalysisEnabled
            RoomCapability.AUDIO_AUTO_ANALYSIS -> room.audioAutoAnalysisEnabled
        }
    }

    fun isCurrent(revision: Long, chatId: Long, capability: RoomCapability): Boolean {
        val room = current.rooms.firstOrNull { it.chatId == chatId } ?: return false
        val currentRevision = when (capability) {
            RoomCapability.TEXT -> room.textRevision
            RoomCapability.GENERAL_CONVERSATION -> room.generalConversationRevision
            RoomCapability.IMAGE -> room.imageRevision
            RoomCapability.VIDEO -> room.videoRevision
            RoomCapability.YOUTUBE_DOWNLOAD -> room.youtubeDownloadRevision
            RoomCapability.PEN_BRUSH -> room.penBrushRevision
            RoomCapability.IMAGE_ANALYSIS -> room.imageAnalysisRevision
            RoomCapability.AUDIO_ANALYSIS -> room.audioAnalysisRevision
            RoomCapability.AUDIO_AUTO_ANALYSIS -> room.audioAutoAnalysisRevision
        }
        if (currentRevision != revision) return false
        return when (capability) {
            RoomCapability.TEXT -> room.textEnabled
            RoomCapability.GENERAL_CONVERSATION -> room.generalConversationEnabled
            RoomCapability.IMAGE -> room.imageEnabled
            RoomCapability.VIDEO -> room.videoEnabled
            RoomCapability.YOUTUBE_DOWNLOAD -> room.youtubeDownloadEnabled
            RoomCapability.PEN_BRUSH -> room.penBrushEnabled
            RoomCapability.IMAGE_ANALYSIS -> room.imageAnalysisEnabled
            RoomCapability.AUDIO_ANALYSIS -> room.audioAnalysisEnabled
            RoomCapability.AUDIO_AUTO_ANALYSIS -> room.audioAutoAnalysisEnabled
        }
    }

    fun preview(
        userId: Long,
        reference: String,
        capability: RoomCapability,
        enabled: Boolean
    ): RoomCapabilityMutationResult = synchronized(lock) {
        if (!current.ready) return@synchronized RoomCapabilityMutationResult.Rejected("방 권한 정책이 준비되지 않았어요.")
        val room = current.rooms.firstOrNull { it.reference == reference }
            ?: return@synchronized RoomCapabilityMutationResult.Rejected("방 참조값을 찾을 수 없어요. ‘헤이봇 방 목록’을 확인해주세요.")
        if (room.chatId == controlChatId && capability == RoomCapability.TEXT && !enabled) {
            return@synchronized RoomCapabilityMutationResult.Rejected("코어라인 AI 연구소의 텍스트 권한은 끌 수 없어요.")
        }
        if (capability == RoomCapability.GENERAL_CONVERSATION && enabled && !room.textEnabled) {
            return@synchronized RoomCapabilityMutationResult.Rejected("일반대화는 텍스트 권한이 허용된 방에서만 켤 수 있어요.")
        }
        if (capability == RoomCapability.AUDIO_AUTO_ANALYSIS && enabled &&
            (!room.textEnabled || !room.audioAnalysisEnabled)
        ) {
            return@synchronized RoomCapabilityMutationResult.Rejected("음성자동은 텍스트와 음성 권한이 모두 허용된 방에서만 켤 수 있어요.")
        }
        val preview = RoomCapabilityPreview(
            nonce = UUID.randomUUID().toString().replace("-", "").take(8).uppercase(),
            reference = room.reference,
            label = room.label,
            capability = capability,
            enabled = enabled,
            expiresAtMillis = nowMillis() + PREVIEW_TTL_MILLIS
        )
        pendingByUserId[userId] = PendingMutation(preview)
        RoomCapabilityMutationResult.PreviewReady(preview)
    }

    fun apply(userId: Long, nonce: String): RoomCapabilityMutationResult = synchronized(lock) {
        val pending = pendingByUserId.remove(userId)
            ?: return@synchronized RoomCapabilityMutationResult.Rejected("적용할 방 권한 변경이 없어요.")
        val preview = pending.preview
        if (nowMillis() > preview.expiresAtMillis) {
            return@synchronized RoomCapabilityMutationResult.Rejected("방 권한 변경 시간이 만료됐어요. 다시 요청해주세요.")
        }
        if (!preview.nonce.equals(nonce.trim(), ignoreCase = true)) {
            return@synchronized RoomCapabilityMutationResult.Rejected("확인 코드가 일치하지 않아요.")
        }
        val nextRevision = current.revision + 1L
        val rooms = current.rooms.map { room ->
            if (room.reference != preview.reference) room else when (preview.capability) {
                RoomCapability.TEXT -> room.copy(
                    textEnabled = preview.enabled,
                    generalConversationEnabled = if (preview.enabled) room.generalConversationEnabled else false,
                    audioAutoAnalysisEnabled = if (preview.enabled) room.audioAutoAnalysisEnabled else false,
                    textRevision = nextRevision,
                    generalConversationRevision = if (!preview.enabled && room.generalConversationEnabled) {
                        nextRevision
                    } else {
                        room.generalConversationRevision
                    },
                    audioAutoAnalysisRevision = if (!preview.enabled && room.audioAutoAnalysisEnabled) {
                        nextRevision
                    } else room.audioAutoAnalysisRevision
                )
                RoomCapability.GENERAL_CONVERSATION -> room.copy(
                    generalConversationEnabled = preview.enabled,
                    generalConversationRevision = nextRevision
                )
                RoomCapability.IMAGE -> room.copy(
                    imageEnabled = preview.enabled,
                    imageRevision = nextRevision
                )
                RoomCapability.VIDEO -> room.copy(
                    videoEnabled = preview.enabled,
                    videoRevision = nextRevision
                )
                RoomCapability.YOUTUBE_DOWNLOAD -> room.copy(
                    youtubeDownloadEnabled = preview.enabled,
                    youtubeDownloadRevision = nextRevision
                )
                RoomCapability.PEN_BRUSH -> room.copy(
                    penBrushEnabled = preview.enabled,
                    penBrushRevision = nextRevision
                )
                RoomCapability.IMAGE_ANALYSIS -> room.copy(
                    imageAnalysisEnabled = preview.enabled,
                    imageAnalysisRevision = nextRevision
                )
                RoomCapability.AUDIO_ANALYSIS -> room.copy(
                    audioAnalysisEnabled = preview.enabled,
                    audioAutoAnalysisEnabled = if (preview.enabled) room.audioAutoAnalysisEnabled else false,
                    audioAnalysisRevision = nextRevision,
                    audioAutoAnalysisRevision = if (!preview.enabled && room.audioAutoAnalysisEnabled) {
                        nextRevision
                    } else room.audioAutoAnalysisRevision
                )
                RoomCapability.AUDIO_AUTO_ANALYSIS -> room.copy(
                    audioAutoAnalysisEnabled = preview.enabled,
                    audioAutoAnalysisRevision = nextRevision
                )
            }
        }
        val next = RoomCapabilitySnapshot(true, nextRevision, rooms)
        if (!isValid(next, managedChatIds, controlChatId)) {
            return@synchronized RoomCapabilityMutationResult.Rejected("방 권한 조합이 올바르지 않아요.")
        }
        val persisted = runCatching { backend?.write(encode(next)) ?: error("방 권한 저장소가 없어요") }
            .onFailure { log("Room capability policy persist failed: ${it::class.simpleName}") }
            .isSuccess
        if (!persisted) {
            return@synchronized RoomCapabilityMutationResult.Rejected("방 권한을 저장하지 못했어요. 변경하지 않았어요.")
        }
        current = next
        RoomCapabilityMutationResult.Applied(preview)
    }

    fun cancel(userId: Long): RoomCapabilityMutationResult = synchronized(lock) {
        pendingByUserId.remove(userId)
        RoomCapabilityMutationResult.Cancelled
    }

    fun renderList(): String {
        val snapshot = current
        if (!snapshot.ready) return "방 권한 정책이 준비되지 않았어요."
        return buildString {
            append("헤이봇 지원 카톡방 목록\n")
            snapshot.rooms.forEach { room ->
                append("\n")
                append(room.reference).append(". ").append(room.label).append("\n")
                append("텍스트: ").append(enabledLabel(room.textEnabled)).append(" | ")
                append("일반대화: ").append(enabledLabel(room.generalConversationEnabled)).append(" | ")
                append("이미지: ").append(enabledLabel(room.imageEnabled))
                append(" | 영상: ").append(enabledLabel(room.videoEnabled))
                append(" | 유튜브: ").append(enabledLabel(room.youtubeDownloadEnabled))
                append(" | 펜브러쉬: ").append(enabledLabel(room.penBrushEnabled))
                append(" | 이미지분석: ").append(enabledLabel(room.imageAnalysisEnabled))
                append(" | 음성: ").append(enabledLabel(room.audioAnalysisEnabled))
                append(" | 음성자동: ").append(enabledLabel(room.audioAutoAnalysisEnabled))
            }
        }.take(MAX_REPLY_CHARS)
    }

    /** Returns the stable reference and configured Kakao room title for the
     * room where the user entered `헤이봇 카톡방`. */
    fun renderCurrentRoom(chatId: Long): String {
        val snapshot = current
        if (!snapshot.ready) return "방 권한 정책이 준비되지 않았어요."
        val room = snapshot.rooms.firstOrNull { it.chatId == chatId }
            ?: return "이 카톡방은 헤이봇 관리 대상이 아니에요."
        return "현재 카톡방\n" +
            "${room.reference}. ${room.label}\n" +
            "텍스트: ${enabledLabel(room.textEnabled)} | " +
            "일반대화: ${enabledLabel(room.generalConversationEnabled)}\n" +
            "이미지: ${enabledLabel(room.imageEnabled)} | " +
            "영상: ${enabledLabel(room.videoEnabled)} | " +
            "유튜브: ${enabledLabel(room.youtubeDownloadEnabled)} | " +
            "펜브러쉬: ${enabledLabel(room.penBrushEnabled)} | " +
            "이미지분석: ${enabledLabel(room.imageAnalysisEnabled)}\n" +
            "음성: ${enabledLabel(room.audioAnalysisEnabled)} | " +
            "음성자동: ${enabledLabel(room.audioAutoAnalysisEnabled)}"
    }

    fun renderStatus(reference: String): String {
        val room = current.rooms.firstOrNull { it.reference == reference }
            ?: return "방 참조값을 찾을 수 없어요. ‘헤이봇 방 목록’을 확인해주세요."
        return "${room.reference}. ${room.label}\n" +
            "텍스트: ${enabledLabel(room.textEnabled)}\n" +
            "일반대화: ${enabledLabel(room.generalConversationEnabled)}\n" +
            "이미지: ${enabledLabel(room.imageEnabled)}\n" +
            "영상: ${enabledLabel(room.videoEnabled)}\n" +
            "유튜브: ${enabledLabel(room.youtubeDownloadEnabled)}\n" +
            "펜브러쉬: ${enabledLabel(room.penBrushEnabled)}\n" +
            "이미지분석: ${enabledLabel(room.imageAnalysisEnabled)}\n" +
            "음성: ${enabledLabel(room.audioAnalysisEnabled)}\n" +
            "음성자동: ${enabledLabel(room.audioAutoAnalysisEnabled)}"
    }

    private fun encode(snapshot: RoomCapabilitySnapshot): ByteArray =
        json.encodeToString(
            PersistedRoomCapabilityDocument(
                version = VERSION,
                revision = snapshot.revision,
                rooms = snapshot.rooms.map {
                    PersistedManagedRoomCapability(
                        reference = it.reference,
                        chatId = it.chatId.toString(),
                        label = it.label,
                        textEnabled = it.textEnabled,
                        generalConversationEnabled = it.generalConversationEnabled,
                        imageEnabled = it.imageEnabled,
                        videoEnabled = it.videoEnabled,
                        youtubeDownloadEnabled = it.youtubeDownloadEnabled,
                        penBrushEnabled = it.penBrushEnabled,
                        imageAnalysisEnabled = it.imageAnalysisEnabled,
                        audioAnalysisEnabled = it.audioAnalysisEnabled,
                        audioAutoAnalysisEnabled = it.audioAutoAnalysisEnabled,
                        textRevision = it.textRevision,
                        generalConversationRevision = it.generalConversationRevision,
                        imageRevision = it.imageRevision,
                        videoRevision = it.videoRevision,
                        youtubeDownloadRevision = it.youtubeDownloadRevision,
                        penBrushRevision = it.penBrushRevision,
                        imageAnalysisRevision = it.imageAnalysisRevision,
                        audioAnalysisRevision = it.audioAnalysisRevision,
                        audioAutoAnalysisRevision = it.audioAutoAnalysisRevision
                    )
                }
            )
        ).toByteArray(Charsets.UTF_8)

    private data class PendingMutation(val preview: RoomCapabilityPreview)

    companion object {
        private const val VERSION = 5
        private const val MAX_FILE_BYTES = 64 * 1024
        private const val MAX_REPLY_CHARS = 480
        private const val PREVIEW_TTL_MILLIS = 2 * 60 * 1000L
        private val json = Json { ignoreUnknownKeys = false; encodeDefaults = true }
        private val privatePermissions = setOf(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE
        )

        fun load(
            settings: RoomCapabilitySettings?,
            managedChatIds: Set<Long>,
            controlChatId: Long?,
            backend: ConversationMemoryBackend? = null,
            metadataVerifier: (File) -> Boolean = ::hasPrivateMetadata,
            nowMillis: () -> Long = System::currentTimeMillis,
            log: (String) -> Unit = ::println
        ): RoomCapabilityPolicyStore {
            if (settings == null || controlChatId == null) {
                return disabled(managedChatIds, controlChatId, nowMillis, log)
            }
            val file = settings.policyFile
            val actualBackend = backend ?: AndroidAtomicFileBackend(file)
            if (!file.isFile || !file.canRead() || !metadataVerifier(file)) {
                return disabled(managedChatIds, controlChatId, nowMillis, log)
            }
            val snapshot = runCatching {
                val bytes = actualBackend.read() ?: error("missing")
                require(bytes.size <= MAX_FILE_BYTES)
                decode(json.decodeFromString<PersistedRoomCapabilityDocument>(bytes.toString(Charsets.UTF_8)))
            }.getOrElse {
                log("Room capability policy load failed: ${it::class.simpleName}")
                return disabled(managedChatIds, controlChatId, nowMillis, log)
            }
            if (!isValid(snapshot, managedChatIds, controlChatId)) {
                log("Room capability policy load failed: InvalidPolicy")
                return disabled(managedChatIds, controlChatId, nowMillis, log)
            }
            return RoomCapabilityPolicyStore(snapshot, actualBackend, managedChatIds, controlChatId, nowMillis, log)
        }

        fun legacy(managedChatIds: Set<Long>): RoomCapabilityPolicyStore =
            RoomCapabilityPolicyStore(
                RoomCapabilitySnapshot(
                    ready = true,
                    revision = 0L,
                    rooms = managedChatIds.sorted().mapIndexed { index, chatId ->
                        ManagedRoomCapability("R${(index + 1).toString().padStart(2, '0')}", chatId, "관리 방", true, true, true, false)
                    }
                ),
                backend = null,
                managedChatIds = managedChatIds,
                controlChatId = null,
                nowMillis = System::currentTimeMillis,
                log = {}
            )

        fun forTesting(
            rooms: List<ManagedRoomCapability>,
            controlChatId: Long,
            backend: ConversationMemoryBackend,
            nowMillis: () -> Long = System::currentTimeMillis
        ): RoomCapabilityPolicyStore {
            val snapshot = RoomCapabilitySnapshot(true, 1L, rooms)
            require(isValid(snapshot, rooms.map { it.chatId }.toSet(), controlChatId))
            return RoomCapabilityPolicyStore(snapshot, backend, rooms.map { it.chatId }.toSet(), controlChatId, nowMillis, {})
        }

        private fun disabled(
            managedChatIds: Set<Long>,
            controlChatId: Long?,
            nowMillis: () -> Long,
            log: (String) -> Unit
        ) = RoomCapabilityPolicyStore(
            RoomCapabilitySnapshot(false, -1L, emptyList()),
            backend = null,
            managedChatIds = managedChatIds,
            controlChatId = controlChatId,
            nowMillis = nowMillis,
            log = log
        )

        private fun decode(document: PersistedRoomCapabilityDocument): RoomCapabilitySnapshot {
            require(document.version in 1..VERSION && document.revision >= 1L)
            return RoomCapabilitySnapshot(
                ready = true,
                revision = document.revision,
                rooms = document.rooms.map {
                    ManagedRoomCapability(
                        reference = it.reference,
                        chatId = it.chatId.toLongOrNull() ?: error("chatId"),
                        label = it.label,
                        textEnabled = it.textEnabled,
                        generalConversationEnabled = it.generalConversationEnabled,
                        imageEnabled = it.imageEnabled,
                        videoEnabled = it.videoEnabled ?: false,
                        youtubeDownloadEnabled = it.youtubeDownloadEnabled ?: false,
                        // Existing policy documents must never implicitly enable pen-brush rendering.
                        penBrushEnabled = it.penBrushEnabled ?: false,
                        // Existing policies never implicitly enable user-image analysis.
                        imageAnalysisEnabled = it.imageAnalysisEnabled ?: false,
                        // Existing policies never implicitly enable audio processing.
                        audioAnalysisEnabled = it.audioAnalysisEnabled ?: false,
                        audioAutoAnalysisEnabled = it.audioAutoAnalysisEnabled ?: false,
                        // Version 1 policy files did not carry per-capability
                        // revisions. Their global revision safely represents
                        // each capability's last known state.
                        textRevision = it.textRevision ?: document.revision,
                        generalConversationRevision =
                            it.generalConversationRevision ?: document.revision,
                        imageRevision = it.imageRevision ?: document.revision,
                        // Existing policy documents must never implicitly enable billable video.
                        videoRevision = it.videoRevision ?: document.revision,
                        youtubeDownloadRevision = it.youtubeDownloadRevision ?: document.revision,
                        penBrushRevision = it.penBrushRevision ?: document.revision,
                        imageAnalysisRevision = it.imageAnalysisRevision ?: document.revision,
                        audioAnalysisRevision = it.audioAnalysisRevision ?: document.revision,
                        audioAutoAnalysisRevision = it.audioAutoAnalysisRevision ?: document.revision
                    )
                }
            )
        }

        private fun isValid(
            snapshot: RoomCapabilitySnapshot,
            managedChatIds: Set<Long>,
            controlChatId: Long?
        ): Boolean {
            if (!snapshot.ready || snapshot.revision < 0L || snapshot.rooms.isEmpty()) return false
            if (snapshot.rooms.map { it.reference }.toSet().size != snapshot.rooms.size) return false
            if (snapshot.rooms.map { it.chatId }.toSet().size != snapshot.rooms.size) return false
            if (snapshot.rooms.any {
                    !it.reference.matches(Regex("R[0-9]{2}")) ||
                        it.chatId <= 0L || it.chatId !in managedChatIds ||
                        it.label.isBlank() || it.label.length > 80 ||
                        it.label.any { char -> char == '\n' || char == '\r' } ||
                        it.textRevision < 0L || it.textRevision > snapshot.revision ||
                        it.generalConversationRevision < 0L ||
                        it.generalConversationRevision > snapshot.revision ||
                        it.imageRevision < 0L || it.imageRevision > snapshot.revision ||
                        it.videoRevision < 0L || it.videoRevision > snapshot.revision ||
                        it.youtubeDownloadRevision < 0L || it.youtubeDownloadRevision > snapshot.revision ||
                        it.penBrushRevision < 0L || it.penBrushRevision > snapshot.revision ||
                        it.imageAnalysisRevision < 0L ||
                        it.imageAnalysisRevision > snapshot.revision ||
                        it.audioAnalysisRevision < 0L ||
                        it.audioAnalysisRevision > snapshot.revision ||
                        it.audioAutoAnalysisRevision < 0L ||
                        it.audioAutoAnalysisRevision > snapshot.revision ||
                        (it.generalConversationEnabled && !it.textEnabled) ||
                        (it.audioAutoAnalysisEnabled &&
                            (!it.textEnabled || !it.audioAnalysisEnabled))
                }
            ) return false
            return controlChatId == null || snapshot.rooms.any { it.chatId == controlChatId && it.textEnabled }
        }

        private fun enabledLabel(enabled: Boolean): String = if (enabled) "허용" else "불허용"

        private fun hasPrivateMetadata(file: File): Boolean = runCatching {
            if (Files.getPosixFilePermissions(file.toPath()) != privatePermissions) return@runCatching false
            val vmName = System.getProperty("java.vm.name").orEmpty()
            !vmName.contains("Dalvik", ignoreCase = true) || Files.getOwner(file.toPath()).name == "root"
        }.getOrDefault(false)
    }
}

@Serializable
private data class PersistedRoomCapabilityDocument(
    val version: Int,
    val revision: Long,
    val rooms: List<PersistedManagedRoomCapability>
)

@Serializable
private data class PersistedManagedRoomCapability(
    val reference: String,
    val chatId: String,
    val label: String,
    val textEnabled: Boolean,
    val generalConversationEnabled: Boolean,
    val imageEnabled: Boolean,
    val videoEnabled: Boolean? = null,
    val youtubeDownloadEnabled: Boolean? = null,
    val penBrushEnabled: Boolean? = null,
    val imageAnalysisEnabled: Boolean? = null,
    val audioAnalysisEnabled: Boolean? = null,
    val audioAutoAnalysisEnabled: Boolean? = null,
    val textRevision: Long? = null,
    val generalConversationRevision: Long? = null,
    val imageRevision: Long? = null,
    val videoRevision: Long? = null,
    val youtubeDownloadRevision: Long? = null,
    val penBrushRevision: Long? = null,
    val imageAnalysisRevision: Long? = null,
    val audioAnalysisRevision: Long? = null,
    val audioAutoAnalysisRevision: Long? = null
)
