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

data class PenBrushProxyJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val fileHref: String?
)

interface PenBrushProxyGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<PenBrushProxyJob>

    suspend fun status(jobId: String, chatId: Long): Result<PenBrushProxyJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<PenBrushProxyJob>
    suspend fun download(jobId: String, chatId: Long): Result<ByteArray>
}

class PenBrushProxyClient(
    private val settings: PenBrushProxySettings,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : PenBrushProxyGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        userId: Long,
        logId: Long,
        prompt: String
    ): Result<PenBrushProxyJob> = withContext(Dispatchers.IO) {
        runCatching {
            val body = PenBrushCreateRequest(
                requestId = requestId,
                chatId = chatId.toString(),
                userId = userId.toString(),
                logId = logId.toString(),
                prompt = prompt
            )
            executeJson(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/draw/jobs")
                    .post(
                        json.encodeToString(PenBrushCreateRequest.serializer(), body)
                            .toRequestBody(JSON_MEDIA_TYPE)
                    )
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<PenBrushProxyJob> =
        withContext(Dispatchers.IO) {
            runCatching {
                executeJson(
                    Request.Builder()
                        .url(scopedUrl(jobId, chatId))
                        .get()
                )
            }
        }

    override suspend fun cancel(jobId: String, chatId: Long): Result<PenBrushProxyJob> =
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
                        throw PenBrushProxyFailure.Http(response.code)
                    }
                    if (response.header("Content-Type")?.substringBefore(';') != "video/mp4") {
                        throw PenBrushProxyFailure.InvalidPenBrush()
                    }
                    val declared = response.body?.contentLength() ?: -1L
                    if (declared > settings.videoMaxBytes) {
                        throw PenBrushProxyFailure.PenBrushTooLarge()
                    }
                    val source = response.body?.byteStream()
                        ?: throw PenBrushProxyFailure.InvalidPenBrush()
                    val output = ByteArrayOutputStream()
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val read = source.read(buffer)
                        if (read < 0) break
                        if (output.size() + read > settings.videoMaxBytes) {
                            throw PenBrushProxyFailure.PenBrushTooLarge()
                        }
                        output.write(buffer, 0, read)
                    }
                    output.toByteArray()
                }
            }
        }

    private fun executeJson(builder: Request.Builder): PenBrushProxyJob {
        val request = authorized(builder).build()
        return client.newCall(request).execute().use { response ->
            val raw = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching {
                    json.decodeFromString(PenBrushErrorResponse.serializer(), raw).error.code
                }.getOrNull()
                throw PenBrushProxyFailure.Api(response.code, error)
            }
            val dto = json.decodeFromString(PenBrushJobResponse.serializer(), raw)
            PenBrushProxyJob(
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
        "${settings.baseUrl}/v1/draw/jobs/$jobId$suffix?chatId=$chatId"

    private companion object {
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

sealed class PenBrushProxyFailure(message: String) : RuntimeException(message) {
    class Http(val status: Int) : PenBrushProxyFailure("PenBrush proxy HTTP $status")
    class Api(val status: Int, val code: String?) :
        PenBrushProxyFailure(code ?: "PenBrush proxy API $status")
    class InvalidPenBrush : PenBrushProxyFailure("Downloaded pen-brush video is invalid")
    class PenBrushTooLarge : PenBrushProxyFailure("Downloaded pen-brush video is too large")
}

@Serializable
private data class PenBrushCreateRequest(
    val requestId: String,
    val chatId: String,
    val userId: String,
    val logId: String,
    val prompt: String
)

@Serializable
private data class PenBrushJobResponse(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: PenBrushError? = null,
    val file: PenBrushFile? = null
)

@Serializable
private data class PenBrushFile(
    val href: String,
    val mediaType: String? = null,
    val bytes: Long? = null,
    val sha256: String? = null
)

@Serializable
private data class PenBrushError(val code: String)

@Serializable
private data class PenBrushErrorResponse(val error: PenBrushError)
