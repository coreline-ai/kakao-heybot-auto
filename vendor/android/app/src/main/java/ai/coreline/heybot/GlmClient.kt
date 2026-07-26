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
import java.io.IOException
import java.io.InterruptedIOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

data class GlmMessage(val role: String, val content: String)

enum class GlmRequestKind(val metricLabel: String) {
    WAKE_WORD("wake_word"),
    GENERAL_CONVERSATION("general_conversation")
}

data class GlmChatRequest(
    val model: String,
    val messages: List<GlmMessage>,
    val temperature: Double,
    val maxTokens: Int,
    /** Null uses the process-wide GLM timeout. */
    val timeoutMillis: Long? = null,
    val kind: GlmRequestKind = GlmRequestKind.WAKE_WORD
)

data class GlmChatResponse(
    val content: String,
    val requestId: String?,
    val finishReason: String?,
    val promptTokens: Int?,
    val completionTokens: Int?,
    val totalTokens: Int?,
    val latencyMillis: Long
)

enum class ConversationEngine(val wireValue: String, val displayName: String) {
    GLM("glm", "기본(GLM)"),
    CODEX("codex", "코덱스"),
    GROK("grok", "그록")
}

typealias ConversationMessage = GlmMessage
typealias ConversationRequest = GlmChatRequest
typealias ConversationResponse = GlmChatResponse

fun interface ConversationGateway {
    suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse>
}

typealias GlmGateway = ConversationGateway

class GlmClient(
    private val settings: GlmSettings,
    private val httpClient: OkHttpClient = defaultHttpClient(settings.timeoutMillis),
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val nowMillis: () -> Long = { System.nanoTime() / 1_000_000L }
) : GlmGateway {
    override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> = withContext(Dispatchers.IO) {
        runCatching {
            val authorization = settings.authorizationHeader().getOrThrow()
            val body = GlmChatCompletionRequestDto(
                model = request.model,
                messages = request.messages.map { GlmChatMessageDto(role = it.role, content = it.content) },
                temperature = request.temperature,
                maxTokens = request.maxTokens,
                stream = true,
                thinking = GlmThinkingDto(type = "disabled", clearThinking = true)
            )
            val requestJson = json.encodeToString(GlmChatCompletionRequestDto.serializer(), body)
            val startedAt = nowMillis()
            val httpRequest = Request.Builder()
                .url("${settings.baseUrl}chat/completions")
                .header("Authorization", authorization)
                .header("Content-Type", "application/json")
                // Z.AI sends `stream=true` completions as Server-Sent Events.
                // Advertising JSON here can cause an intermediary to buffer the
                // stream until completion, defeating the chatbot's latency goal.
                .header("Accept", "text/event-stream")
                .post(requestJson.toRequestBody(JSON_MEDIA_TYPE))
                .build()

            val call = httpClient.newCall(httpRequest)
            request.timeoutMillis?.let {
                call.timeout().timeout(it, TimeUnit.MILLISECONDS)
            }
            call.execute().use { response ->
                if (!response.isSuccessful) {
                    throw GlmFailure.fromHttpCode(response.code, response.header("Retry-After"))
                }

                response.readStreamingResponse(startedAt)
            }
        }.recoverCatching { throwable ->
            throw GlmFailure.fromThrowable(throwable)
        }
    }

    companion object {
        internal fun defaultHttpClient(timeoutMillis: Long): OkHttpClient =
            OkHttpClient.Builder()
                // OkHttp defaults read/write to 10 seconds. GLM can take
                // longer before the first SSE chunk, so every transport phase
                // must use the configured request budget.
                .connectTimeout(timeoutMillis, TimeUnit.MILLISECONDS)
                .readTimeout(timeoutMillis, TimeUnit.MILLISECONDS)
                .writeTimeout(timeoutMillis, TimeUnit.MILLISECONDS)
                .callTimeout(timeoutMillis, TimeUnit.MILLISECONDS)
                .build()

        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }

    private fun okhttp3.Response.readStreamingResponse(startedAtMillis: Long): GlmChatResponse {
        val content = StringBuilder()
        var requestId: String? = null
        var finishReason: String? = null
        var promptTokens: Int? = null
        var completionTokens: Int? = null
        var totalTokens: Int? = null

        val source = body?.source() ?: throw GlmFailure.EmptyResponse()
        source.use {
            while (!it.exhausted()) {
                val line = it.readUtf8Line() ?: continue
                if (!line.startsWith("data:")) continue

                val payload = line.removePrefix("data:").trim()
                if (payload == "[DONE]") break
                if (payload.isEmpty()) continue

                val chunk = json.decodeFromString(GlmChatCompletionStreamChunkDto.serializer(), payload)
                requestId = chunk.id ?: requestId
                val choice = chunk.choices.firstOrNull()
                if (choice != null) {
                    content.append(choice.delta.content.orEmpty())
                    finishReason = choice.finishReason ?: finishReason
                }
                promptTokens = chunk.usage?.promptTokens ?: promptTokens
                completionTokens = chunk.usage?.completionTokens ?: completionTokens
                totalTokens = chunk.usage?.totalTokens ?: totalTokens
                // Some compatible SSE gateways keep the HTTP stream open after
                // the terminal choice. The final chunk already contains usage,
                // so do not occupy a room worker until the outer call timeout.
                if (choice?.finishReason != null) break
            }
        }

        val finalContent = content.toString().trim()
        if (finalContent.isBlank()) throw GlmFailure.EmptyResponse()

        return GlmChatResponse(
            content = finalContent,
            requestId = requestId,
            finishReason = finishReason,
            promptTokens = promptTokens,
            completionTokens = completionTokens,
            totalTokens = totalTokens,
            latencyMillis = nowMillis() - startedAtMillis
        )
    }
}

