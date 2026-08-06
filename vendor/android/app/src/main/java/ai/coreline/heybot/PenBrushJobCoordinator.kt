package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.ConcurrentHashMap

fun interface PenBrushTextReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

fun interface PenBrushBytesReplySender {
    fun send(chatId: Long, bytes: ByteArray, onDispatched: (Result<Unit>) -> Unit)
}

class PenBrushJobCoordinator(
    private val settings: PenBrushProxySettings,
    trigger: String,
    private val botId: Long,
    private val gateway: PenBrushProxyGateway,
    private val textSender: PenBrushTextReplySender,
    private val videoSender: PenBrushBytesReplySender,
    private val stateStore: PenBrushJobStateStore,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.legacy(settings.allowedChatIds),
    private val log: (String) -> Unit = ::println,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val deliveryGate: KakaoVideoDeliveryGate = KakaoVideoDeliveryGate(),
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val parser = PenBrushCommandParser(trigger, settings.promptMaxChars)
    private val ready = CompletableDeferred<Unit>()
    private val pollers = ConcurrentHashMap<String, Job>()
    private val confirmationWatchers = ConcurrentHashMap<String, Job>()
    private val deliveryLocks = ConcurrentHashMap<Long, Mutex>()
    private val admission = RequestAdmissionController(
        roomWindowMillis = settings.roomRateWindowMillis,
        roomMaxRequests = settings.roomRateMaxRequests,
        userWindowMillis = settings.userRateWindowMillis,
        userMaxRequests = settings.userRateMaxRequests,
        duplicateWindowMillis = 8_000L,
        nowMillis = nowMillis
    )

    init {
        scope.launch {
            runCatching {
                stateStore.initialize()
                stateStore.pending().forEach(::resume)
            }.onFailure {
                log("PenBrush job state initialization failed: ${it::class.simpleName}")
            }
            ready.complete(Unit)
            log("PenBrush proxy coordinator ready")
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (isOutgoingPenBrush(incoming)) {
            confirmDelivery(incoming)
            return
        }
        if (!isCandidate(incoming)) return
        val command = parser.parse(incoming.message) ?: return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.PEN_BRUSH)
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = RequestTraceKind.PEN_BRUSH
        )
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.PEN_BRUSH)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "PEN_BRUSH_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch {
            ready.await()
            when (command) {
                is PenBrushCommand.Generate -> create(incoming, command.prompt)
                is PenBrushCommand.Invalid -> reply(incoming, command.reason)
                PenBrushCommand.Status -> showStatus(incoming)
                PenBrushCommand.Cancel -> cancel(incoming)
                PenBrushCommand.Retry -> retry(incoming)
            }
        }
    }

    fun close() {
        scope.cancel()
    }

    private suspend fun create(incoming: GlmIncomingMessage, prompt: String) {
        val policySnapshot = roomCapabilityPolicy.snapshot()
        val roomCapabilityRevision = policySnapshot
            .capabilityRevision(incoming.chatId, RoomCapability.PEN_BRUSH) ?: return
        if (!roomCapabilityPolicy.isCurrent(
                roomCapabilityRevision,
                incoming.chatId,
                RoomCapability.PEN_BRUSH
            )
        ) return
        if (stateStore.countPending(incoming.chatId) >= settings.maxPendingPerRoom) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.QUEUE_FULL,
                reasonCode = "PEN_BRUSH_ROOM_QUEUE_FULL"
            )
            reply(incoming, "이 방의 펜브러쉬 요청이 이미 진행 중이에요. 완료 후 다시 요청해주세요.")
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
                reply(incoming, "펜브러쉬 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val requestId = "pen-brush:${incoming.chatId}:${incoming.logId}"
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED)
        gateway.create(
            requestId = requestId,
            chatId = incoming.chatId,
            userId = incoming.userId,
            logId = incoming.logId,
            prompt = prompt
        ).onSuccess { remote ->
            if (remote.chatId != incoming.chatId.toString()) {
                log("PenBrush job rejected: chat ID mismatch")
                reply(incoming, "펜브러쉬 요청 정보가 일치하지 않아 중단했어요.")
                return@onSuccess
            }
            val now = nowMillis()
            val local = LocalPenBrushJob(
                jobId = remote.jobId,
                requestId = requestId,
                chatId = incoming.chatId,
                userId = incoming.userId,
                logId = incoming.logId,
                status = remote.status,
                roomCapabilityRevision = roomCapabilityRevision,
                createdAtMillis = now,
                deadlineAtMillis = now + settings.jobTimeoutMillis,
                updatedAtMillis = now
            )
            stateStore.upsert(local)
            requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_SUCCEEDED)
            reply(incoming, "펜브러쉬 요청을 접수했어요. 완성되면 이 방으로 보내드릴게요.")
            startPolling(local)
        }.onFailure {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "PEN_BRUSH_CREATE_FAILED"
            )
            log("PenBrush job create failed: ${it::class.simpleName}")
            reply(incoming, "펜브러쉬 서버에 연결하지 못했어요. 텍스트 대화는 계속 사용할 수 있어요.")
        }
    }

    private fun startPolling(job: LocalPenBrushJob) {
        pollers.computeIfAbsent(job.jobId) {
            scope.launch {
                poll(job)
            }.also { launched ->
                launched.invokeOnCompletion { pollers.remove(job.jobId, launched) }
            }
        }
    }

    private fun resume(job: LocalPenBrushJob) {
        when (job.status) {
            "queued", "running", "succeeded" -> startPolling(job)
            "kakao_handoff_pending", "delivery_pending", "awaiting_unlock", "kakao_processing" ->
                resumeKakaoProcessing(job)
        }
    }

    private fun resumeKakaoProcessing(job: LocalPenBrushJob) {
        scope.launch {
            val processing = if (job.status == "kakao_processing") job else update(
                job = job,
                status = "kakao_processing",
                handoffAtMillis = job.deliveryHandoffAtMillis ?: job.updatedAtMillis,
                confirmationDeadlineAtMillis = job.deliveryConfirmationDeadlineAtMillis
                    ?: KakaoVideoDeliveryPolicy.legacyConfirmationDeadlineMillis(job.updatedAtMillis)
            )
            while (nowMillis() < (processing.deliveryConfirmationDeadlineAtMillis ?: nowMillis())) {
                if (deliveryGate.tryAcquire(processing.chatId, processing.jobId)) {
                    startConfirmationWatcher(processing)
                    return@launch
                }
                delay(1_000L)
            }
            val current = stateStore.latest(processing.chatId, processing.userId)
            if (current?.jobId == processing.jobId && current.status == "kakao_processing") {
                update(current, "confirmation_delayed")
            }
        }
    }

    private suspend fun poll(initial: LocalPenBrushJob) {
        var local = initial
        while (nowMillis() < local.deadlineAtMillis) {
            val statusResult = gateway.status(local.jobId, local.chatId)
            if (statusResult.isFailure) {
                delay(settings.pollIntervalMillis)
                continue
            }
            val remote = statusResult.getOrThrow()
            if (remote.chatId != local.chatId.toString()) {
                update(local, "failed")
                log("PenBrush polling stopped: chat ID mismatch")
                return
            }
            local = update(local, remote.status)
            when (remote.status) {
                "queued", "running" -> delay(settings.pollIntervalMillis)
                "succeeded" -> {
                    deliver(local)
                    return
                }
                "failed" -> {
                    notifyFailure(local.chatId, failureMessage(remote.errorCode))
                    return
                }
                "cancelled" -> return
                else -> {
                    update(local, "failed")
                    notifyFailure(local.chatId, "펜브러쉬 작업 상태를 확인할 수 없어요.")
                    return
                }
            }
        }
        gateway.cancel(local.jobId, local.chatId)
        update(local, "failed")
        notifyFailure(local.chatId, "펜브러쉬 생성 시간이 너무 길어 작업을 종료했어요.")
    }

    private suspend fun deliver(job: LocalPenBrushJob) {
        deliveryLocks.computeIfAbsent(job.chatId) { Mutex() }.withLock {
            deliverLocked(job)
        }
    }

    private suspend fun deliverLocked(job: LocalPenBrushJob) {
        if (!roomCapabilityPolicy.isCurrent(
                job.roomCapabilityRevision,
                job.chatId,
                RoomCapability.PEN_BRUSH
            )
        ) {
            update(job, "cancelled")
            log("PenBrush delivery skipped: room capability changed")
            return
        }
        while (!deliveryGate.tryAcquire(job.chatId, job.jobId)) delay(1_000L)
        val downloadResult = gateway.download(job.jobId, job.chatId)
        if (downloadResult.isFailure) {
            deliveryGate.release(job.chatId, job.jobId)
            update(job, "failed")
            notifyFailure(job.chatId, "완성된 펜브러쉬 영상을 내려받지 못했어요.")
            return
        }
        val bytes = downloadResult.getOrThrow()
        if (!isValidMp4(bytes, settings.videoMaxBytes)) {
            deliveryGate.release(job.chatId, job.jobId)
            update(job, "failed")
            notifyFailure(job.chatId, "완성된 펜브러쉬 영상 검증에 실패했어요.")
            return
        }

        // Count the attempt before the handoff.  A process death after this
        // durable write must prefer a possible lost video over a duplicate.
        val handoffPending = update(
            job,
            "kakao_handoff_pending",
            deliveryAttempt = job.deliveryAttempt + 1
        )
        val handoff = dispatch(handoffPending, bytes)
        if (handoff.isFailure) {
            deliveryGate.release(job.chatId, job.jobId)
            update(handoffPending, "failed")
            notifyFailure(job.chatId, "펜브러쉬 영상은 완성됐지만 카카오 전송을 시작하지 못했어요.")
            return
        }
        val handoffAt = nowMillis()
        val processing = update(
            job = handoffPending,
            status = "kakao_processing",
            handoffAtMillis = handoffAt,
            confirmationDeadlineAtMillis = KakaoVideoDeliveryPolicy.confirmationDeadlineMillis(
                handoffAt,
                bytes.size
            ),
            deliveryAttempt = handoffPending.deliveryAttempt
        )
        startConfirmationWatcher(processing)
    }

    private suspend fun dispatch(job: LocalPenBrushJob, bytes: ByteArray): Result<Unit> {
        val handoff = CompletableDeferred<Result<Unit>>()
        videoSender.send(job.chatId, bytes) { result ->
            if (!handoff.isCompleted) handoff.complete(result)
        }
        return withTimeoutOrNull(KakaoVideoDeliveryPolicy.LOCAL_HANDOFF_TIMEOUT_MILLIS) {
            handoff.await()
        } ?: Result.failure(IllegalStateException("KAKAO_VIDEO_HANDOFF_TIMEOUT"))
    }

    private fun startConfirmationWatcher(job: LocalPenBrushJob) {
        confirmationWatchers.computeIfAbsent(job.jobId) {
            scope.launch {
                val deadline = job.deliveryConfirmationDeadlineAtMillis ?: nowMillis()
                delay((deadline - nowMillis()).coerceAtLeast(0L))
                val current = stateStore.latest(job.chatId, job.userId)
                if (current?.jobId == job.jobId && current.status == "kakao_processing") {
                    update(current, "confirmation_delayed")
                    deliveryGate.release(job.chatId, job.jobId)
                    log("PenBrush delivery confirmation delayed jobId=${job.jobId}")
                }
            }.also { watcher ->
                watcher.invokeOnCompletion { confirmationWatchers.remove(job.jobId, watcher) }
            }
        }
    }

    private suspend fun showStatus(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming) ?: stateStore.latest(incoming.chatId, incoming.userId)
        val label = when (job?.status) {
            "queued" -> "대기 중"
            "running" -> "생성 중"
            "succeeded" -> "전송 준비 중"
            "kakao_handoff_pending", "delivery_pending" -> "카카오 전송 시작 중"
            "kakao_processing", "awaiting_unlock" -> "카카오톡 영상 처리 중 (자동 재전송 안 함)"
            "confirmation_delayed" -> "카카오 전송 확인 지연 (재전송 가능)"
            "delivered" -> "전송 완료"
            "failed" -> "실패"
            "cancelled" -> "취소됨"
            else -> "요청 없음"
        }
        reply(incoming, "최근 펜브러쉬 작업 상태: $label")
    }

    private suspend fun cancel(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming)
        if (
            job == null ||
            job.status !in setOf(
                "queued", "running", "succeeded", "kakao_handoff_pending", "delivery_pending",
                "kakao_processing", "awaiting_unlock", "confirmation_delayed"
            )
        ) {
            reply(incoming, "취소할 펜브러쉬 작업이 없어요.")
            return
        }
        gateway.cancel(job.jobId, job.chatId)
        update(job, "cancelled")
        pollers.remove(job.jobId)?.cancel()
        confirmationWatchers.remove(job.jobId)?.cancel()
        deliveryGate.release(job.chatId, job.jobId)
        reply(incoming, "최근 펜브러쉬 작업을 취소했어요.")
    }

    private suspend fun retry(incoming: GlmIncomingMessage) {
        val job = stateStore.latest(incoming.chatId, incoming.userId)
        if (job?.status != "confirmation_delayed") {
            reply(incoming, "재전송을 기다리는 펜브러쉬 영상이 없어요.")
            return
        }
        if (job.deliveryAttempt >= 2) {
            reply(incoming, "이미 재전송을 시도한 펜브러쉬 영상이에요. 카카오톡 전송 상태를 확인해주세요.")
            return
        }
        if (pollers[job.jobId]?.isActive == true) {
            reply(incoming, "영상 전송을 이미 확인하고 있어요.")
            return
        }
        val refreshed = job.copy(
            status = "succeeded",
            deadlineAtMillis = nowMillis() + settings.jobTimeoutMillis,
            updatedAtMillis = nowMillis(),
            deliveryHandoffAtMillis = null,
            deliveryConfirmationDeadlineAtMillis = null
        )
        stateStore.upsert(refreshed)
        reply(incoming, "완성된 영상을 다시 전송할게요.")
        startPolling(refreshed)
    }

    private suspend fun latestPending(incoming: GlmIncomingMessage): LocalPenBrushJob? =
        stateStore.pending()
            .filter { it.chatId == incoming.chatId && it.userId == incoming.userId }
            .maxByOrNull { it.updatedAtMillis }

    private suspend fun update(
        job: LocalPenBrushJob,
        status: String,
        handoffAtMillis: Long? = job.deliveryHandoffAtMillis,
        confirmationDeadlineAtMillis: Long? = job.deliveryConfirmationDeadlineAtMillis,
        deliveryAttempt: Int = job.deliveryAttempt
    ): LocalPenBrushJob {
        val updated = job.copy(
            status = status,
            updatedAtMillis = nowMillis(),
            deliveryHandoffAtMillis = handoffAtMillis,
            deliveryConfirmationDeadlineAtMillis = confirmationDeadlineAtMillis,
            deliveryAttempt = deliveryAttempt
        )
        stateStore.upsert(updated)
        val traceId = RequestTraceIds.from(job.chatId, job.logId)
        when (status) {
            "queued", "running" -> requestTraceStore.record(
                traceId,
                RequestTraceStage.PROVIDER_STARTED
            )
            "succeeded" -> requestTraceStore.record(
                traceId,
                RequestTraceStage.PROVIDER_SUCCEEDED
            )
            "failed" -> requestTraceStore.record(
                traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "PEN_BRUSH_JOB_FAILED"
            )
            "kakao_handoff_pending", "delivery_pending", "kakao_processing" ->
                requestTraceStore.record(traceId, RequestTraceStage.ENQUEUED)
            "delivered" -> requestTraceStore.record(traceId, RequestTraceStage.DB_CONFIRMED)
            "confirmation_delayed", "awaiting_unlock" -> requestTraceStore.record(
                traceId,
                RequestTraceStage.UNCONFIRMED,
                reasonCode = "KAKAO_DB_TIMEOUT"
            )
            "cancelled" -> requestTraceStore.record(
                traceId,
                RequestTraceStage.FINISHED,
                reasonCode = "CANCELLED"
            )
        }
        return updated
    }

    private fun isCandidate(incoming: GlmIncomingMessage): Boolean =
        incoming.messageType == "1" &&
            incoming.chatId in settings.allowedChatIds &&
            (botId == 0L || incoming.userId != botId)

    private fun isOutgoingPenBrush(incoming: GlmIncomingMessage): Boolean =
        botId != 0L &&
            incoming.userId == botId &&
            incoming.chatId in settings.allowedChatIds &&
            incoming.messageType in OUTGOING_PEN_BRUSH_TYPES

    private fun confirmDelivery(incoming: GlmIncomingMessage) {
        scope.launch {
            ready.await()
            val pending = stateStore.pending().firstOrNull {
                it.chatId == incoming.chatId &&
                    it.status == "kakao_processing" &&
                    incoming.logId > it.logId &&
                    deliveryGate.owns(it.chatId, it.jobId)
            } ?: return@launch
            update(pending, "delivered")
            confirmationWatchers.remove(pending.jobId)?.cancel()
            deliveryGate.release(pending.chatId, pending.jobId)
            log("Delayed video delivery reconciled jobId=${pending.jobId} logId=${incoming.logId}")
        }
    }

    private fun reply(incoming: GlmIncomingMessage, message: String) {
        textDeliveryTracker?.enqueued(
            incoming.traceId,
            incoming.chatId,
            message,
            incoming.threadId
        ) ?: requestTraceStore.record(incoming.traceId, RequestTraceStage.ENQUEUED)
        textSender.send(incoming.chatId, message, incoming.threadId)
    }

    private fun notifyFailure(chatId: Long, message: String) {
        textSender.send(chatId, message, null)
    }

    private fun failureMessage(code: String?): String = when (code) {
        "DRAW_QUEUE_FULL", "PEN_BRUSH_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "펜브러쉬 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "PEN_BRUSH_TOO_DARK", "PEN_BRUSH_TOO_BRIGHT", "PEN_BRUSH_LOW_CONTRAST", "PEN_BRUSH_LOW_ENTROPY" ->
            "완성된 펜브러쉬 영상의 품질 기준을 통과하지 못했어요."
        else -> "펜브러쉬 생성에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    companion object {
        // PD20의 카카오톡은 동영상 공유 intent를 type=3(POST)으로 기록한다.
        // 일부 버전의 type=16도 계속 허용해 이전 클라이언트와 호환한다.
        private val OUTGOING_PEN_BRUSH_TYPES = setOf("3", "16")

        /** Defense in depth: proxy-draw runs ffprobe QC; Iris additionally
         * accepts only a bounded ISO-BMFF/MP4 container before invoking Kakao. */
        fun isValidMp4(bytes: ByteArray, maximumBytes: Int): Boolean {
            if (bytes.size !in 16..maximumBytes) return false
            return bytes[4] == 'f'.code.toByte() &&
                bytes[5] == 't'.code.toByte() &&
                bytes[6] == 'y'.code.toByte() &&
                bytes[7] == 'p'.code.toByte()
        }
    }
}
