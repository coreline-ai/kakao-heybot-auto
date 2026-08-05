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

data class YoutubeDownloadProxyJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val fileHref: String?
)

interface YoutubeDownloadProxyGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        url: String
    ): Result<YoutubeDownloadProxyJob>

    suspend fun status(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob>
    suspend fun download(jobId: String, chatId: Long): Result<ByteArray>
}

class YoutubeDownloadProxyClient(
    private val settings: YoutubeDownloadProxySettings,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : YoutubeDownloadProxyGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        url: String
    ): Result<YoutubeDownloadProxyJob> = withContext(Dispatchers.IO) {
        runCatching {
            val body = YoutubeDownloadCreateRequest(
                requestId = requestId,
                chatId = chatId.toString(),
                userId = userId.toString(),
                logId = logId.toString(),
                url = url
            )
            executeJson(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/youtube/jobs")
                    .post(
                        json.encodeToString(YoutubeDownloadCreateRequest.serializer(), body)
                            .toRequestBody(JSON_MEDIA_TYPE)
                    )
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                executeJson(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId))
                        .get()
                )
            }
        }

    override suspend fun cancel(jobId: String, chatId: Long): Result<YoutubeDownloadProxyJob> =
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
                        throw YoutubeDownloadProxyFailure.Http(response.code)
                    }
                    if (response.header("Content-Type")?.substringBefore(';') != "video/mp4") {
                        throw YoutubeDownloadProxyFailure.InvalidVideo()
                    }
                    val declared = response.body?.contentLength() ?: -1L
                    if (declared > settings.youtubeDownloadMaxBytes) {
                        throw YoutubeDownloadProxyFailure.VideoTooLarge()
                    }
                    val source = response.body?.byteStream()
                        ?: throw YoutubeDownloadProxyFailure.InvalidVideo()
                    val output = ByteArrayOutputStream()
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val read = source.read(buffer)
                        if (read < 0) break
                        if (output.size() + read > settings.youtubeDownloadMaxBytes) {
                            throw YoutubeDownloadProxyFailure.VideoTooLarge()
                        }
                        output.write(buffer, 0, read)
                    }
                    output.toByteArray()
                }
            }
        }

    private fun executeJson(builder: Request.Builder): YoutubeDownloadProxyJob {
        val request = authorized(builder).build()
        return client.newCall(request).execute().use { response ->
            val raw = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching {
                    json.decodeFromString(YoutubeDownloadErrorResponse.serializer(), raw).error.code
                }.getOrNull()
                throw YoutubeDownloadProxyFailure.Api(response.code, error)
            }
            val dto = json.decodeFromString(YoutubeDownloadJobResponse.serializer(), raw)
            YoutubeDownloadProxyJob(
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
        "${settings.baseUrl}/v1/youtube/jobs/$jobId$suffix?chatId=$chatId"

    private companion object {
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

sealed class YoutubeDownloadProxyFailure(message: String) : RuntimeException(message) {
    class Http(val status: Int) : YoutubeDownloadProxyFailure("YoutubeDownload proxy HTTP $status")
    class Api(val status: Int, val code: String?) :
        YoutubeDownloadProxyFailure(code ?: "YoutubeDownload proxy API $status")
    class InvalidVideo : YoutubeDownloadProxyFailure("Downloaded youtubeDownload is invalid")
    class VideoTooLarge : YoutubeDownloadProxyFailure("Downloaded youtubeDownload is too large")
}

@Serializable
private data class YoutubeDownloadCreateRequest(
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val url: String
)

@Serializable
private data class YoutubeDownloadJobResponse(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: YoutubeDownloadError? = null,
    val file: YoutubeDownloadFile? = null
)

@Serializable
private data class YoutubeDownloadFile(
    val href: String,
    val mediaType: String? = null,
    val bytes: Long? = null,
    val sha256: String? = null
)

@Serializable
private data class YoutubeDownloadError(val code: String)

@Serializable
private data class YoutubeDownloadErrorResponse(val error: YoutubeDownloadError)
