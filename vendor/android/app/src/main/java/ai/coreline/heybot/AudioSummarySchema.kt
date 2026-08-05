package ai.coreline.heybot

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Strict, versioned LLM boundary for audio summaries. Transcript text never
 * becomes executable instruction; the only references permitted in claims are
 * IDs supplied by the STT result for this one job.
 */
@Serializable
data class AudioEvidenceMap(
    val version: Int,
    val facts: List<AudioEvidenceClaim>,
    val warnings: List<String>
)

@Serializable
data class AudioEvidenceClaim(
    val text: String,
    val evidence: List<String>
)

@Serializable
data class AudioSummaryActionItem(
    val text: String,
    val owner: String?,
    val dueAt: String?,
    val evidence: List<String>
)

@Serializable
data class AudioSummaryDocument(
    val version: Int,
    val pattern: String,
    val view: String,
    val title: String,
    val oneLine: String,
    val oneLineEvidence: List<String>,
    val keyPoints: List<AudioEvidenceClaim>,
    val decisions: List<AudioEvidenceClaim>,
    val actionItems: List<AudioSummaryActionItem>,
    val openQuestions: List<AudioEvidenceClaim>,
    val warnings: List<String>
)

object AudioSummarySchema {
    private const val VERSION = 1
    private const val MAX_FACTS = 24
    private const val MAX_CLAIMS_PER_SECTION = 8
    private const val MAX_ACTIONS = 8
    private const val MAX_CLAIM_CHARS = 300
    private const val MAX_TITLE_CHARS = 100
    private const val MAX_ONE_LINE_CHARS = 240
    private const val MAX_OWNER_CHARS = 80
    private const val MAX_DUE_AT_CHARS = 80
    private const val MAX_WARNINGS = 8
    private val SEGMENT_ID = Regex("S[0-9]{4}")
    private val WARNING = Regex("[A-Z0-9_.-]{1,64}")
    private val json = Json {
        ignoreUnknownKeys = false
        isLenient = false
        explicitNulls = true
    }

    fun decodeEvidenceMap(raw: String, allowedSegmentIds: Set<String>): AudioEvidenceMap = validated {
        val document = decode<AudioEvidenceMap>(raw)
        require(document.version == VERSION)
        require(document.facts.size <= MAX_FACTS)
        require(document.facts.map { it.text.trim() }.distinct().size == document.facts.size)
        document.facts.forEach { validateClaim(it, allowedSegmentIds) }
        validateWarnings(document.warnings)
        document.copy(
            facts = document.facts.map(::normalizeClaim),
            warnings = document.warnings.map(String::trim)
        )
    }

    fun decodeSummary(
        raw: String,
        profile: AudioSummaryProfile,
        allowedSegmentIds: Set<String>
    ): AudioSummaryDocument = validated {
        val document = decode<AudioSummaryDocument>(raw)
        require(document.version == VERSION)
        require(document.pattern == profile.pattern.wireValue)
        require(document.view == profile.view.wireValue)
        require(document.title.isBoundedText(MAX_TITLE_CHARS))
        require(document.oneLine.isBoundedText(MAX_ONE_LINE_CHARS))
        validateEvidence(document.oneLineEvidence, allowedSegmentIds)
        require(document.keyPoints.size <= MAX_CLAIMS_PER_SECTION)
        require(document.decisions.size <= MAX_CLAIMS_PER_SECTION)
        require(document.openQuestions.size <= MAX_CLAIMS_PER_SECTION)
        require(document.actionItems.size <= MAX_ACTIONS)
        document.keyPoints.forEach { validateClaim(it, allowedSegmentIds) }
        document.decisions.forEach { validateClaim(it, allowedSegmentIds) }
        document.openQuestions.forEach { validateClaim(it, allowedSegmentIds) }
        document.actionItems.forEach { validateAction(it, allowedSegmentIds) }
        validateWarnings(document.warnings)
        val allClaims = (document.keyPoints + document.decisions + document.openQuestions)
            .map { it.text.trim() }
        require(allClaims.distinct().size == allClaims.size)
        document.copy(
            title = document.title.trim(),
            oneLine = document.oneLine.trim(),
            oneLineEvidence = document.oneLineEvidence.map(String::trim),
            keyPoints = document.keyPoints.map(::normalizeClaim),
            decisions = document.decisions.map(::normalizeClaim),
            openQuestions = document.openQuestions.map(::normalizeClaim),
            actionItems = document.actionItems.map(::normalizeAction),
            warnings = document.warnings.map(String::trim)
        )
    }

    fun evidenceMapContract(): String =
        """{"version":1,"facts":[{"text":"사실","evidence":["S0001"]}],"warnings":[]}"""

