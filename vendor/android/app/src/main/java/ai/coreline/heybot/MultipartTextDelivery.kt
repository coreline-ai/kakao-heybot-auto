package ai.coreline.heybot

/** Unicode-safe bounded Kakao text splitter with stable part numbering. */
object MultipartTextDelivery {
    const val MAX_PART_CHARS = 440
    const val MAX_PARTS = 8

    fun split(text: String, maxParts: Int = MAX_PARTS): List<String> {
        require(maxParts in 1..MAX_PARTS)
        val normalized = text.trim().replace("\r\n", "\n")
        if (normalized.isEmpty()) return emptyList()
        if (normalized.length <= MAX_PART_CHARS) return listOf(normalized)

        val payloadLimit = MAX_PART_CHARS - 12
        val raw = mutableListOf<String>()
        var remainder = normalized
        while (remainder.isNotEmpty() && raw.size < maxParts) {
            if (remainder.length <= payloadLimit) {
                raw += remainder
                remainder = ""
                break
            }
            var cut = remainder.lastIndexOf('\n', payloadLimit)
            if (cut < payloadLimit / 2) cut = remainder.lastIndexOf(' ', payloadLimit)
            if (cut < payloadLimit / 2) cut = payloadLimit
            if (cut < remainder.length && cut > 0 &&
                Character.isHighSurrogate(remainder[cut - 1]) &&
                Character.isLowSurrogate(remainder[cut])
            ) cut--
            raw += remainder.substring(0, cut).trimEnd()
            remainder = remainder.substring(cut).trimStart()
        }
        if (remainder.isNotEmpty()) {
            val last = raw.lastIndex
            val suffix = "\n…(이하 생략)"
            raw[last] = safePrefix(
                raw[last],
                (payloadLimit - suffix.length).coerceAtLeast(1)
            ) + suffix
        }
        return raw.mapIndexed { index, value -> "[${index + 1}/${raw.size}] $value" }
    }

    /** Replays one exact part with an explicit marker while preserving the bound. */
    fun resend(part: String): String {
        val normalized = part.trim()
        val match = PART_HEADER.matchEntire(normalized.substringBefore('\n'))
        val header = if (match != null) {
            "[${match.groupValues[1]}/${match.groupValues[2]}·재전송] "
        } else {
            "[재전송] "
        }
        val payload = if (match != null) normalized.substring(match.value.length).trimStart() else normalized
        return header + safePrefix(payload, (MAX_PART_CHARS - header.length).coerceAtLeast(1))
    }

    private fun safePrefix(value: String, maxChars: Int): String {
        var cut = maxChars.coerceAtMost(value.length)
        if (cut in 1 until value.length &&
            Character.isHighSurrogate(value[cut - 1]) && Character.isLowSurrogate(value[cut])
        ) cut--
        return value.substring(0, cut)
    }

    private val PART_HEADER = Regex("\\[(\\d{1,2})/(\\d{1,2})\\]\\s*(.*)")
}
