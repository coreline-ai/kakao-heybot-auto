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
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val visionContextStore: VisionConversationContextStore =
        VisionConversationContextStore(),
    private val createRetryDelaysMillis: List<Long> = listOf(2_000L, 5_000L, 10_000L),
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
        val command = analyzeCommand(incoming) ?: return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.VISION)
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = RequestTraceKind.VISION
        )
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.IMAGE_ANALYSIS)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "IMAGE_ANALYSIS_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch { create(incoming, command.task) }
    }

    fun close() = scope.cancel()

    private suspend fun create(incoming: GlmIncomingMessage, task: VisionTask) {
        val source = selectSource(incoming)
        if (source == null) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "VISION_SOURCE_NOT_FOUND"
            )
            val message = if (incoming.threadId != null) {
                "답장한 이미지를 찾을 수 없거나 원본 주소가 만료됐어요. 이미지를 다시 보낸 뒤 요청해주세요."
            } else {
                "${recentWindowLabel()} 안에 보낸 분석 가능한 이미지를 찾지 못했어요. 이미지를 보낸 뒤 다시 요청해주세요."
            }
            reply(incoming, message)
            return
        }
        when (admission.admit(incoming)) {
            AdmissionResult.Accepted -> requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.ADMITTED
            )
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DUPLICATE,
                    reasonCode = "DUPLICATE_REQUEST"
                )
                return
            }
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
                reply(incoming, "이미지 분석 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val counter = pendingByRoom.computeIfAbsent(incoming.chatId) { AtomicInteger() }
        if (counter.incrementAndGet() > settings.maxPendingPerRoom) {
            counter.decrementAndGet()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.QUEUE_FULL,
                reasonCode = "VISION_ROOM_QUEUE_FULL"
            )
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
        val requestId = "vision:${incoming.chatId}:${source.sourceLogId}:${task.wireValue}"
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED)
        val created = createWithRetry(
            requestId = requestId,
            incoming = incoming,
            source = source,
            task = task,
            revision = revision
        )
        val remote = created.getOrElse { error ->
            counter.decrementAndGet()
            if (error is VisionCapabilityChangedException) return
            val reasonCode = error.visionReasonCode()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = reasonCode
            )
            log("Vision job create failed: ${error::class.simpleName} code=$reasonCode")
            reply(incoming, createFailureMessage(error))
            return
        }
        if (remote.chatId != incoming.chatId.toString()) {
            counter.decrementAndGet()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "VISION_CHAT_SCOPE_MISMATCH"
            )
            reply(incoming, "이미지 분석 요청 정보가 일치하지 않아 중단했어요.")
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_SUCCEEDED)
        reply(incoming, "이미지 분석을 시작했어요. 완료되면 이 방에 설명해드릴게요.")
        startPolling(remote, incoming, source.sourceLogId, revision, counter, task)
    }

    private suspend fun createWithRetry(
        requestId: String,
        incoming: GlmIncomingMessage,
        source: IncomingImageAttachment,
        task: VisionTask,
        revision: Long
    ): Result<ImageAnalysisJob> {
        var result = gateway.create(requestId, incoming.chatId, incoming.userId, source, task)
        createRetryDelaysMillis.take(MAX_CREATE_RETRIES).forEachIndexed { index, retryDelay ->
            val error = result.exceptionOrNull() ?: return result
            if (!error.isRetryableVisionTransport()) return result
            if (!roomCapabilityPolicy.isCurrent(
                    revision,
                    incoming.chatId,
                    RoomCapability.IMAGE_ANALYSIS
                )) {
                return Result.failure(VisionCapabilityChangedException())
            }
            log("Vision transport retry scheduled: attempt=${index + 2}")
            delay(retryDelay.coerceAtLeast(0L))
            if (!roomCapabilityPolicy.isCurrent(
                    revision,
                    incoming.chatId,
                    RoomCapability.IMAGE_ANALYSIS
                )) {
                return Result.failure(VisionCapabilityChangedException())
            }
            result = gateway.create(requestId, incoming.chatId, incoming.userId, source, task)
        }
        return result
    }

    private fun startPolling(
        initial: ImageAnalysisJob,
        incoming: GlmIncomingMessage,
        sourceLogId: Long,
        revision: Long,
        counter: AtomicInteger,
        expectedTask: VisionTask
    ) {
        val launched = scope.launch(start = CoroutineStart.LAZY) {
                try {
                    val deadline = nowMillis() + settings.jobTimeoutMillis
                    var remote = initial
                    var lastStatusFailure: Throwable? = null
                    while (nowMillis() < deadline) {
                        when (remote.status) {
                            "queued", "running" -> {
                                delay(settings.pollIntervalMillis)
                                val status = gateway.status(remote.jobId, incoming.chatId)
                                if (status.isFailure) {
                                    val error = status.exceptionOrNull()!!
                                    lastStatusFailure = error
                                    if (error.isRetryableVisionTransport()) continue
                                    requestTraceStore.record(
                                        incoming.traceId,
                                        RequestTraceStage.PROVIDER_FAILED,
                                        reasonCode = error.visionReasonCode()
                                    )
                                    reply(incoming, createFailureMessage(error))
                                    return@launch
                                }
                                lastStatusFailure = null
                                remote = status.getOrThrow()
                            }
                            "succeeded" -> {
                                deliver(
                                    incoming,
                                    remote.result,
                                    sourceLogId,
                                    revision,
                                    expectedTask
                                )
                                return@launch
                            }
                            "failed" -> {
                                requestTraceStore.record(
                                    incoming.traceId,
                                    RequestTraceStage.PROVIDER_FAILED,
                                    reasonCode = remote.errorCode ?: "VISION_JOB_FAILED"
                                )
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
                    requestTraceStore.record(
                        incoming.traceId,
                        RequestTraceStage.PROVIDER_FAILED,
                        reasonCode = lastStatusFailure?.visionReasonCode() ?: "VISION_JOB_TIMEOUT"
                    )
                    if (lastStatusFailure?.isRetryableVisionTransport() == true) {
                        reply(incoming, createFailureMessage(lastStatusFailure))
                    } else {
                        reply(incoming, "이미지 분석 시간이 너무 길어 작업을 종료했어요.")
                    }
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

    private suspend fun deliver(
        incoming: GlmIncomingMessage,
        result: ImageAnalysisResult?,
        sourceLogId: Long,
        revision: Long,
        expectedTask: VisionTask
    ) {
        if (!roomCapabilityPolicy.isCurrent(
                revision, incoming.chatId, RoomCapability.IMAGE_ANALYSIS
            )) {
            log("Vision delivery skipped: room capability changed")
            return
        }
        if (result?.task != expectedTask) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "VISION_TASK_MISMATCH"
            )
            reply(incoming, "이미지 분석 결과 형식이 일치하지 않아 표시하지 않았어요.")
            return
        }
        val raw = result.answer
        val safe = safety.apply(raw)
        if (safe is ReplySafetyResult.Safe) {
            requestTraceStore.record(incoming.traceId, RequestTraceStage.SAFETY_PASSED)
            val label = when (result?.task) {
                VisionTask.OCR -> "이미지 글자 추출 결과"
                VisionTask.TRANSLATE_KO -> "이미지 글자 번역 결과"
                else -> "이미지 분석 결과"
            }
            val resultText = "$label\n${safe.text}"
            if (!reply(incoming, resultText)) return
            val confirmedLogId = textDeliveryTracker?.awaitConfirmedLogId(incoming.traceId)
                ?: return
            if (!roomCapabilityPolicy.isCurrent(
                    revision, incoming.chatId, RoomCapability.IMAGE_ANALYSIS
                )) {
                log("Vision context skipped: room capability changed")
                return
            }
            val now = nowMillis()
            val stored = visionContextStore.put(
                VisionConversationContext(
                    chatId = incoming.chatId,
                    ownerUserId = incoming.userId,
                    sourceLogId = sourceLogId,
                    resultLogId = confirmedLogId,
                    task = expectedTask,
                    safeAnswer = safe.text,
                    uncertainty = result.uncertainty,
                    capabilityRevision = revision,
                    createdAtMillis = now,
                    expiresAtMillis = now + settings.recentImageWindowMillis
                )
            )
            if (!stored) log("Vision context was not persisted")
        } else {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.SAFETY_BLOCKED,
                reasonCode = "REPLY_SAFETY_BLOCKED"
            )
            reply(incoming, "이미지 분석 결과를 안전하게 표시할 수 없어요.")
        }
    }

    private fun analyzeCommand(incoming: GlmIncomingMessage): BotCommand.AnalyzeImage? {
        if (incoming.messageType != "1" || incoming.chatId !in settings.allowedChatIds) return null
        return commandRouter.route(incoming.message) as? BotCommand.AnalyzeImage
    }

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

    private fun reply(incoming: GlmIncomingMessage, text: String): Boolean {
        textDeliveryTracker?.enqueued(
            incoming.traceId,
            incoming.chatId,
            text,
            incoming.threadId
        ) ?: requestTraceStore.record(incoming.traceId, RequestTraceStage.ENQUEUED)
        return runCatching { replySender.send(incoming.chatId, text, incoming.threadId) }
            .fold(
                onSuccess = { true },
                onFailure = {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DISPATCH_FAILED,
                    reasonCode = "REPLY_SENDER_EXCEPTION"
                )
                    false
                }
            )
    }

    private fun failureMessage(code: String?): String = when (code) {
        "SOURCE_EXPIRED" -> "이미지 원본 주소가 만료됐어요. 이미지를 다시 보낸 뒤 분석해주세요."
        "VISION_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "이미지 분석 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "INVALID_IMAGE", "GIF_CONVERSION_FAILED" ->
            "이미지 파일 형식을 확인하지 못했어요. 이미지를 다시 저장하거나 다른 이미지로 보내주세요."
        "SOURCE_TOO_LARGE" ->
            "이미지 파일이 너무 커서 분석할 수 없어요. 10MB 이하 이미지로 보내주세요."
        "FORBIDDEN_SOURCE" ->
            "지원되지 않는 이미지 출처라서 분석할 수 없어요. 카카오톡에 이미지를 직접 올린 뒤 요청해주세요."
        else -> "이미지 분석에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    private fun createFailureMessage(error: Throwable): String = when (error) {
        is VisionTransportException ->
            "이미지 분석 연결을 자동 복구하지 못했어요. 잠시 후 다시 요청해주세요. 다른 기능은 계속 사용할 수 있어요."
        is VisionAuthorizationException,
        is VisionConfigurationException ->
            "이미지 분석 서버 인증 설정을 확인할 수 없어요. 다른 기능은 계속 사용할 수 있어요."
        else -> "이미지 분석 요청을 처리하지 못했어요. 잠시 후 다시 요청해주세요."
    }

    private class VisionCapabilityChangedException : IllegalStateException()

    private companion object {
        const val MAX_CREATE_RETRIES = 3
    }
}
