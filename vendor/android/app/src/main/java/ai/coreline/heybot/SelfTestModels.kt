package ai.coreline.heybot

import kotlinx.serialization.Serializable

@Serializable
enum class SelfTestMode(val wireName: String, val displayName: String) {
    QUICK("quick", "빠른"),
    INTEGRATION("integration", "통합"),
    DEVICE("device", "기기"),
    CANARY("canary", "카나리");

    companion object {
        fun parse(value: String?): SelfTestMode? = when (value?.trim()?.lowercase()) {
            null, "", "quick", "빠른" -> QUICK
            "integration", "통합" -> INTEGRATION
            "device", "기기" -> DEVICE
            "canary", "카나리" -> CANARY
            else -> null
        }
    }
}

@Serializable
enum class SelfTestStatus {
    PASS,
    WARN,
    FAIL,
    SKIP
}

@Serializable
data class SelfTestCaseResult(
    val id: String,
    val status: SelfTestStatus,
    val code: String,
    val latencyMillis: Long
)

@Serializable
data class SelfTestReport(
    val runId: String,
    val mode: SelfTestMode,
    val status: SelfTestStatus,
    val runnerVersion: String,
    val startedAtMillis: Long,
    val finishedAtMillis: Long,
    val cases: List<SelfTestCaseResult>
) {
    fun render(maxChars: Int = 440): String {
        val header = "헤이봇 자체진단 ${mode.displayName} ${status.name} ($runId)"
        val body = cases.joinToString("\n") {
            "${it.status.name} ${it.id} ${it.code} ${it.latencyMillis}ms"
        }
        return "$header\n$body".take(maxChars)
    }
}

data class SelfTestCaseDefinition(
    val id: String,
    val modes: Set<SelfTestMode>,
    val action: suspend () -> SelfTestCaseResult
)
