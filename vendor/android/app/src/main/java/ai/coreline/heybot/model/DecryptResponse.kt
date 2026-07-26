package ai.coreline.heybot.model

import kotlinx.serialization.Serializable

@Serializable
data class DecryptResponse(
    val plain_text: String
)