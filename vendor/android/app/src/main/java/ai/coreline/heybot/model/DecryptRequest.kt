package ai.coreline.heybot.model

import kotlinx.serialization.Serializable

@Serializable
data class DecryptRequest(
    val b64_ciphertext: String,
    val user_id: Long?,
    val enc: Int,
)