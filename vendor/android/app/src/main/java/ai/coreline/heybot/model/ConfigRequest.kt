package ai.coreline.heybot.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.LongAsStringSerializer
import ai.coreline.heybot.util.IntAsStringSerializer

@Serializable
data class ConfigRequest(
    val endpoint: String? = null,
    val botname: String? = null,
    @Serializable(with = LongAsStringSerializer::class)
    val rate: Long? = null,
    @Serializable(with = IntAsStringSerializer::class)
    val port: Int? = null
)
