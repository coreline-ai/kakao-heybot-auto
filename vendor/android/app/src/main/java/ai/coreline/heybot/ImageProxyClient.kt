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
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

data class ImageProxyJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val fileHref: String?
)

interface ImageProxyGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<ImageProxyJob>

    suspend fun status(jobId: String, chatId: Long): Result<ImageProxyJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<ImageProxyJob>
    suspend fun download(jobId: String, chatId: Long): Result<ByteArray>
}

class ImageProxyClient(
    private val settings: ImageProxySettings,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : ImageProxyGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<ImageProxyJob> = withContext(Dispatchers.IO) {
        runCatching {
            val body = ImageCreateRequest(
                requestId = requestId,
                chatId = chatId.toString(),
                userId = userId.toString(),
                logId = logId.toString(),
                prompt = prompt
            )
            executeJson(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/image/jobs")
                    .post(
                        json.encodeToString(ImageCreateRequest.serializer(), body)
                            .toRequestBody(JSON_MEDIA_TYPE)
                    )
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<ImageProxyJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                executeJson(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId))
                        .get()
                )
            }
        }

    override suspend fun cancel(jobId: String, chatId: Long): Result<ImageProxyJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                executeJson(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId))
                        .delete()
                )
            }
        }

    override suspend fun download(jobId: String, chatId: Long): Result<ByteArray> =
        withContext(Dispatchers.IO) {
            runCatching {
                val request = authorized(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId, "/file"))
                        .get()
                ).build()
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        throw ImageProxyFailure.Http(response.code)
                    }
                    if (response.header("Content-Type")?.substringBefore(';') != "image/png") {
                        throw ImageProxyFailure.InvalidImage()
                    }
                    val declared = response.body?.contentLength() ?: -1L
                    if (declared > settings.imageMaxBytes) {
                        throw ImageProxyFailure.ImageTooLarge()
                    }
                    val source = response.body?.byteStream()
                        ?: throw ImageProxyFailure.InvalidImage()
                    val output = ByteArrayOutputStream()
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val read = source.read(buffer)
                        if (read < 0) break
                        if (output.size() + read > settings.imageMaxBytes) {
                            throw ImageProxyFailure.ImageTooLarge()
                        }
                        output.write(buffer, 0, read)
                    }
                    output.toByteArray()
                }
            }
        }

    private fun executeJson(builder: Request.Builder): ImageProxyJob {
        val request = authorized(builder).build()
        return client.newCall(request).execute().use { response ->
            val raw = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching {
                    json.decodeFromString(ImageErrorResponse.serializer(), raw).error.code
                }.getOrNull()
                throw ImageProxyFailure.Api(response.code, error)
            }
            val dto = json.decodeFromString(ImageJobResponse.serializer(), raw)
            ImageProxyJob(
                jobId = dto.jobId,
                requestId = dto.requestId,
                chatId = dto.chatId,
                status = dto.status,
                errorCode = dto.error?.code,
                fileHref = dto.file?.href
            )
        }
    }

    private fun authorized(builder: Request.Builder): Request.Builder =
        builder.header("Authorization", settings.authorizationHeader().getOrThrow())

    private fun scopedUrl(jobId: String, chatId: Long, suffix: String = ""): String =
        "${settings.baseUrl}/v1/image/jobs/$jobId$suffix?chatId=$chatId"

    private companion object {
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

sealed class ImageProxyFailure(message: String) : RuntimeException(message) {
    class Http(val status: Int) : ImageProxyFailure("Image proxy HTTP $status")
    class Api(val status: Int, val code: String?) :
        ImageProxyFailure(code ?: "Image proxy API $status")
    class InvalidImage : ImageProxyFailure("Downloaded image is invalid")
    class ImageTooLarge : ImageProxyFailure("Downloaded image is too large")
}

@Serializable
private data class ImageCreateRequest(
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val prompt: String
)

@Serializable
private data class ImageJobResponse(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: ImageError? = null,
    val file: ImageFile? = null
)

@Serializable
private data class ImageFile(
    val href: String,
    val mediaType: String? = null,
    val bytes: Long? = null,
    val sha256: String? = null
)

@Serializable
private data class ImageError(val code: String)

@Serializable
private data class ImageErrorResponse(val error: ImageError)
