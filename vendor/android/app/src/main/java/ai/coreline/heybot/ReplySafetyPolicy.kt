package ai.coreline.heybot

enum class ReplySafetyBlockReason {
    EMPTY,
    SECRET_LIKE
}

enum class ReplyRedaction {
    EMAIL,
    PHONE,
    RESIDENT_ID,
    CARD
}

sealed interface ReplySafetyResult {
    data class Safe(
        val text: String,
        val redactions: Set<ReplyRedaction>
    ) : ReplySafetyResult

    data class Blocked(val reason: ReplySafetyBlockReason) : ReplySafetyResult
}

/**
 * Deterministic final boundary for text produced by an external language model.
 * It never logs or retains the supplied text.
 */
class ReplySafetyPolicy(
    private val maxChars: Int = DEFAULT_MAX_CHARS
) {
    fun apply(raw: String): ReplySafetyResult {
        var normalized = raw
            .replace(THINKING_BLOCK, " ")
            .replace("```", " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (normalized.isEmpty()) return ReplySafetyResult.Blocked(ReplySafetyBlockReason.EMPTY)
        if (SECRET_PATTERNS.any { it.containsMatchIn(normalized) }) {
            return ReplySafetyResult.Blocked(ReplySafetyBlockReason.SECRET_LIKE)
        }

        val redactions = linkedSetOf<ReplyRedaction>()
        normalized = redact(normalized, RESIDENT_ID, ReplyRedaction.RESIDENT_ID, "[주민번호 마스킹]", redactions)
        normalized = redact(normalized, CARD_NUMBER, ReplyRedaction.CARD, "[카드번호 마스킹]", redactions)
        normalized = redact(normalized, EMAIL, ReplyRedaction.EMAIL, "[이메일 마스킹]", redactions)
        normalized = redact(normalized, PHONE, ReplyRedaction.PHONE, "[전화번호 마스킹]", redactions)
        normalized = normalized.take(maxChars).trim()
        if (normalized.isEmpty()) return ReplySafetyResult.Blocked(ReplySafetyBlockReason.EMPTY)
        return ReplySafetyResult.Safe(normalized, redactions)
    }

    private fun redact(
        input: String,
        pattern: Regex,
        category: ReplyRedaction,
        replacement: String,
        redactions: MutableSet<ReplyRedaction>
    ): String {
        if (!pattern.containsMatchIn(input)) return input
        redactions += category
        return input.replace(pattern, replacement)
    }

    private companion object {
        const val DEFAULT_MAX_CHARS = 480
        val THINKING_BLOCK = Regex("(?is)<think>.*?</think>")
        val SECRET_PATTERNS = listOf(
            Regex("(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]{8,}"),
            Regex("(?i)\\bAuthorization\\s*[:=]"),
            Regex("(?i)\\b(?:api[_ -]?key|token|secret|password)\\s*[:=]\\s*\\S+"),
            Regex("\\bIRIS_[A-Z0-9_]+\\s*="),
            Regex("(?i)/data/local/private(?:/|\\b)"),
            Regex("(?i)\\broot\\s*:\\s*root\\b")
        )
        val RESIDENT_ID = Regex("(?<!\\d)\\d{6}[- ]?[1-4]\\d{6}(?!\\d)")
        val CARD_NUMBER = Regex("(?<!\\d)(?:\\d{4}[- ]?){3}\\d{4}(?!\\d)")
        val EMAIL = Regex(
            "(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}(?![A-Z0-9._%+-])"
        )
        val PHONE = Regex(
            "(?<!\\d)(?:01[016789][- ]?\\d{3,4}[- ]?\\d{4}|0\\d{1,2}[- ]?\\d{3,4}[- ]?\\d{4})(?!\\d)"
        )
    }
}
