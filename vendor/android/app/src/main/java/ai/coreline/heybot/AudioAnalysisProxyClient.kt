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

data class AudioSegment(
    val id: String,
    val startMs: Long,
    val endMs: Long,
    val text: String
)

data class AudioTranscriptResult(
    val version: Int,
    val durationMs: Long,
    val language: String,
    val segments: List<AudioSegment>,
    val speechRatio: Double,
    val warnings: List<String>
)

data class AudioAnalysisJob(
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val errorCode: String?,
    val result: AudioTranscriptResult?
)

interface AudioAnalysisGateway {
    suspend fun create(
        requestId: String,
        chatId: Long,
        source: IncomingAudioAttachment
    ): Result<AudioAnalysisJob>

    suspend fun status(jobId: String, chatId: Long): Result<AudioAnalysisJob>
    suspend fun cancel(jobId: String, chatId: Long): Result<AudioAnalysisJob>
    suspend fun purge(jobId: String, chatId: Long): Result<Boolean>
}

sealed class AudioProxyException(val reasonCode: String, cause: Throwable? = null) :
    RuntimeException(reasonCode, cause)

class AudioTransportException(cause: Throwable) :
    AudioProxyException("AUDIO_TRANSPORT_UNAVAILABLE", cause)
class AudioAuthorizationException : AudioProxyException("AUDIO_ROUTE_UNAUTHORIZED")
class AudioHttpException(val statusCode: Int, code: String) : AudioProxyException(code)
class AudioInvalidResponseException(cause: Throwable? = null) :
    AudioProxyException("AUDIO_RESULT_INVALID", cause)

