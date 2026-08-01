package ai.coreline.heybot

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

data class ImageAnalysisResult(
    val summary: String,
    val visibleObjects: List<String>,
    val visibleText: List<String>,
    val uncertainty: String
)

data class ImageAnalysisJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val result: ImageAnalysisResult?
)

interface ImageAnalysisGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        source: IncomingImageAttachment
    ): Result<ImageAnalysisJob>

    suspend fun status(jobId: String, chatId: Long): Result<ImageAnalysisJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<ImageAnalysisJob>
}

class ImageAnalysisProxyClient(
    private val settings: ImageAnalysisSettings,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : ImageAnalysisGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        source: IncomingImageAttachment
    ): Result<ImageAnalysisJob> = withContext(Dispatchers.IO) {
        runCatching {
            execute(
                Request.Builder().url("${settings.baseUrl}/v1/vision/jobs").post(
                    json.encodeToString(
                        VisionCreateRequest.serializer(),
                        VisionCreateRequest(
                            requestId = requestId,
                            chatId = chatId.toString(),
                            userId = userId.toString(),
                            logId = source.sourceLogId.toString(),
                            source = VisionSource(
                                url = source.url,
                                width = source.width,
                                height = source.height,
                                declaredBytes = source.declaredBytes,
                                expiresAtMillis = source.expiresAtMillis
                            )
                        )
                    ).toRequestBody(JSON)
                )
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<ImageAnalysisJob> =
        request("${settings.baseUrl}/v1/vision/jobs/$jobId?chatId=$chatId", "GET")

    override suspend fun cancel(jobId: String, chatId: Long): Result<ImageAnalysisJob> =
        request("${settings.baseUrl}/v1/vision/jobs/$jobId?chatId=$chatId", "DELETE")

    private suspend fun request(url: String, method: String): Result<ImageAnalysisJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                val builder = Request.Builder().url(url)
                if (method == "DELETE") builder.delete() else builder.get()
                execute(builder)
            }
        }

    private fun execute(builder: Request.Builder): ImageAnalysisJob {
        val authorization = settings.authorizationHeader().getOrThrow()
        return client.newCall(builder.header("Authorization", authorization).build()).execute().use {
            val raw = it.body?.string().orEmpty()
            if (!it.isSuccessful) throw IllegalStateException("VISION_HTTP_${it.code}")
            val body = json.decodeFromString(VisionJobResponse.serializer(), raw)
            ImageAnalysisJob(
                jobId = body.jobId,
                requestId = body.requestId,
                chatId = body.chatId,
                status = body.status,
                errorCode = body.error?.code,
                result = body.result?.let { result ->
                    ImageAnalysisResult(
                        result.summary,
                        result.visibleObjects,
                        result.visibleText,
                        result.uncertainty
                    )
                }
            )
        }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

@Serializable
private data class VisionCreateRequest(
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val source: VisionSource
)

@Serializable
private data class VisionSource(
    val url: String,
    val width: Int,
    val height: Int,
    val declaredBytes: Long,
    val expiresAtMillis: Long
)

@Serializable
private data class VisionJobResponse(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: VisionError? = null,
    val result: VisionResult? = null
)

@Serializable private data class VisionError(val code: String)

@Serializable
private data class VisionResult(
    val version: Int,
    val summary: String,
    val visibleObjects: List<String> = emptyList(),
    val visibleText: List<String> = emptyList(),
    val uncertainty: String
)
