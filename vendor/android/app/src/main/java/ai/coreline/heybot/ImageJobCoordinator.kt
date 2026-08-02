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

fun interface ImageTextReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

fun interface ImageBytesReplySender {
    fun send(chatId: Long, bytes: ByteArray)
}

class ImageJobCoordinator(
    private val settings: ImageProxySettings,
    trigger: String,
    private val botId: Long,
    private val gateway: ImageProxyGateway,
    private val textSender: ImageTextReplySender,
    private val imageSender: ImageBytesReplySender,
    private val stateStore: ImageJobStateStore,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.legacy(settings.allowedChatIds),
    private val log: (String) -> Unit = ::println,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val parser = ImageCommandParser(trigger, settings.promptMaxChars)
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
                log("Image job state initialization failed: ${it::class.simpleName}")
            }
            ready.complete(Unit)
            log("Image proxy coordinator ready")
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (isOutgoingImage(incoming)) {
            confirmDelivery(incoming)
            return
        }
        if (!isCandidate(incoming)) return
        val command = parser.parse(incoming.message) ?: return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.IMAGE)
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = RequestTraceKind.IMAGE
        )
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.IMAGE)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "IMAGE_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch {
            ready.await()
            when (command) {
                is ImageCommand.Generate -> create(incoming, command.prompt)
                is ImageCommand.Invalid -> reply(incoming, command.reason)
                ImageCommand.Status -> showStatus(incoming)
                ImageCommand.Cancel -> cancel(incoming)
                ImageCommand.Retry -> retry(incoming)
            }
        }
    }

    fun close() {
        scope.cancel()
    }

    private suspend fun create(incoming: GlmIncomingMessage, prompt: String) {
        val policySnapshot = roomCapabilityPolicy.snapshot()
        val roomCapabilityRevision = policySnapshot
            .capabilityRevision(incoming.chatId, RoomCapability.IMAGE) ?: return
        if (!roomCapabilityPolicy.isCurrent(
                roomCapabilityRevision,
                incoming.chatId,
                RoomCapability.IMAGE
            )
        ) return
        if (stateStore.countPending(incoming.chatId) >= settings.maxPendingPerRoom) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.QUEUE_FULL,
                reasonCode = "IMAGE_ROOM_QUEUE_FULL"
            )
            reply(incoming, "이 방의 이미지 요청이 이미 많이 쌓여 있어요. 완료 후 다시 요청해주세요.")
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
                reply(incoming, "이미지 요청 횟수가 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val requestId = "image:${incoming.chatId}:${incoming.logId}"
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED)
        gateway.create(
            requestId = requestId,
            chatId = incoming.chatId,
            userId = incoming.userId,
            logId = incoming.logId,
            prompt = prompt
        ).onSuccess { remote ->
            if (remote.chatId != incoming.chatId.toString()) {
                log("Image job rejected: chat ID mismatch")
                reply(incoming, "이미지 요청 정보가 일치하지 않아 중단했어요.")
                return@onSuccess
            }
            val now = nowMillis()
            val local = LocalImageJob(
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
            reply(incoming, "이미지 요청을 접수했어요. 완성되면 이 방으로 보내드릴게요.")
            startPolling(local)
        }.onFailure {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "IMAGE_CREATE_FAILED"
            )
            log("Image job create failed: ${it::class.simpleName}")
            reply(incoming, "이미지 서버에 연결하지 못했어요. 텍스트 대화는 계속 사용할 수 있어요.")
        }
    }

    private fun startPolling(job: LocalImageJob) {
        pollers.computeIfAbsent(job.jobId) {
            scope.launch {
                poll(job)
            }.also { launched ->
                launched.invokeOnCompletion { pollers.remove(job.jobId, launched) }
            }
        }
    }

    private suspend fun poll(initial: LocalImageJob) {
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
                log("Image polling stopped: chat ID mismatch")
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
                    notifyFailure(local.chatId, "이미지 작업 상태를 확인할 수 없어요.")
                    return
                }
            }
        }
        gateway.cancel(local.jobId, local.chatId)
        update(local, "failed")
        notifyFailure(local.chatId, "이미지 생성 시간이 너무 길어 작업을 종료했어요.")
    }

    private suspend fun deliver(job: LocalImageJob) {
        deliveryLocks.computeIfAbsent(job.chatId) { Mutex() }.withLock {
            deliverLocked(job)
        }
    }

    private suspend fun deliverLocked(job: LocalImageJob) {
        if (!roomCapabilityPolicy.isCurrent(
                job.roomCapabilityRevision,
                job.chatId,
                RoomCapability.IMAGE
            )
        ) {
            update(job, "cancelled")
            log("Image delivery skipped: room capability changed")
            return
        }
        val downloadResult = gateway.download(job.jobId, job.chatId)
        if (downloadResult.isFailure) {
            update(job, "failed")
            notifyFailure(job.chatId, "완성 이미지를 내려받지 못했어요.")
            return
        }
        val bytes = downloadResult.getOrThrow()
        if (!isValidPng(bytes, settings.imageMaxBytes)) {
            update(job, "failed")
            notifyFailure(job.chatId, "완성 이미지 검증에 실패했어요.")
            return
        }

        val waiting = update(job, "delivery_pending")
        val waiter = DeliveryWaiter(job.jobId, CompletableDeferred())
        deliveryWaiters
            .computeIfAbsent(job.chatId) { ConcurrentLinkedQueue() }
            .add(waiter)

        val dispatch = runCatching { imageSender.send(job.chatId, bytes) }
        if (dispatch.isFailure) {
            removeWaiter(job.chatId, waiter)
            update(waiting, "failed")
            notifyFailure(job.chatId, "이미지는 완성됐지만 카카오 전송을 시작하지 못했어요.")
            return
        }

        val confirmedLogId = withTimeoutOrNull(settings.deliveryConfirmTimeoutMillis) {
            waiter.confirmedLogId.await()
        }
        removeWaiter(job.chatId, waiter)
        if (confirmedLogId != null) {
            update(waiting, "delivered")
            log("Image delivery confirmed jobId=${job.jobId} logId=$confirmedLogId")
            return
        }

        update(waiting, "awaiting_unlock")
        notifyFailure(
            job.chatId,
            "이미지는 완성됐지만 카카오톡 전송이 확인되지 않았어요. " +
                "카카오톡 잠금을 해제한 뒤 '헤이봇 이미지 재전송'을 입력해주세요."
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
        reply(incoming, "최근 이미지 작업 상태: $label")
    }

    private suspend fun cancel(incoming: GlmIncomingMessage) {
        val job = latestPending(incoming)
        if (
            job == null ||
            job.status !in setOf(
                "queued", "running", "succeeded", "delivery_pending", "awaiting_unlock"
            )
        ) {
            reply(incoming, "취소할 이미지 작업이 없어요.")
            return
        }
        gateway.cancel(job.jobId, job.chatId)
        update(job, "cancelled")
        pollers.remove(job.jobId)?.cancel()
        reply(incoming, "최근 이미지 작업을 취소했어요.")
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
            reply(incoming, "재전송을 기다리는 이미지가 없어요.")
            return
        }
        if (pollers[job.jobId]?.isActive == true) {
            reply(incoming, "이미지 전송을 이미 확인하고 있어요.")
            return
        }
        val refreshed = job.copy(
            status = "succeeded",
            deadlineAtMillis = nowMillis() + settings.jobTimeoutMillis,
            updatedAtMillis = nowMillis()
        )
        stateStore.upsert(refreshed)
        reply(incoming, "완성된 이미지를 다시 전송할게요.")
        startPolling(refreshed)
    }

    private suspend fun latestPending(incoming: GlmIncomingMessage): LocalImageJob? =
        stateStore.pending()
            .filter { it.chatId == incoming.chatId && it.userId == incoming.userId }
            .maxByOrNull { it.updatedAtMillis }

    private suspend fun update(job: LocalImageJob, status: String): LocalImageJob {
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
                reasonCode = "IMAGE_JOB_FAILED"
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

    private fun isOutgoingImage(incoming: GlmIncomingMessage): Boolean =
        botId != 0L &&
            incoming.userId == botId &&
            incoming.chatId in settings.allowedChatIds &&
            incoming.messageType in OUTGOING_IMAGE_TYPES

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
            log("Delayed image delivery reconciled jobId=${pending.jobId} logId=${incoming.logId}")
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
        "IMAGE_QUEUE_FULL", "ROOM_QUEUE_LIMIT", "CODEX_QUEUE_FULL" ->
            "이미지 요청이 많이 쌓여 있어요. 잠시 후 다시 요청해주세요."
        "IMAGE_TOO_DARK", "IMAGE_TOO_BRIGHT", "IMAGE_LOW_CONTRAST", "IMAGE_LOW_ENTROPY" ->
            "완성 이미지의 품질 기준을 통과하지 못했어요."
        else -> "이미지 생성에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    companion object {
        private val OUTGOING_IMAGE_TYPES = setOf("2", "3")

        fun isValidPng(bytes: ByteArray, maximumBytes: Int): Boolean {
            if (bytes.size !in 24..maximumBytes) return false
            val signature = byteArrayOf(
                0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A
            )
            if (!bytes.copyOfRange(0, 8).contentEquals(signature)) return false
            fun uint32(offset: Int): Long =
                ((bytes[offset].toLong() and 0xff) shl 24) or
                    ((bytes[offset + 1].toLong() and 0xff) shl 16) or
                    ((bytes[offset + 2].toLong() and 0xff) shl 8) or
                    (bytes[offset + 3].toLong() and 0xff)
            val width = uint32(16)
            val height = uint32(20)
            return width in 256L..4_096L && height in 256L..4_096L
        }
    }

    private data class DeliveryWaiter(
        val jobId: String,
        val confirmedLogId: CompletableDeferred<Long>
    )
}
