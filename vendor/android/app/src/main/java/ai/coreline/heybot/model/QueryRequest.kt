package ai.coreline.heybot.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonPrimitive

@Serializable
data class QueryRequest(
    val query: String,
    val bind: List<JsonPrimitive>? = null
)
