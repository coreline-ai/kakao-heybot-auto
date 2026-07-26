package ai.coreline.heybot.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class AotResponse(
    val success: Boolean,
    val aot: JsonObject
)