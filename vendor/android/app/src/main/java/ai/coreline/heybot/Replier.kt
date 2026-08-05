package ai.coreline.heybot

import android.app.RemoteInput
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import ai.coreline.heybot.Replier.Companion.SendMessageRequest
import java.io.File

// SendMsg : ye-seola/go-kdb

class Replier {
    companion object {
        private val messageChannel = Channel<SendMessageRequest>(Channel.BUFFERED)
        private val coroutineScope = CoroutineScope(Dispatchers.IO)
        private var messageSenderJob: Job? = null
        private val mutex = Mutex()

        init {
            startMessageSender()
        }

        fun startMessageSender() {
            coroutineScope.launch {
                if (messageSenderJob?.isActive == true) {
                    messageSenderJob?.cancelAndJoin()
                }
                messageSenderJob = launch {
                    for (request in messageChannel) {
                        try {
                            mutex.withLock {
                                request.send()
                                delay(Configurable.messageSendRate)
                            }
                        } catch (e: Exception) {
                            System.err.println("Error sending message from channel: $e")
                        }
                    }
                }
            }
        }

        fun restartMessageSender() {
            startMessageSender()
        }

        private fun sendMessageInternal(
            referer: String,
            chatId: Long,
            msg: String,
            threadId: Long?
        ) {
            val intent = Intent().apply {
                component = ComponentName(
                    "com.kakao.talk", "com.kakao.talk.notification.NotificationActionService"
                )
                putExtra("noti_referer", referer)
                putExtra("chat_id", chatId)

                putExtra("is_chat_thread_notification", threadId != null)
                if (threadId != null) {
                    putExtra("thread_id", threadId)
                }

                action = "com.kakao.talk.notification.REPLY_MESSAGE"

                val results = Bundle().apply {
                    putCharSequence("reply_message", msg)
                }

                val remoteInput = RemoteInput.Builder("reply_message").build()
                RemoteInput.addResultsToIntent(arrayOf(remoteInput), this, results)
            }

            AndroidHiddenApi.startService(intent)
        }

        fun sendMessage(
            referer: String,
            chatId: Long,
            msg: String,
            threadId: Long?,
            onDispatched: (Result<Unit>) -> Unit = {}
        ) {
            coroutineScope.launch {
                runCatching {
                    messageChannel.send(SendMessageRequest {
                        val result = runCatching {
                            sendMessageInternal(referer, chatId, msg, threadId)
                        }
                        onDispatched(result)
                        result.getOrThrow()
                    })
                }.onFailure { onDispatched(Result.failure(it)) }
            }
        }


        fun sendPhoto(room: Long, base64ImageDataString: String) {
            coroutineScope.launch {
                messageChannel.send(SendMessageRequest {
                    sendPhotoInternal(
                        room, base64ImageDataString
                    )
                })
            }
        }

        fun sendPhotoBytes(
            room: Long,
            imageBytes: ByteArray,
            onDispatched: (Result<Unit>) -> Unit = {}
        ) {
            coroutineScope.launch {
                runCatching {
                    messageChannel.send(SendMessageRequest {
                        val result = runCatching {
                            sendMultiplePhotoBytesInternal(room, listOf(imageBytes))
                        }
                        onDispatched(result)
                        result.getOrThrow()
                    })
                }.onFailure { onDispatched(Result.failure(it)) }
            }
        }

        fun sendMultiplePhotos(room: Long, base64ImageDataStrings: List<String>) {
            coroutineScope.launch {
                messageChannel.send(SendMessageRequest {
                    sendMultiplePhotosInternal(
                        room, base64ImageDataStrings
                    )
                })
            }
        }

        fun sendVideo(room: Long, base64VideoDataString: String) {
            coroutineScope.launch {
                messageChannel.send(SendMessageRequest {
                    sendVideoInternal(room, base64VideoDataString)
                })
            }
        }

        /** Sends a validated MP4 returned by the local video proxy without
         * putting media bytes into a text/base64 command path. */
        fun sendVideoBytes(room: Long, videoBytes: ByteArray) {
            coroutineScope.launch {
                messageChannel.send(SendMessageRequest {
                    sendVideoBytesInternal(room, videoBytes)
                })
            }
        }

        /** Sends a validated audio fixture through Kakao's direct-share path.
         * The callback confirms local dispatch only; callers must verify the
         * resulting Kakao DB row before treating the upload as delivered. */
        fun sendAudioBytes(
            room: Long,
            audioBytes: ByteArray,
            format: KakaoAudioShareFormat = KakaoAudioShareFormat.M4A,
            onDispatched: (Result<Unit>) -> Unit = {}
        ) {
            coroutineScope.launch {
                runCatching {
                    messageChannel.send(SendMessageRequest {
                        val result = runCatching {
                            sendAudioBytesInternal(room, audioBytes, format)
                        }
                        onDispatched(result)
                        result.getOrThrow()
                    })
                }.onFailure { onDispatched(Result.failure(it)) }
            }
        }

        private fun sendPhotoInternal(room: Long, base64ImageDataString: String) {
            sendMultiplePhotosInternal(room, listOf(base64ImageDataString))
        }

        private fun sendMultiplePhotosInternal(room: Long, base64ImageDataStrings: List<String>) {
            sendMultiplePhotoBytesInternal(
                room,
                base64ImageDataStrings.map { Base64.decode(it, Base64.DEFAULT) }
            )
        }

        private fun sendMultiplePhotoBytesInternal(room: Long, images: List<ByteArray>) {
            val picDir = File(IMAGE_DIR_PATH).apply {
                if (!exists()) {
                    mkdirs()
                }
            }

            val uris = images.mapIndexed { idx, imageBytes ->
                val timestamp = System.currentTimeMillis().toString()

                val imageFile = File(picDir, "${timestamp}_${idx}.png").apply {
                    writeBytes(imageBytes)
                }

                val imageUri = Uri.fromFile(imageFile)
                mediaScan(imageUri)
                imageUri
            }

            if (uris.isEmpty()) {
                System.err.println("No image URIs created, cannot send multiple photos.")
                return
            }

            val intent = Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                setPackage("com.kakao.talk")
                type = "image/*"
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
                putExtra("key_id", room)
                putExtra("key_type", 1)
                putExtra("key_from_direct_share", true)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }

            try {
                AndroidHiddenApi.startActivity(intent)
            } catch (e: Exception) {
                System.err.println("Error starting activity for sending multiple photos: $e")
                throw e
            }
        }

