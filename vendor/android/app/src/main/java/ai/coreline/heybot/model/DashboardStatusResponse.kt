package ai.coreline.heybot.model

import kotlinx.serialization.Serializable

@Serializable
data class DashboardStatusResponse(
    val isObserving: Boolean, val statusMessage: String, val lastLogs: List<Map<String, String?>>
)
