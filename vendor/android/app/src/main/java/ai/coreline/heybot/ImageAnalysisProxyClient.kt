package ai.coreline.heybot

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

enum class VisionTask(val wireValue: String) {
    DESCRIBE("describe"),
    OCR("ocr"),
    TRANSLATE_KO("translate_ko");

    companion object {
        fun fromWire(value: String?): VisionTask? = entries.firstOrNull { it.wireValue == value }
    }
}

data class ImageAnalysisResult(
    val version: Int,
    val task: VisionTask,
    val answer: String,
    val visibleObjects: List<String>,
    val extractedText: List<String>,
    val uncertainty: String
) {
    val summary: String get() = answer
    val visibleText: List<String> get() = extractedText

    constructor(
        summary: String,
        visibleObjects: List<String>,
        visibleText: List<String>,
        uncertainty: String
    ) : this(1, VisionTask.DESCRIBE, summary, visibleObjects, visibleText, uncertainty)
}

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
        source: IncomingImageAttachment,
        task: VisionTask = VisionTask.DESCRIBE
    ): Result<ImageAnalysisJob>

    suspend fun status(jobId: String, chatId: Long): Result<ImageAnalysisJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<ImageAnalysisJob>
}

sealed class VisionProxyException(
    val reasonCode: String,
    cause: Throwable? = null
) : RuntimeException(reasonCode, cause)

class VisionTransportException(cause: Throwable) :
    VisionProxyException("VISION_TRANSPORT_UNAVAILABLE", cause)

class VisionAuthorizationException(val statusCode: Int) :
    VisionProxyException("VISION_ROUTE_UNAUTHORIZED")

class VisionHttpException(val statusCode: Int) :
    VisionProxyException("VISION_HTTP_$statusCode")

class VisionConfigurationException(cause: Throwable) :
    VisionProxyException("VISION_CONFIGURATION_INVALID", cause)

class VisionInvalidResponseException(cause: Throwable? = null) :
    VisionProxyException("VISION_RESULT_INVALID", cause)

fun Throwable.isRetryableVisionTransport(): Boolean = this is VisionTransportException

fun Throwable.visionReasonCode(): String =
    (this as? VisionProxyException)?.reasonCode ?: "VISION_CREATE_FAILED"

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
        source: IncomingImageAttachment,
        task: VisionTask
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
                            task = task.wireValue,
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
        val authorization = settings.authorizationHeader().getOrElse {
            throw VisionConfigurationException(it)
        }
        try {
            return client.newCall(builder.header("Authorization", authorization).build()).execute().use {
                val raw = it.body?.string().orEmpty()
                if (!it.isSuccessful) {
                    if (it.code == 401 || it.code == 403) {
                        throw VisionAuthorizationException(it.code)
                    }
                    throw VisionHttpException(it.code)
                }
                decodeJob(raw)
            }
        } catch (error: VisionProxyException) {
            throw error
        } catch (error: IOException) {
            throw VisionTransportException(error)
        } catch (error: Exception) {
            throw VisionInvalidResponseException(error)
        }
    }

    private fun decodeJob(raw: String): ImageAnalysisJob {
        val body = json.decodeFromString(VisionJobResponse.serializer(), raw)
        return ImageAnalysisJob(
            jobId = body.jobId,
            requestId = body.requestId,
            chatId = body.chatId,
            status = body.status,
            errorCode = body.error?.code,
            result = body.result?.let { result ->
                val task = when (result.version) {
                    1 -> {
                        if (result.task != null && result.task != VisionTask.DESCRIBE.wireValue) {
                            throw VisionInvalidResponseException()
                        }
                        VisionTask.DESCRIBE
                    }
                    2 -> VisionTask.fromWire(result.task)
                        ?: throw VisionInvalidResponseException()
                    else -> throw VisionInvalidResponseException()
                }
                val answer = when (result.version) {
                    1 -> result.summary ?: result.answer
                    2 -> result.answer
                    else -> null
                } ?: throw VisionInvalidResponseException()
                val extractedText = result.extractedText.ifEmpty { result.visibleText }
                if (
                    answer.isBlank() || answer.length > 480 ||
                    result.visibleObjects.size > 20 ||
                    result.visibleObjects.any { it.isBlank() || it.length > 80 } ||
                    extractedText.size > 20 ||
                    extractedText.any { it.isBlank() || it.length > 120 } ||
                    result.uncertainty !in setOf("low", "medium", "high")
                ) throw VisionInvalidResponseException()
                ImageAnalysisResult(
                    version = result.version,
                    task = task,
                    answer = answer,
                    visibleObjects = result.visibleObjects,
                    extractedText = extractedText,
                    uncertainty = result.uncertainty
                )
            }
        )
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
    val task: String,
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
    val task: String? = null,
    val answer: String? = null,
    val summary: String? = null,
    val visibleObjects: List<String> = emptyList(),
    val visibleText: List<String> = emptyList(),
    val extractedText: List<String> = emptyList(),
    val uncertainty: String
)
