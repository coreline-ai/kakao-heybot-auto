package ai.coreline.heybot

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

fun interface ImageAnalysisReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

class ImageAnalysisCoordinator(
    private val settings: ImageAnalysisSettings,
    private val trigger: String,
    private val botId: Long,
    private val gateway: ImageAnalysisGateway,
    private val replySender: ImageAnalysisReplySender,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore,
    private val recentStore: RecentIncomingImageStore = RecentIncomingImageStore(
        retentionMillis = settings.recentImageWindowMillis
    ),
    private val attachmentLookup: ImageAttachmentLookup = EmptyImageAttachmentLookup,
    private val safety: ReplySafetyPolicy = ReplySafetyPolicy(),
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val pollers = ConcurrentHashMap<String, Job>()
    private val pendingByRoom = ConcurrentHashMap<Long, AtomicInteger>()
    private val commandRouter = BotCommandRouter(trigger)
    private val admission = RequestAdmissionController(
        roomWindowMillis = settings.roomRateWindowMillis,
        roomMaxRequests = settings.roomRateMaxRequests,
        userWindowMillis = settings.userRateWindowMillis,
        userMaxRequests = settings.userRateMaxRequests,
        duplicateWindowMillis = 8_000L,
        nowMillis = nowMillis
    )

    fun onIncoming(incoming: GlmIncomingMessage) {
        incoming.imageAttachment?.let { attachment ->
            if (incoming.chatId in settings.allowedChatIds) {
                recentStore.put(attachment)
            }
        }
        if (!isAnalyzeCommand(incoming)) return
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.IMAGE_ANALYSIS)) return
        scope.launch { create(incoming) }
    }

    fun close() = scope.cancel()

    private suspend fun create(incoming: GlmIncomingMessage) {
        val source = selectSource(incoming)
        if (source == null) {
            val message = if (incoming.threadId != null) {
                "답장한 이미지를 찾을 수 없거나 원본 주소가 만료됐어요. 이미지를 다시 보낸 뒤 요청해주세요."
            } else {
                "${recentWindowLabel()} 안에 보낸 분석 가능한 이미지를 찾지 못했어요. 이미지를 보낸 뒤 다시 요청해주세요."
            }
            reply(incoming, message)
            return
        }
        when (admission.admit(incoming)) {
            AdmissionResult.Accepted -> Unit
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> return
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                reply(incoming, "이미지 분석 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val counter = pendingByRoom.computeIfAbsent(incoming.chatId) { AtomicInteger() }
        if (counter.incrementAndGet() > settings.maxPendingPerRoom) {
            counter.decrementAndGet()
            reply(incoming, "이 방의 이미지 분석 요청이 이미 진행 중이에요. 잠시 후 다시 요청해주세요.")
            return
        }
        val revision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.IMAGE_ANALYSIS)
        if (revision == null || !roomCapabilityPolicy.isCurrent(
                revision, incoming.chatId, RoomCapability.IMAGE_ANALYSIS
            )) {
            counter.decrementAndGet()
            return
        }
        val requestId = "vision:${incoming.chatId}:${source.sourceLogId}"
        gateway.create(requestId, incoming.chatId, incoming.userId, source)
            .onSuccess { remote ->
                if (remote.chatId != incoming.chatId.toString()) {
                    counter.decrementAndGet()
                    reply(incoming, "이미지 분석 요청 정보가 일치하지 않아 중단했어요.")
                    return@onSuccess
                }
                reply(incoming, "이미지 분석을 시작했어요. 완료되면 이 방에 설명해드릴게요.")
                startPolling(remote, incoming, revision, counter)
            }
            .onFailure {
                counter.decrementAndGet()
                log("Vision job create failed: ${it::class.simpleName}")
                reply(incoming, "이미지 분석 서버에 연결하지 못했어요. 다른 기능은 계속 사용할 수 있어요.")
            }
    }

    private fun startPolling(
        initial: ImageAnalysisJob,
        incoming: GlmIncomingMessage,
        revision: Long,
        counter: AtomicInteger
    ) {
        val launched = scope.launch(start = CoroutineStart.LAZY) {
                try {
                    val deadline = nowMillis() + settings.jobTimeoutMillis
                    var remote = initial
                    while (nowMillis() < deadline) {
                        when (remote.status) {
                            "queued", "running" -> {
                                delay(settings.pollIntervalMillis)
                                val status = gateway.status(remote.jobId, incoming.chatId)
                                if (status.isFailure) continue
                                remote = status.getOrThrow()
                            }
                            "succeeded" -> {
                                deliver(incoming, remote.result, revision)
                                return@launch
                            }
                            "failed" -> {
                                reply(incoming, failureMessage(remote.errorCode))
                                return@launch
                            }
                            "cancelled" -> return@launch
                            else -> {
                                reply(incoming, "이미지 분석 상태를 확인할 수 없어요.")
                                return@launch
                            }
                        }
                    }
                    gateway.cancel(initial.jobId, incoming.chatId)
                    reply(incoming, "이미지 분석 시간이 너무 길어 작업을 종료했어요.")
                } finally {
                    counter.decrementAndGet()
                }
            }
        val existing = pollers.putIfAbsent(initial.jobId, launched)
        if (existing != null) {
            launched.cancel()
            counter.decrementAndGet()
            reply(incoming, "같은 이미지 분석이 이미 진행 중이에요.")
            return
        }
        launched.invokeOnCompletion { pollers.remove(initial.jobId, launched) }
        launched.start()
    }

    private fun deliver(
        incoming: GlmIncomingMessage,
        result: ImageAnalysisResult?,
        revision: Long
    ) {
        if (!roomCapabilityPolicy.isCurrent(
                revision, incoming.chatId, RoomCapability.IMAGE_ANALYSIS
            )) {
            log("Vision delivery skipped: room capability changed")
            return
        }
        val raw = result?.summary.orEmpty()
        val safe = safety.apply(raw)
        if (safe is ReplySafetyResult.Safe) {
            reply(incoming, "이미지 분석 결과\n${safe.text}")
        } else {
            reply(incoming, "이미지 분석 결과를 안전하게 표시할 수 없어요.")
        }
    }

    private fun isAnalyzeCommand(incoming: GlmIncomingMessage): Boolean =
        incoming.messageType == "1" &&
            incoming.chatId in settings.allowedChatIds &&
            commandRouter.route(incoming.message) == BotCommand.AnalyzeImage

    private fun selectSource(incoming: GlmIncomingMessage): IncomingImageAttachment? {
        val referencedLogId = incoming.threadId
        val selected = if (referencedLogId != null) {
            recentStore.findExact(incoming.chatId, referencedLogId)
                ?: attachmentLookup.findExact(incoming.chatId, referencedLogId)
        } else {
            val notBeforeMillis = nowMillis() - settings.recentImageWindowMillis
            recentStore.findRecent(incoming.chatId, incoming.userId, notBeforeMillis)
                ?: attachmentLookup.findLatest(
                    incoming.chatId,
                    incoming.userId,
                    notBeforeMillis
                )
                ?: selectLatestBotImage(incoming.chatId)
        }
        selected?.let(recentStore::put)
        return selected
    }

    private fun selectLatestBotImage(chatId: Long): IncomingImageAttachment? {
        if (botId == 0L) return null
        return recentStore.findRecent(chatId, botId, Long.MIN_VALUE)
            ?: attachmentLookup.findLatest(chatId, botId, Long.MIN_VALUE)
    }

    private fun recentWindowLabel(): String {
        val minutes = settings.recentImageWindowMillis / 60_000L
        return if (minutes > 0L) "최근 ${minutes}분" else "설정된 최근 시간"
    }

    private fun reply(incoming: GlmIncomingMessage, text: String) =
        replySender.send(incoming.chatId, text, incoming.threadId)

    private fun failureMessage(code: String?): String = when (code) {
        "SOURCE_EXPIRED" -> "이미지 원본 주소가 만료됐어요. 이미지를 다시 보낸 뒤 분석해주세요."
        "VISION_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "이미지 분석 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "INVALID_IMAGE", "SOURCE_TOO_LARGE", "FORBIDDEN_SOURCE" ->
            "이 이미지는 안전 검증을 통과하지 못해 분석할 수 없어요."
        else -> "이미지 분석에 실패했어요. 잠시 후 다시 요청해주세요."
    }
}
