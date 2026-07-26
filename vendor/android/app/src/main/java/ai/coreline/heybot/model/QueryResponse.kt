package ai.coreline.heybot.model

import kotlinx.serialization.Serializable

@Serializable
data class QueryResponse(
    val data: List<Map<String, String?>>
)
