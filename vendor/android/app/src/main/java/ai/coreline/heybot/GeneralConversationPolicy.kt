package ai.coreline.heybot

import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission

enum class GeneralConversationPolicyReason {
    READY,
    NOT_CONFIGURED,
    FILE_UNAVAILABLE,
    FILE_METADATA_INVALID,
    FILE_CONTENT_INVALID
}

data class GeneralConversationPolicyStatus(
    val ready: Boolean,
    val allowedRoomCount: Int,
    val globalBlockCount: Int,
    val roomBlockCount: Int,
    val reason: GeneralConversationPolicyReason
)

/**
 * Exact numeric room/user admission policy for ambient general conversation.
 * Message text, nicknames and profile data never enter this component.
 */
class GeneralConversationPolicy private constructor(
    private val allowedChatIds: Set<Long>,
    private val globallyBlockedUserIds: Set<Long>,
    private val roomBlockedUsers: Set<Pair<Long, Long>>,
    private val reason: GeneralConversationPolicyReason
) {
    fun allows(chatId: Long, userId: Long): Boolean =
        chatId in allowedChatIds && allowsUser(chatId, userId)

    /** Applies the shared user block list without the ambient-room allowlist. */
    fun allowsUser(chatId: Long, userId: Long): Boolean =
        reason == GeneralConversationPolicyReason.READY &&
            userId !in globallyBlockedUserIds &&
            (chatId to userId) !in roomBlockedUsers

    fun status(): GeneralConversationPolicyStatus = GeneralConversationPolicyStatus(
        ready = reason == GeneralConversationPolicyReason.READY,
        allowedRoomCount = allowedChatIds.size,
        globalBlockCount = globallyBlockedUserIds.size,
        roomBlockCount = roomBlockedUsers.size,
        reason = reason
    )

    companion object {
        private val PRIVATE_PERMISSIONS = setOf(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE
        )

        fun disabled(
            reason: GeneralConversationPolicyReason =
                GeneralConversationPolicyReason.NOT_CONFIGURED
        ): GeneralConversationPolicy = GeneralConversationPolicy(
            allowedChatIds = emptySet(),
            globallyBlockedUserIds = emptySet(),
            roomBlockedUsers = emptySet(),
            reason = reason
        )

        fun load(
            settings: GeneralConversationSettings?,
            metadataVerifier: (File) -> Boolean = ::hasPrivateMetadata
        ): GeneralConversationPolicy {
            if (settings == null) return disabled()
            val file = settings.blockFile
            if (!file.isFile || !file.canRead()) {
                return unavailable(settings, GeneralConversationPolicyReason.FILE_UNAVAILABLE)
            }
            if (!metadataVerifier(file)) {
                return unavailable(settings, GeneralConversationPolicyReason.FILE_METADATA_INVALID)
            }

            val global = linkedSetOf<Long>()
            val room = linkedSetOf<Pair<Long, Long>>()
            val canonical = linkedSetOf<String>()
            val parsed = runCatching {
                file.useLines { lines ->
                    lines.forEach { raw ->
                        val line = raw.trim()
                        if (line.isEmpty() || line.startsWith('#')) return@forEach
                        if (!canonical.add(line)) throw IllegalArgumentException("duplicate")
                        val parts = line.split(':')
                        when (parts.size) {
                            1 -> global += positiveLong(parts[0])
                            2 -> room += positiveLong(parts[0]) to positiveLong(parts[1])
                            else -> throw IllegalArgumentException("invalid")
                        }
                    }
                }
            }
            if (parsed.isFailure) {
                return unavailable(settings, GeneralConversationPolicyReason.FILE_CONTENT_INVALID)
            }
            return GeneralConversationPolicy(
                allowedChatIds = settings.allowedChatIds,
                globallyBlockedUserIds = global,
                roomBlockedUsers = room,
                reason = GeneralConversationPolicyReason.READY
            )
        }

        fun forTesting(
            allowedChatIds: Set<Long>,
            globallyBlockedUserIds: Set<Long> = emptySet(),
            roomBlockedUsers: Set<Pair<Long, Long>> = emptySet()
        ): GeneralConversationPolicy = GeneralConversationPolicy(
            allowedChatIds = allowedChatIds,
            globallyBlockedUserIds = globallyBlockedUserIds,
            roomBlockedUsers = roomBlockedUsers,
            reason = GeneralConversationPolicyReason.READY
        )

        private fun unavailable(
            settings: GeneralConversationSettings,
            reason: GeneralConversationPolicyReason
        ) = GeneralConversationPolicy(
            allowedChatIds = settings.allowedChatIds,
            globallyBlockedUserIds = emptySet(),
            roomBlockedUsers = emptySet(),
            reason = reason
        )

        private fun positiveLong(raw: String): Long {
            if (!raw.matches(Regex("[1-9]\\d{0,18}"))) {
                throw IllegalArgumentException("invalid")
            }
            return raw.toLongOrNull()?.takeIf { it > 0L }
                ?: throw IllegalArgumentException("invalid")
        }

        private fun hasPrivateMetadata(file: File): Boolean = runCatching {
            val permissions = Files.getPosixFilePermissions(file.toPath())
            if (permissions != PRIVATE_PERMISSIONS) return@runCatching false
            val vmName = System.getProperty("java.vm.name").orEmpty()
            if (vmName.contains("Dalvik", ignoreCase = true)) {
                Files.getOwner(file.toPath()).name == "root"
            } else {
                true
            }
        }.getOrDefault(false)
    }
}