sealed class GlmFailure(message: String, cause: Throwable? = null) : RuntimeException(message, cause) {
    class Unauthorized : GlmFailure("GLM authentication failed")
    class Forbidden : GlmFailure("GLM access was denied")
    class RateLimited(val retryAfterMillis: Long? = null) : GlmFailure("GLM rate limit reached")
    class Server(val statusCode: Int) : GlmFailure("GLM server error: $statusCode")
    class Http(val statusCode: Int) : GlmFailure("GLM HTTP error: $statusCode")
    class Timeout(cause: Throwable) : GlmFailure("GLM request timed out", cause)
    class Network(cause: Throwable) : GlmFailure("GLM network request failed", cause)
    class EmptyResponse : GlmFailure("GLM returned no final content")
    class InvalidResponse(cause: Throwable) : GlmFailure("GLM returned an invalid response", cause)
    class Proxy(val code: String) : GlmFailure("Conversation proxy failure: $code")
    class Unknown(cause: Throwable) : GlmFailure("GLM request failed", cause)

    companion object {
        fun fromHttpCode(code: Int, retryAfterHeader: String? = null): GlmFailure = when (code) {
            401 -> Unauthorized()
            403 -> Forbidden()
            429 -> RateLimited(retryAfterHeader?.trim()?.toLongOrNull()?.times(1_000L))
            in 500..599 -> Server(code)
            else -> Http(code)
        }

        fun fromThrowable(throwable: Throwable): GlmFailure = when (throwable) {
            is GlmFailure -> throwable
            is SocketTimeoutException -> Timeout(throwable)
            is InterruptedIOException -> Timeout(throwable)
            is IOException -> Network(throwable)
            is kotlinx.serialization.SerializationException -> InvalidResponse(throwable)
            else -> Unknown(throwable)
        }
    }
}

@Serializable
private data class GlmChatCompletionRequestDto(
    val model: String,
    val messages: List<GlmChatMessageDto>,
    val temperature: Double,
    @SerialName("max_tokens") val maxTokens: Int,
    val stream: Boolean,
    val thinking: GlmThinkingDto
)

@Serializable
private data class GlmChatMessageDto(
    val role: String,
    val content: String
)

@Serializable
private data class GlmThinkingDto(
    val type: String,
    @SerialName("clear_thinking") val clearThinking: Boolean
)

@Serializable
private data class GlmChatCompletionStreamChunkDto(
    val id: String? = null,
    val choices: List<GlmStreamChoiceDto> = emptyList(),
    val usage: GlmUsageDto? = null
)

@Serializable
private data class GlmStreamChoiceDto(
    @SerialName("finish_reason") val finishReason: String? = null,
    val delta: GlmStreamDeltaDto = GlmStreamDeltaDto()
)

@Serializable
private data class GlmStreamDeltaDto(
    val content: String? = null,
    @SerialName("reasoning_content") val reasoningContent: String? = null
)

@Serializable
private data class GlmUsageDto(
    @SerialName("prompt_tokens") val promptTokens: Int? = null,
    @SerialName("completion_tokens") val completionTokens: Int? = null,
    @SerialName("total_tokens") val totalTokens: Int? = null
)
