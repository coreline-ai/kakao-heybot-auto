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

data class VideoProxyJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val fileHref: String?
)

interface VideoProxyGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<VideoProxyJob>

    suspend fun status(jobId: String, chatId: Long): Result<VideoProxyJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<VideoProxyJob>
    suspend fun download(jobId: String, chatId: Long): Result<ByteArray>
}

class VideoProxyClient(
    private val settings: VideoProxySettings,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : VideoProxyGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<VideoProxyJob> = withContext(Dispatchers.IO) {
        runCatching {
            val body = VideoCreateRequest(
                requestId = requestId,
                chatId = chatId.toString(),
                userId = userId.toString(),
                logId = logId.toString(),
                prompt = prompt
            )
            executeJson(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/video/jobs")
                    .post(
                        json.encodeToString(VideoCreateRequest.serializer(), body)
                            .toRequestBody(JSON_MEDIA_TYPE)
                    )
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<VideoProxyJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                executeJson(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId))
                        .get()
                )
            }
        }

    override suspend fun cancel(jobId: String, chatId: Long): Result<VideoProxyJob> =
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
                        throw VideoProxyFailure.Http(response.code)
                    }
                    if (response.header("Content-Type")?.substringBefore(';') != "video/mp4") {
                        throw VideoProxyFailure.InvalidVideo()
                    }
                    val declared = response.body?.contentLength() ?: -1L
                    if (declared > settings.videoMaxBytes) {
                        throw VideoProxyFailure.VideoTooLarge()
                    }
                    val source = response.body?.byteStream()
                        ?: throw VideoProxyFailure.InvalidVideo()
                    val output = ByteArrayOutputStream()
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val read = source.read(buffer)
                        if (read < 0) break
                        if (output.size() + read > settings.videoMaxBytes) {
                            throw VideoProxyFailure.VideoTooLarge()
                        }
                        output.write(buffer, 0, read)
                    }
                    output.toByteArray()
                }
            }
        }

    private fun executeJson(builder: Request.Builder): VideoProxyJob {
        val request = authorized(builder).build()
        return client.newCall(request).execute().use { response ->
            val raw = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching {
                    json.decodeFromString(VideoErrorResponse.serializer(), raw).error.code
                }.getOrNull()
                throw VideoProxyFailure.Api(response.code, error)
            }
            val dto = json.decodeFromString(VideoJobResponse.serializer(), raw)
            VideoProxyJob(
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
        "${settings.baseUrl}/v1/video/jobs/$jobId$suffix?chatId=$chatId"

    private companion object {
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

sealed class VideoProxyFailure(message: String) : RuntimeException(message) {
    class Http(val status: Int) : VideoProxyFailure("Video proxy HTTP $status")
    class Api(val status: Int, val code: String?) :
        VideoProxyFailure(code ?: "Video proxy API $status")
    class InvalidVideo : VideoProxyFailure("Downloaded video is invalid")
    class VideoTooLarge : VideoProxyFailure("Downloaded video is too large")
}

@Serializable
private data class VideoCreateRequest(
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val prompt: String
)

@Serializable
private data class VideoJobResponse(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: VideoError? = null,
    val file: VideoFile? = null
)

@Serializable
private data class VideoFile(
    val href: String,
    val mediaType: String? = null,
    val bytes: Long? = null,
    val sha256: String? = null
)

@Serializable
private data class VideoError(val code: String)

@Serializable
private data class VideoErrorResponse(val error: VideoError)
