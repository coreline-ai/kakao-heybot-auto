package ai.coreline.heybot

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class ConversationProxyClient(
    private val settings: ConversationProxySettings,
    private val engine: ConversationEngine,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        // OkHttp's default read timeout is 10 seconds.  Codex/Grok text
        // responses are synchronous CLI jobs and can legitimately stay quiet
        // longer than that while the provider is working.  Apply the same
        // bounded budget to every socket phase so the Android route does not
        // fail even though the manager/provider request is still healthy.
        .connectTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .readTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .writeTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : ConversationGateway {
    override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> = withContext(Dispatchers.IO) {
        runCatching {
            val authorization = settings.authorizationHeader().getOrThrow()
            val body = ConversationRequestDto(
                requestId = "iris-${System.nanoTime()}",
                engine = engine.wireValue,
                kind = request.kind.name,
                promptVersion = "heybot-conversation-v1",
                messages = request.messages.map { ConversationMessageDto(it.role, it.content) }
            )
            val started = System.nanoTime()
            val response = client.newCall(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/conversation/respond")
                    .header("Authorization", authorization)
                    .header("Content-Type", "application/json")
                    .post(json.encodeToString(ConversationRequestDto.serializer(), body).toRequestBody(JSON))
                    .build()
            ).execute()
            response.use {
                val responseBody = it.body?.string().orEmpty()
                if (!it.isSuccessful) {
                    val error = runCatching { json.decodeFromString(ConversationErrorDto.serializer(), responseBody) }.getOrNull()
                    throw GlmFailure.Proxy(error?.error?.code ?: "CONVERSATION_PROXY_HTTP_${it.code}")
                }
                val parsed = json.decodeFromString(ConversationResponseDto.serializer(), responseBody)
                if (parsed.text.isBlank() || parsed.text.length > request.maxTokens * 8) {
                    throw GlmFailure.InvalidResponse(IllegalStateException("conversation text output invalid"))
                }
                GlmChatResponse(
                    content = parsed.text.trim(),
                    requestId = parsed.requestId,
                    finishReason = "stop",
                    promptTokens = null,
                    completionTokens = null,
                    totalTokens = null,
                    latencyMillis = parsed.latencyMillis ?: ((System.nanoTime() - started) / 1_000_000L)
                )
            }
        }.recoverCatching { throwable ->
            when (throwable) {
                is GlmFailure -> throw throwable
                is java.net.SocketTimeoutException -> throw GlmFailure.Timeout(throwable)
                is java.io.IOException -> throw GlmFailure.Network(throwable)
                else -> throw GlmFailure.Unknown(throwable)
            }
        }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

@Serializable
private data class ConversationRequestDto(
    val requestId: String,
    val engine: String,
    val kind: String,
    val promptVersion: String,
    val messages: List<ConversationMessageDto>
)

@Serializable
private data class ConversationMessageDto(val role: String, val content: String)

@Serializable
private data class ConversationResponseDto(
    val requestId: String? = null,
    val engine: String? = null,
    val text: String,
    val latencyMillis: Long? = null
)

@Serializable
private data class ConversationErrorDto(val error: ConversationErrorBody? = null)

@Serializable
private data class ConversationErrorBody(val code: String? = null)
