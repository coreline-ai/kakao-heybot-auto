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
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.ConcurrentHashMap

fun interface VideoTextReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

fun interface VideoBytesReplySender {
    fun send(chatId: Long, bytes: ByteArray)
}

class VideoJobCoordinator(
    private val settings: VideoProxySettings,
    trigger: String,
    private val botId: Long,
    private val gateway: VideoProxyGateway,
    private val textSender: VideoTextReplySender,
    private val videoSender: VideoBytesReplySender,
    private val stateStore: VideoJobStateStore,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.legacy(settings.allowedChatIds),
    private val log: (String) -> Unit = ::println,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val parser = VideoCommandParser(trigger, settings.promptMaxChars)
    private val ready = CompletableDeferred<Unit>()
    private val pollers = ConcurrentHashMap<String, Job>()
    private val deliveryWaiters =
        ConcurrentHashMap<Long, ConcurrentLinkedQueue<DeliveryWaiter>>()
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
                stateStore.pending().forEach(::startPolling)
            }.onFailure {
                log("Video job state initialization failed: ${it::class.simpleName}")
            }
            ready.complete(Unit)
            log("Video proxy coordinator ready")
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (isOutgoingVideo(incoming)) {
            confirmDelivery(incoming)
            return
        }
        if (!isCandidate(incoming)) return
        val command = parser.parse(incoming.message) ?: return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.VIDEO)
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = RequestTraceKind.VIDEO
        )
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.VIDEO)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "VIDEO_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch {
            ready.await()
            when (command) {
                is VideoCommand.Generate -> create(incoming, command.prompt)
                is VideoCommand.Invalid -> reply(incoming, command.reason)
                VideoCommand.Status -> showStatus(incoming)
                VideoCommand.Cancel -> cancel(incoming)
                VideoCommand.Retry -> retry(incoming)
            }
        }
    }

    fun close() {
        scope.cancel()
    }

    private suspend fun create(incoming: GlmIncomingMessage, prompt: String) {
        val policySnapshot = roomCapabilityPolicy.snapshot()
        val roomCapabilityRevision = policySnapshot
            .capabilityRevision(incoming.chatId, RoomCapability.VIDEO) ?: return
        if (!roomCapabilityPolicy.isCurrent(
                roomCapabilityRevision,
                incoming.chatId,
                RoomCapability.VIDEO
            )
        ) return
        if (stateStore.countPending(incoming.chatId) >= settings.maxPendingPerRoom) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.QUEUE_FULL,
                reasonCode = "VIDEO_ROOM_QUEUE_FULL"
            )
            reply(incoming, "이 방의 영상 요청이 이미 진행 중이에요. 완료 후 다시 요청해주세요.")
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
                reply(incoming, "영상 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val requestId = "video:${incoming.chatId}:${incoming.logId}"
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED)
        gateway.create(
            requestId = requestId,
            chatId = incoming.chatId,
            userId = incoming.userId,
            logId = incoming.logId,
            prompt = prompt
        ).onSuccess { remote ->
            if (remote.chatId != incoming.chatId.toString()) {
                log("Video job rejected: chat ID mismatch")
                reply(incoming, "영상 요청 정보가 일치하지 않아 중단했어요.")
                return@onSuccess
            }
            val now = nowMillis()
            val local = LocalVideoJob(
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
            reply(incoming, "영상 요청을 접수했어요. 완성되면 이 방으로 보내드릴게요.")
            startPolling(local)
        }.onFailure {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "VIDEO_CREATE_FAILED"
            )
            log("Video job create failed: ${it::class.simpleName}")
            reply(incoming, "영상 서버에 연결하지 못했어요. 텍스트 대화는 계속 사용할 수 있어요.")
        }
    }

    private fun startPolling(job: LocalVideoJob) {
        pollers.computeIfAbsent(job.jobId) {
            scope.launch {
                poll(job)
            }.also { launched ->
                launched.invokeOnCompletion { pollers.remove(job.jobId, launched) }
            }
        }
    }

    private suspend fun poll(initial: LocalVideoJob) {
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
                log("Video polling stopped: chat ID mismatch")
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
                    notifyFailure(local.chatId, "영상 작업 상태를 확인할 수 없어요.")
                    return
                }
            }
        }
        gateway.cancel(local.jobId, local.chatId)
        update(local, "failed")
        notifyFailure(local.chatId, "영상 생성 시간이 너무 길어 작업을 종료했어요.")
    }

    private suspend fun deliver(job: LocalVideoJob) {
        deliveryLocks.computeIfAbsent(job.chatId) { Mutex() }.withLock {
            deliverLocked(job)
        }
    }

    private suspend fun deliverLocked(job: LocalVideoJob) {
        if (!roomCapabilityPolicy.isCurrent(
                job.roomCapabilityRevision,
                job.chatId,
                RoomCapability.VIDEO
            )
        ) {
            update(job, "cancelled")
            log("Video delivery skipped: room capability changed")
            return
        }
        val downloadResult = gateway.download(job.jobId, job.chatId)
        if (downloadResult.isFailure) {
            update(job, "failed")
            notifyFailure(job.chatId, "완성 영상을 내려받지 못했어요.")
            return
        }
        val bytes = downloadResult.getOrThrow()
        if (!isValidMp4(bytes, settings.videoMaxBytes)) {
            update(job, "failed")
            notifyFailure(job.chatId, "완성 영상 검증에 실패했어요.")
            return
        }

        val waiting = update(job, "delivery_pending")
        val waiter = DeliveryWaiter(job.jobId, CompletableDeferred())
        deliveryWaiters
            .computeIfAbsent(job.chatId) { ConcurrentLinkedQueue() }
            .add(waiter)

        val dispatch = runCatching { videoSender.send(job.chatId, bytes) }
        if (dispatch.isFailure) {
            removeWaiter(job.chatId, waiter)
            update(waiting, "failed")
            notifyFailure(job.chatId, "영상은 완성됐지만 카카오 전송을 시작하지 못했어요.")
            return
        }

        val confirmedLogId = withTimeoutOrNull(settings.deliveryConfirmTimeoutMillis) {
            waiter.confirmedLogId.await()
        }
        removeWaiter(job.chatId, waiter)
        if (confirmedLogId != null) {
            update(waiting, "delivered")
            log("Video delivery confirmed jobId=${job.jobId} logId=$confirmedLogId")
            return
        }

        update(waiting, "awaiting_unlock")
        notifyFailure(
            job.chatId,
            "영상은 완성됐지만 카카오톡 전송이 확인되지 않았어요. " +
                "카카오톡 잠금을 해제한 뒤 '헤이봇 영상 재전송'을 입력해주세요."
        )
    }

    private suspend fun showStatus(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming) ?: stateStore.latest(incoming.chatId, incoming.userId)
        val label = when (job?.status) {
            "queued" -> "대기 중"
            "running" -> "생성 중"
            "succeeded" -> "전송 준비 중"
            "delivery_pending" -> "카카오 전송 확인 중"
            "awaiting_unlock" -> "카카오톡 잠금 해제 대기"
            "delivered" -> "전송 완료"
            "failed" -> "실패"
            "cancelled" -> "취소됨"
            else -> "요청 없음"
        }
        reply(incoming, "최근 영상 작업 상태: $label")
    }

    private suspend fun cancel(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming)
        if (
            job == null ||
            job.status !in setOf(
                "queued", "running", "succeeded", "delivery_pending", "awaiting_unlock"
            )
        ) {
            reply(incoming, "취소할 영상 작업이 없어요.")
            return
        }
        gateway.cancel(job.jobId, job.chatId)
        update(job, "cancelled")
        pollers.remove(job.jobId)?.cancel()
        reply(incoming, "최근 영상 작업을 취소했어요.")
    }

    private suspend fun retry(incoming: GlmIncomingMessage) {
        val job = stateStore.pending()
            .filter {
                it.chatId == incoming.chatId &&
                    it.userId == incoming.userId &&
                    it.status == "awaiting_unlock"
            }
            .maxByOrNull { it.updatedAtMillis }
        if (job == null) {
            reply(incoming, "재전송을 기다리는 영상이 없어요.")
            return
        }
        if (pollers[job.jobId]?.isActive == true) {
            reply(incoming, "영상 전송을 이미 확인하고 있어요.")
            return
        }
        val refreshed = job.copy(
            status = "succeeded",
            deadlineAtMillis = nowMillis() + settings.jobTimeoutMillis,
            updatedAtMillis = nowMillis()
        )
        stateStore.upsert(refreshed)
        reply(incoming, "완성된 영상을 다시 전송할게요.")
        startPolling(refreshed)
    }

    private suspend fun latestPending(incoming: GlmIncomingMessage): LocalVideoJob? =
        stateStore.pending()
            .filter { it.chatId == incoming.chatId && it.userId == incoming.userId }
            .maxByOrNull { it.updatedAtMillis }

    private suspend fun update(job: LocalVideoJob, status: String): LocalVideoJob {
        val updated = job.copy(status = status, updatedAtMillis = nowMillis())
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
                reasonCode = "VIDEO_JOB_FAILED"
            )
            "delivery_pending" -> requestTraceStore.record(traceId, RequestTraceStage.ENQUEUED)
            "delivered" -> requestTraceStore.record(traceId, RequestTraceStage.DB_CONFIRMED)
            "awaiting_unlock" -> requestTraceStore.record(
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

    private fun isOutgoingVideo(incoming: GlmIncomingMessage): Boolean =
        botId != 0L &&
            incoming.userId == botId &&
            incoming.chatId in settings.allowedChatIds &&
            incoming.messageType in OUTGOING_VIDEO_TYPES

    private fun confirmDelivery(incoming: GlmIncomingMessage) {
        val queue = deliveryWaiters[incoming.chatId]
        val waiter = generateSequence { queue?.poll() }
            .firstOrNull { !it.confirmedLogId.isCompleted }
        if (waiter != null) {
            waiter.confirmedLogId.complete(incoming.logId)
            return
        }

        scope.launch {
            ready.await()
            val pending = stateStore.pending().firstOrNull {
                it.chatId == incoming.chatId &&
                    it.status in setOf("delivery_pending", "awaiting_unlock")
            } ?: return@launch
            update(pending, "delivered")
            log("Delayed video delivery reconciled jobId=${pending.jobId} logId=${incoming.logId}")
        }
    }

    private fun removeWaiter(chatId: Long, waiter: DeliveryWaiter) {
        deliveryWaiters[chatId]?.let { queue ->
            queue.remove(waiter)
            if (queue.isEmpty()) deliveryWaiters.remove(chatId, queue)
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
        "VIDEO_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "영상 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "VIDEO_TOO_DARK", "VIDEO_TOO_BRIGHT", "VIDEO_LOW_CONTRAST", "VIDEO_LOW_ENTROPY" ->
            "완성 영상의 품질 기준을 통과하지 못했어요."
        else -> "영상 생성에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    companion object {
        // PD20의 카카오톡은 동영상 공유 intent를 type=3(POST)으로 기록한다.
        // 일부 버전의 type=16도 계속 허용해 이전 클라이언트와 호환한다.
        private val OUTGOING_VIDEO_TYPES = setOf("3", "16")

        /** Defense in depth: proxy-video runs ffprobe QC; Iris additionally
         * accepts only a bounded ISO-BMFF/MP4 container before invoking Kakao. */
        fun isValidMp4(bytes: ByteArray, maximumBytes: Int): Boolean {
            if (bytes.size !in 16..maximumBytes) return false
            return bytes[4] == 'f'.code.toByte() &&
                bytes[5] == 't'.code.toByte() &&
                bytes[6] == 'y'.code.toByte() &&
                bytes[7] == 'p'.code.toByte()
        }
    }

    private data class DeliveryWaiter(
        val jobId: String,
        val confirmedLogId: CompletableDeferred<Long>
    )
}
