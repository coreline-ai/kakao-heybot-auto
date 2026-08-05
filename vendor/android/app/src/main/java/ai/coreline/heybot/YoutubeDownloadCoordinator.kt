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

fun interface YoutubeDownloadTextReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

fun interface YoutubeDownloadBytesReplySender {
    fun send(chatId: Long, bytes: ByteArray)
}

class YoutubeDownloadJobCoordinator(
    private val settings: YoutubeDownloadProxySettings,
    trigger: String,
    private val botId: Long,
    private val gateway: YoutubeDownloadProxyGateway,
    private val textSender: YoutubeDownloadTextReplySender,
    private val youtubeDownloadSender: YoutubeDownloadBytesReplySender,
    private val stateStore: YoutubeDownloadJobStateStore,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.legacy(settings.allowedChatIds),
    private val log: (String) -> Unit = ::println,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val parser = YoutubeDownloadCommandParser(trigger, settings.promptMaxChars)
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
                log("YouTube download job state initialization failed: ${it::class.simpleName}")
            }
            ready.complete(Unit)
            log("YouTube download coordinator ready")
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (isOutgoingYoutubeDownload(incoming)) {
            confirmDelivery(incoming)
            return
        }
        if (!isCandidate(incoming)) return
        val command = parser.parse(incoming.message) ?: return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.YOUTUBE_DOWNLOAD)
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = RequestTraceKind.YOUTUBE_DOWNLOAD
        )
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.YOUTUBE_DOWNLOAD)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "YOUTUBE_DOWNLOAD_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch {
            ready.await()
            when (command) {
                is YoutubeDownloadCommand.Download -> create(incoming, command.url)
                is YoutubeDownloadCommand.Invalid -> reply(incoming, command.reason)
                YoutubeDownloadCommand.Status -> showStatus(incoming)
                YoutubeDownloadCommand.Cancel -> cancel(incoming)
                YoutubeDownloadCommand.Retry -> retry(incoming)
                YoutubeDownloadCommand.Delete -> delete(incoming)
            }
        }
    }

    fun close() {
        scope.cancel()
    }

    private suspend fun create(incoming: GlmIncomingMessage, sourceUrl: String) {
        val policySnapshot = roomCapabilityPolicy.snapshot()
        val roomCapabilityRevision = policySnapshot
            .capabilityRevision(incoming.chatId, RoomCapability.YOUTUBE_DOWNLOAD) ?: return
        if (!roomCapabilityPolicy.isCurrent(
                roomCapabilityRevision,
                incoming.chatId,
                RoomCapability.YOUTUBE_DOWNLOAD
            )
        ) return
        if (stateStore.countPending(incoming.chatId) >= settings.maxPendingPerRoom) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.QUEUE_FULL,
                reasonCode = "YOUTUBE_DOWNLOAD_ROOM_QUEUE_FULL"
            )
            reply(incoming, "이 방의 유튜브 다운로드가 이미 진행 중이에요. 완료 후 다시 요청해주세요.")
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
                reply(incoming, "유튜브 다운로드 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val requestId = "youtube:${incoming.chatId}:${incoming.logId}"
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED)
        gateway.create(
            requestId = requestId,
            chatId = incoming.chatId,
            userId = incoming.userId,
            logId = incoming.logId,
            url = sourceUrl
        ).onSuccess { remote ->
            if (remote.chatId != incoming.chatId.toString()) {
                log("YouTube download job rejected: chat ID mismatch")
                reply(incoming, "유튜브 다운로드 요청 정보가 일치하지 않아 중단했어요.")
                return@onSuccess
            }
            val now = nowMillis()
            val local = LocalYoutubeDownloadJob(
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
            reply(incoming, "유튜브 다운로드를 시작했어요. 완료되면 이 방으로 보내드릴게요.")
            startPolling(local)
        }.onFailure {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "YOUTUBE_DOWNLOAD_CREATE_FAILED"
            )
            log("YoutubeDownload job create failed: ${it::class.simpleName}")
            reply(incoming, "유튜브 다운로드 서버에 연결하지 못했어요. 다른 기능은 계속 사용할 수 있어요.")
        }
    }

    private fun startPolling(job: LocalYoutubeDownloadJob) {
        pollers.computeIfAbsent(job.jobId) {
            scope.launch {
                poll(job)
            }.also { launched ->
                launched.invokeOnCompletion { pollers.remove(job.jobId, launched) }
            }
        }
    }

    private suspend fun poll(initial: LocalYoutubeDownloadJob) {
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
                log("YoutubeDownload polling stopped: chat ID mismatch")
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

    private suspend fun deliver(job: LocalYoutubeDownloadJob) {
        deliveryLocks.computeIfAbsent(job.chatId) { Mutex() }.withLock {
            deliverLocked(job)
        }
    }

    private suspend fun deliverLocked(job: LocalYoutubeDownloadJob) {
        if (!roomCapabilityPolicy.isCurrent(
                job.roomCapabilityRevision,
                job.chatId,
                RoomCapability.YOUTUBE_DOWNLOAD
            )
        ) {
            update(job, "cancelled")
            log("YoutubeDownload delivery skipped: room capability changed")
            return
        }
        val downloadResult = gateway.download(job.jobId, job.chatId)
        if (downloadResult.isFailure) {
            update(job, "failed")
            notifyFailure(job.chatId, "다운로드한 영상을 가져오지 못했어요.")
            return
        }
        val bytes = downloadResult.getOrThrow()
        if (!isValidMp4(bytes, settings.youtubeDownloadMaxBytes)) {
            update(job, "failed")
            notifyFailure(job.chatId, "다운로드한 영상 검증에 실패했어요.")
            return
        }

        val waiting = update(job, "delivery_pending")
        val first = dispatchAndConfirm(waiting, bytes)
        if (first != null) {
            update(waiting, "delivered")
            log("YouTube delivery confirmed jobId=${job.jobId} logId=$first")
            return
        }

        // Direct-share dispatch only proves that Kakao accepted the intent.
        // Do not automatically send a second large video: on slower devices
        // that doubles Kakao's encoder work and makes a timeout more likely.
        update(waiting, "awaiting_unlock")
        notifyFailure(
            job.chatId,
            "영상 다운로드는 완료됐지만 카카오톡 전송이 확인되지 않았어요. " +
                "자동 재전송은 하지 않았어요. 잠시 후 '헤이봇 유튜브 재전송'으로 한 번만 다시 시도해주세요."
        )
    }

    private suspend fun dispatchAndConfirm(job: LocalYoutubeDownloadJob, bytes: ByteArray): Long? {
        val waiter = DeliveryWaiter(job.jobId, CompletableDeferred())
        deliveryWaiters.computeIfAbsent(job.chatId) { ConcurrentLinkedQueue() }.add(waiter)
        val dispatch = runCatching { youtubeDownloadSender.send(job.chatId, bytes) }
        if (dispatch.isFailure) {
            removeWaiter(job.chatId, waiter)
            return null
        }
        val confirmed = withTimeoutOrNull(settings.deliveryConfirmTimeoutMillis) {
            waiter.confirmedLogId.await()
        }
        removeWaiter(job.chatId, waiter)
        return confirmed
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
        reply(incoming, "최근 유튜브 다운로드 상태: $label")
    }

    private suspend fun cancel(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming)
        if (
            job == null ||
            job.status !in setOf(
                "queued", "running", "succeeded", "delivery_pending", "awaiting_unlock"
            )
        ) {
            reply(incoming, "취소할 유튜브 다운로드 작업이 없어요.")
            return
        }
        gateway.cancel(job.jobId, job.chatId)
        update(job, "cancelled")
        pollers.remove(job.jobId)?.cancel()
        reply(incoming, "최근 유튜브 다운로드 작업을 취소했어요.")
    }

    private suspend fun delete(incoming: GlmIncomingMessage) {
        val job = stateStore.latest(incoming.chatId, incoming.userId)
        if (job == null) {
            reply(incoming, "삭제할 유튜브 다운로드 기록이 없어요.")
            return
        }
        gateway.cancel(job.jobId, job.chatId)
        update(job, "cancelled")
        pollers.remove(job.jobId)?.cancel()
        reply(incoming, "최근 유튜브 다운로드 작업을 삭제 대기 상태로 정리했어요.")
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

    private suspend fun latestPending(incoming: GlmIncomingMessage): LocalYoutubeDownloadJob? =
        stateStore.pending()
            .filter { it.chatId == incoming.chatId && it.userId == incoming.userId }
            .maxByOrNull { it.updatedAtMillis }

    private suspend fun update(job: LocalYoutubeDownloadJob, status: String): LocalYoutubeDownloadJob {
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
                reasonCode = "YOUTUBE_DOWNLOAD_JOB_FAILED"
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

    private fun isOutgoingYoutubeDownload(incoming: GlmIncomingMessage): Boolean =
        botId != 0L &&
            incoming.userId == botId &&
            incoming.chatId in settings.allowedChatIds &&
            incoming.messageType in OUTGOING_YOUTUBE_DOWNLOAD_TYPES

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
            log("Delayed youtubeDownload delivery reconciled jobId=${pending.jobId} logId=${incoming.logId}")
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
        "YOUTUBE_DOWNLOAD_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "영상 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "YOUTUBE_DOWNLOAD_TOO_DARK", "YOUTUBE_DOWNLOAD_TOO_BRIGHT", "YOUTUBE_DOWNLOAD_LOW_CONTRAST", "YOUTUBE_DOWNLOAD_LOW_ENTROPY" ->
            "완성 영상의 품질 기준을 통과하지 못했어요."
        else -> "영상 생성에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    companion object {
        // PD20의 카카오톡은 동영상 공유 intent를 type=3(POST)으로 기록한다.
        // 일부 버전의 type=16도 계속 허용해 이전 클라이언트와 호환한다.
        private val OUTGOING_YOUTUBE_DOWNLOAD_TYPES = setOf("3", "16")

        /** Defense in depth: proxy-youtubeDownload runs ffprobe QC; Iris additionally
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
