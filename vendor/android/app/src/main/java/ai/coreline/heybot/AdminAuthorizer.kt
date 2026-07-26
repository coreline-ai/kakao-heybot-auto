package ai.coreline.heybot

import java.io.File

class AdminAuthorizer private constructor(
    private val adminUserIds: Set<Long>
) {
    fun isAdmin(userId: Long): Boolean = userId in adminUserIds

    val adminCount: Int
        get() = adminUserIds.size

    companion object {
        fun empty(): AdminAuthorizer = AdminAuthorizer(emptySet())

        fun fromFile(
            file: File,
            log: (String) -> Unit = ::println
        ): AdminAuthorizer {
            if (!file.isFile) {
                log("Bot admin list unavailable; admin commands disabled")
                return empty()
            }

            val ids = linkedSetOf<Long>()
            var invalidEntries = 0
            val loaded = runCatching {
                file.useLines { lines ->
                    lines.forEach { rawLine ->
                        val line = rawLine.substringBefore('#').trim()
                        if (line.isBlank()) return@forEach

                        val userId = line.toLongOrNull()
                        if (userId == null || userId <= 0L) {
                            invalidEntries += 1
                        } else {
                            ids += userId
                        }
                    }
                }
            }
            if (loaded.isFailure) {
                log("Bot admin list unreadable; admin commands disabled")
                return empty()
            }

            if (invalidEntries > 0) {
                log("Bot admin list ignored $invalidEntries invalid entries")
            }
            log("Bot admin authorization loaded (${ids.size} IDs)")
            return AdminAuthorizer(ids)
        }
    }
}