class AudioAnalysisProxyClient(
    private val settings: AudioAnalysisSettings,
    // `language` has a Kotlin default, but it is a required field in the
    // proxy contract. Keep defaults encoded so production requests cannot be
    // rejected as INVALID_REQUEST while local DTO decoding still succeeds.
    private val json: Json = Json { ignoreUnknownKeys = true; encodeDefaults = true },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .readTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .writeTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .callTimeout(settings.requestTimeoutMillis, TimeUnit.MILLISECONDS)
        .build()
) : AudioAnalysisGateway {
    override suspend fun create(
        requestId: String,
        chatId: Long,
        source: IncomingAudioAttachment
    ): Result<AudioAnalysisJob> = withContext(Dispatchers.IO) {
        runCatching {
            val body = AudioCreateRequest(
                requestId = requestId,
                chatId = chatId.toString(),
                source = AudioSourceDto(
                    url = source.sourceUrl,
                    declaredBytes = source.declaredBytes,
                    expiresAtMillis = source.expiresAtMillis,
                    declaredExtension = source.declaredExtension
                )
            )
            executeJob(
                Request.Builder()
                    .url("${settings.baseUrl}/v1/audio/transcriptions")
                    .post(json.encodeToString(AudioCreateRequest.serializer(), body).toRequestBody(JSON))
            )
        }
    }

    override suspend fun status(jobId: String, chatId: Long): Result<AudioAnalysisJob> =
        jobRequest(jobId, chatId, delete = false)

    override suspend fun cancel(jobId: String, chatId: Long): Result<AudioAnalysisJob> =
        jobRequest(jobId, chatId, delete = true)

    override suspend fun purge(jobId: String, chatId: Long): Result<Boolean> =
        withContext(Dispatchers.IO) {
            runCatching {
                val request = authorized(
                    Request.Builder()
                        .url("${settings.baseUrl}/v1/audio/transcriptions/$jobId/purge?chatId=$chatId")
                        .delete()
                ).build()
                client.newCall(request).execute().use { response ->
                    val raw = response.body?.string().orEmpty()
                    if (!response.isSuccessful) throwFailure(response.code, raw)
                    val body = json.decodeFromString<AudioPurgeResponse>(raw)
                    body.deleted
                }
            }
        }

    private suspend fun jobRequest(jobId: String, chatId: Long, delete: Boolean) =
        withContext(Dispatchers.IO) {
            runCatching {
                val builder = Request.Builder()
                    .url("${settings.baseUrl}/v1/audio/transcriptions/$jobId?chatId=$chatId")
                if (delete) builder.delete() else builder.get()
                executeJob(builder)
            }
        }

    private fun executeJob(builder: Request.Builder): AudioAnalysisJob {
        try {
            return client.newCall(authorized(builder).build()).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) throwFailure(response.code, raw)
                decodeJob(raw)
            }
        } catch (error: AudioProxyException) {
            throw error
        } catch (error: IOException) {
            throw AudioTransportException(error)
        } catch (error: Exception) {
            throw AudioInvalidResponseException(error)
        }
    }

    private fun authorized(builder: Request.Builder): Request.Builder {
        val authorization = settings.authorizationHeader().getOrElse {
            throw AudioInvalidResponseException(it)
        }
        return builder.header("Authorization", authorization).header("Content-Type", "application/json")
    }

    private fun throwFailure(status: Int, raw: String): Nothing {
        if (status == 401 || status == 403) throw AudioAuthorizationException()
        val code = runCatching { json.decodeFromString<AudioErrorResponse>(raw).error.code }
            .getOrDefault("AUDIO_HTTP_$status")
        throw AudioHttpException(status, code)
    }

    private fun decodeJob(raw: String): AudioAnalysisJob {
        val body = json.decodeFromString<AudioJobResponse>(raw)
        if (!body.jobId.matches(UUID) || !body.requestId.startsWith("audio:") ||
            body.chatId.toLongOrNull() == null || body.status !in STATUSES
        ) throw AudioInvalidResponseException()
        val result = body.result?.let { value ->
            if (value.version != 1 || value.status != "transcribed" || value.language != "ko" ||
                value.durationMs !in 1..7_200_000 || value.segments.size > 2_000 ||
                value.quality.speechRatio !in 0.0..1.0 || value.quality.warnings.size > 20
            ) throw AudioInvalidResponseException()
            val segments = value.segments.mapIndexed { index, segment ->
                val expected = "S${(index + 1).toString().padStart(4, '0')}"
                if (segment.id != expected || segment.startMs < 0 || segment.endMs < segment.startMs ||
                    segment.endMs > value.durationMs + 2_000 || segment.text.isBlank() ||
                    segment.text.length > 2_000
                ) throw AudioInvalidResponseException()
                AudioSegment(segment.id, segment.startMs, segment.endMs, segment.text.trim())
            }
            AudioTranscriptResult(
                version = value.version,
                durationMs = value.durationMs,
                language = value.language,
                segments = segments,
                speechRatio = value.quality.speechRatio,
                warnings = value.quality.warnings
            )
        }
        return AudioAnalysisJob(
            body.jobId, body.requestId, body.chatId, body.status, body.error?.code, result
        )
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
        val UUID = Regex("[0-9a-fA-F-]{36}")
        val STATUSES = setOf(
            "queued", "fetching", "validating", "normalizing", "transcribing",
            "transcribed", "failed", "cancelled"
        )
    }
}

@Serializable
private data class AudioCreateRequest(
    val requestId: String,
    val chatId: String,
    val source: AudioSourceDto,
    val language: String = "ko"
)
@Serializable private data class AudioSourceDto(
    val url: String,
    val declaredBytes: Long,
    val expiresAtMillis: Long,
    val declaredExtension: String
)
@Serializable private data class AudioJobResponse(
    val version: Int,
    val jobId: String,
    val requestId: String,
    val chatId: String,
    val status: String,
    val error: AudioErrorDto? = null,
    val result: AudioResultDto? = null
)
@Serializable private data class AudioErrorDto(val code: String)
@Serializable private data class AudioErrorResponse(val error: AudioErrorDto)
@Serializable private data class AudioPurgeResponse(val deleted: Boolean)
@Serializable private data class AudioResultDto(
    val version: Int,
    val status: String,
    val durationMs: Long,
    val language: String,
    val segments: List<AudioSegmentDto>,
    val quality: AudioQualityDto
)
@Serializable private data class AudioSegmentDto(
    val id: String,
    val startMs: Long,
    val endMs: Long,
    val text: String
)
@Serializable private data class AudioQualityDto(
    val speechRatio: Double,
    val warnings: List<String> = emptyList()
)