        private fun sendVideoInternal(room: Long, base64VideoDataString: String) {
            sendVideoBytesInternal(room, Base64.decode(base64VideoDataString, Base64.DEFAULT))
        }

        private fun sendVideoBytesInternal(room: Long, videoBytes: ByteArray) {
            val mediaDir = File(IMAGE_DIR_PATH).apply {
                if (!exists()) {
                    mkdirs()
                }
            }
            val timestamp = System.currentTimeMillis().toString()
            val videoFile = File(mediaDir, "${timestamp}.mp4").apply {
                writeBytes(videoBytes)
            }
            val videoUri = Uri.fromFile(videoFile)
            mediaScan(videoUri)

            val intent = Intent(Intent.ACTION_SEND).apply {
                setPackage("com.kakao.talk")
                type = "video/mp4"
                putExtra(Intent.EXTRA_STREAM, videoUri)
                putExtra("key_id", room)
                putExtra("key_type", 1)
                putExtra("key_from_direct_share", true)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }

            try {
                AndroidHiddenApi.startActivity(intent)
            } catch (e: Exception) {
                System.err.println("Error starting activity for sending video: $e")
                throw e
            }
        }

        private fun sendAudioBytesInternal(
            room: Long,
            audioBytes: ByteArray,
            format: KakaoAudioShareFormat
        ) {
            val mediaDir = File(IMAGE_DIR_PATH).apply {
                if (!exists()) mkdirs()
            }
            val audioFile = File(
                mediaDir,
                "heybot_audio_${System.currentTimeMillis()}.${format.extension}"
            ).apply {
                writeBytes(audioBytes)
            }
            val audioUri = Uri.fromFile(audioFile)
            mediaScan(audioUri)

            val intent = Intent(Intent.ACTION_SEND).apply {
                setPackage("com.kakao.talk")
                type = format.mimeType
                putExtra(Intent.EXTRA_STREAM, audioUri)
                putExtra("key_id", room)
                putExtra("key_type", 1)
                putExtra("key_from_direct_share", true)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }

            try {
                AndroidHiddenApi.startActivity(intent)
            } catch (e: Exception) {
                System.err.println("Error starting activity for sending audio: $e")
                throw e
            }
        }


        internal fun interface SendMessageRequest {
            suspend fun send()
        }

        private fun mediaScan(uri: Uri) {
            val mediaScanIntent = Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE).apply {
                data = uri
            }
            AndroidHiddenApi.broadcastIntent(mediaScanIntent)
        }
    }
}

enum class KakaoAudioShareFormat(val extension: String, val mimeType: String) {
    // KakaoTalk's direct-share receiver recognizes audio/mp3 for an MP3 file;
    // audio/mpeg opens the picker but does not create the target chat DB row.
    MP3("mp3", "audio/mp3"),
    M4A("m4a", "audio/mp4"),
    WAV("wav", "audio/wav");

    fun matchesMagic(header: ByteArray): Boolean = when (this) {
        MP3 -> (header.size >= 3 && header.copyOfRange(0, 3).contentEquals(MP3_ID3)) ||
            (header.size >= 2 && header[0] == 0xff.toByte() && (header[1].toInt() and 0xe0) == 0xe0)
        M4A -> header.size >= 8 && header.copyOfRange(4, 8).contentEquals(M4A_FTYP)
        WAV -> header.size >= 12 && header.copyOfRange(0, 4).contentEquals(WAV_RIFF) &&
            header.copyOfRange(8, 12).contentEquals(WAV_WAVE)
    }

    companion object {
        fun parse(raw: String?): KakaoAudioShareFormat? = entries.firstOrNull {
            it.extension.equals(raw?.trim(), ignoreCase = true)
        }

        private val MP3_ID3 = "ID3".toByteArray()
        private val M4A_FTYP = "ftyp".toByteArray()
        private val WAV_RIFF = "RIFF".toByteArray()
        private val WAV_WAVE = "WAVE".toByteArray()
    }
}
