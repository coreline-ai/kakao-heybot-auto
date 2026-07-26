package ai.coreline.heybot.model

import kotlinx.serialization.Serializable

@Serializable
data class CommonErrorResponse(
    val status: Boolean = false,
    val message: String,
)