    fun summaryContract(profile: AudioSummaryProfile): String =
        """{"version":1,"pattern":"${profile.pattern.wireValue}","view":"${profile.view.wireValue}","title":"제목","oneLine":"한 줄 요약","oneLineEvidence":["S0001"],"keyPoints":[{"text":"핵심","evidence":["S0001"]}],"decisions":[],"actionItems":[{"text":"명시된 액션","owner":null,"dueAt":null,"evidence":["S0001"]}],"openQuestions":[],"warnings":[]}"""

    private inline fun <T> validated(block: () -> T): T = try {
        block()
    } catch (_: Throwable) {
        throw IllegalArgumentException("SUMMARY_OUTPUT_INVALID")
    }

    private inline fun <reified T> decode(raw: String): T = try {
        require(raw.length in 2..MAX_JSON_CHARS)
        json.decodeFromString<T>(raw.trim())
    } catch (_: Throwable) {
        throw IllegalArgumentException("SUMMARY_OUTPUT_INVALID")
    }

    private fun validateClaim(claim: AudioEvidenceClaim, allowedSegmentIds: Set<String>) {
        require(claim.text.isBoundedText(MAX_CLAIM_CHARS))
        validateEvidence(claim.evidence, allowedSegmentIds)
    }

    private fun validateAction(action: AudioSummaryActionItem, allowedSegmentIds: Set<String>) {
        require(action.text.isBoundedText(MAX_CLAIM_CHARS))
        action.owner?.let { require(it.isBoundedText(MAX_OWNER_CHARS)) }
        action.dueAt?.let { require(it.isBoundedText(MAX_DUE_AT_CHARS)) }
        // Non-null owner/due values are permitted only together with explicit
        // evidence. The model is instructed to emit null when the transcript
        // does not state them; the validator rejects ungrounded empty refs.
        validateEvidence(action.evidence, allowedSegmentIds)
    }

    private fun validateEvidence(evidence: List<String>, allowedSegmentIds: Set<String>) {
        require(evidence.size in 1..4)
        val normalized = evidence.map(String::trim)
        require(normalized.distinct().size == normalized.size)
        require(normalized.all { it.matches(SEGMENT_ID) && it in allowedSegmentIds })
    }

    private fun validateWarnings(warnings: List<String>) {
        require(warnings.size <= MAX_WARNINGS)
        require(warnings.map(String::trim).all { it.matches(WARNING) })
    }

    private fun normalizeClaim(value: AudioEvidenceClaim) = value.copy(
        text = value.text.trim(), evidence = value.evidence.map(String::trim)
    )

    private fun normalizeAction(value: AudioSummaryActionItem) = value.copy(
        text = value.text.trim(),
        owner = value.owner?.trim()?.takeIf(String::isNotBlank),
        dueAt = value.dueAt?.trim()?.takeIf(String::isNotBlank),
        evidence = value.evidence.map(String::trim)
    )

    private fun String.isBoundedText(maxChars: Int): Boolean {
        val normalized = trim()
        return normalized.isNotBlank() && normalized.length <= maxChars &&
            '\u0000' !in normalized && '\n' !in normalized && '\r' !in normalized
    }

    private const val MAX_JSON_CHARS = 24_000
}

object AudioSummaryRenderer {
    fun render(document: AudioSummaryDocument): String = buildList {
        add(document.title)
        add("한 줄 요약: ${document.oneLine} ${evidence(document.oneLineEvidence)}")
        addClaims("핵심", document.keyPoints)
        addClaims("결정", document.decisions)
        if (document.actionItems.isNotEmpty()) {
            add("액션")
            document.actionItems.forEach { action ->
                val details = listOfNotNull(
                    action.owner?.let { "담당: $it" },
                    action.dueAt?.let { "기한: $it" }
                ).joinToString(" · ")
                add("• ${action.text}${details.takeIf(String::isNotBlank)?.let { " ($it)" }.orEmpty()} ${evidence(action.evidence)}")
            }
        }
        addClaims("확인 필요", document.openQuestions)
        document.warnings.takeIf { it.isNotEmpty() }?.let { add("주의: ${it.joinToString(", ")}") }
    }.joinToString("\n")

    fun contextText(document: AudioSummaryDocument): String = buildString {
        append(document.title).append('\n')
        append(document.oneLine)
        document.keyPoints.take(3).forEach { append("\n• ").append(it.text) }
        document.decisions.take(2).forEach { append("\n• 결정: ").append(it.text) }
        document.actionItems.take(2).forEach { append("\n• 다음: ").append(it.text) }
    }.take(MAX_CONTEXT_CHARS)

    private fun MutableList<String>.addClaims(title: String, claims: List<AudioEvidenceClaim>) {
        if (claims.isEmpty()) return
        add(title)
        claims.forEach { add("• ${it.text} ${evidence(it.evidence)}") }
    }

    private fun evidence(ids: List<String>) = "[${ids.joinToString(",")}]"

    private const val MAX_CONTEXT_CHARS = 900
}
