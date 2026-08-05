package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

fun interface AudioAnalysisReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

class AudioAnalysisCoordinator(
    private val settings: AudioAnalysisSettings,
    private val trigger: String,
    private val botId: Long,
    private val gateway: AudioAnalysisGateway,
    private val summaryGenerator: AudioSummaryGenerator,
    private val engineModeStore: ConversationEngineModeStore,
    private val replySender: AudioAnalysisReplySender,
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore,
    private val stateStore: AudioAnalysisStateStore = InMemoryAudioAnalysisStateStore(),
    private val recentStore: RecentIncomingAudioStore = RecentIncomingAudioStore(
        retentionMillis = settings.recentAudioWindowMillis
    ),
    private val attachmentLookup: AudioAttachmentLookup = EmptyAudioAttachmentLookup,
    private val safety: ReplySafetyPolicy = ReplySafetyPolicy(maxChars = 3_200),
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val audioContextStore: AudioConversationContextStore = AudioConversationContextStore(),
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = ::println,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    private val ready = CompletableDeferred<Unit>()
    private val router = BotCommandRouter(trigger)
    private val pollers = ConcurrentHashMap<String, Job>()
    private val admission = RequestAdmissionController(
        roomWindowMillis = settings.rateWindowMillis,
        roomMaxRequests = settings.roomRateMaxRequests,
        userWindowMillis = settings.rateWindowMillis,
        userMaxRequests = settings.userRateMaxRequests,
        duplicateWindowMillis = 8_000L,
        nowMillis = nowMillis
    )

    init {
        scope.launch {
            runCatching { stateStore.initialize() }
                .onFailure { log("Audio state initialization failed: ${it::class.simpleName}") }
            ready.complete(Unit)
            stateStore.pending().forEach(::resume)
        }
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        val attachment = incoming.audioAttachment
        if (attachment != null && incoming.chatId in settings.allowedChatIds &&
            (botId == 0L || incoming.userId != botId)
        ) {
            recentStore.put(attachment)
            if (roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.AUDIO_AUTO_ANALYSIS) &&
                roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.AUDIO_ANALYSIS) &&
                roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT)
            ) {
                requestTraceStore.ensureReceived(incoming, RequestTraceKind.AUDIO)
                scope.launch {
                    ready.await()
                    create(incoming, AudioSummaryProfile(), attachment)
                }
            }
        }

        if (incoming.messageType != "1" || incoming.chatId !in settings.allowedChatIds ||
            (botId != 0L && incoming.userId == botId)
        ) return
        val command = router.route(incoming.message)
        if (command !is BotCommand.SummarizeAudio && command !is BotCommand.AudioStatus &&
            command !is BotCommand.CancelAudio && command !is BotCommand.ResummarizeAudio &&
            command !is BotCommand.ResendAudio && command !is BotCommand.AudioTranscript && command !is BotCommand.AudioEvidence &&
            command !is BotCommand.DeleteAudio
        ) return
        requestTraceStore.ensureReceived(incoming, RequestTraceKind.AUDIO)
        requestTraceStore.record(incoming.traceId, RequestTraceStage.CLASSIFIED, kind = RequestTraceKind.AUDIO)
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.AUDIO_ANALYSIS) ||
            !roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT)
        ) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "AUDIO_ANALYSIS_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        scope.launch {
            ready.await()
            when (command) {
                is BotCommand.SummarizeAudio -> create(incoming, command.profile, selectSource(incoming))
                BotCommand.AudioStatus -> status(incoming)
                BotCommand.CancelAudio -> cancel(incoming)
                BotCommand.ResummarizeAudio -> resummarize(incoming)
                BotCommand.ResendAudio -> resend(incoming)
                is BotCommand.AudioTranscript -> transcript(incoming, command.page, evidenceOnly = false)
                is BotCommand.AudioEvidence -> transcript(incoming, command.page, evidenceOnly = true)
                BotCommand.DeleteAudio -> purge(incoming)
                else -> Unit
            }
        }
    }

    fun close() = scope.cancel()

    private fun selectSource(incoming: GlmIncomingMessage): IncomingAudioAttachment? {
        val source = if (incoming.threadId != null) {
            (recentStore.findExact(incoming.chatId, incoming.threadId)
                ?: attachmentLookup.findExact(incoming.chatId, incoming.threadId))
        } else {
            val cutoff = nowMillis() - settings.recentAudioWindowMillis
            recentStore.findRecent(incoming.chatId, cutoff)
                ?: attachmentLookup.findLatest(incoming.chatId, cutoff)
        }
        source?.let(recentStore::put)
        return source
    }

    private suspend fun create(
        incoming: GlmIncomingMessage,
        profile: AudioSummaryProfile,
        source: IncomingAudioAttachment?
    ) {
        if (source == null) {
            reply(
                incoming,
                if (incoming.threadId != null) "답장한 음성 파일을 찾지 못했어요. 같은 방의 MP3·M4A·WAV에 답장해 다시 요청해주세요."
                else "최근 30분 안에 이 방에 올라온 MP3·M4A·WAV를 찾지 못했어요. 파일을 보낸 뒤 다시 요청해주세요."
            )
            return
        }
        val existing = stateStore.latest(incoming.chatId, incoming.userId)
        if (existing?.status == "delivery_pending") {
            reply(incoming, "이전 음성 요약의 전송 확인이 남아 있어요. ‘헤이봇 음성 재전송’으로 이어서 보내주세요.")
            return
        }
        if (existing?.status in PENDING) {
            reply(incoming, "이미 음성 분석이 진행 중이에요. ‘헤이봇 음성 상태’로 확인해주세요.")
            return
        }
        when (admission.admit(incoming.copy(message = "audio:${source.sourceLogId}"))) {
            AdmissionResult.Accepted -> requestTraceStore.record(
                incoming.traceId, RequestTraceStage.ADMITTED
            )
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                requestTraceStore.record(
                    incoming.traceId, RequestTraceStage.DUPLICATE, reasonCode = "DUPLICATE_REQUEST"
                )
                return
            }
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                requestTraceStore.record(
                    incoming.traceId, RequestTraceStage.RATE_LIMITED, reasonCode = "AUDIO_RATE_LIMIT"
                )
                reply(incoming, "음성 분석 요청이 많아요. 잠시 후 다시 요청해주세요.")
                return
            }
        }
        val revision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.AUDIO_ANALYSIS) ?: return
        val engine = engineModeStore.refresh().engine
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_STARTED, engine = engine.name)
        val remote = gateway.create(
            "audio:${incoming.chatId}:${source.sourceLogId}", incoming.chatId, source
        ).getOrElse { error ->
            requestTraceStore.record(
                incoming.traceId, RequestTraceStage.PROVIDER_FAILED,
                reasonCode = (error as? AudioProxyException)?.reasonCode ?: "AUDIO_CREATE_FAILED"
            )
            reply(incoming, createFailureMessage(error))
            return
        }
        if (remote.chatId != incoming.chatId.toString()) {
            reply(incoming, "음성 분석 요청의 방 정보가 일치하지 않아 중단했어요.")
            return
        }
        val now = nowMillis()
        val local = LocalAudioJob(
            remote.jobId, remote.requestId, incoming.chatId, incoming.userId, source.sourceLogId,
            remote.status, profile, engine, revision, now, now + settings.jobTimeoutMillis, now
        )
        stateStore.upsert(local)
        requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_SUCCEEDED)
        reply(
            incoming,
            "음성 분석을 시작했어요. ${engine.displayName} 엔진으로 전사 후 ${profile.pattern.displayName}·${profile.view.displayName} 요약을 전달할게요."
        )
        startPolling(local, incoming, remote)
    }

    private fun resume(local: LocalAudioJob) {
        // A pending text part must be replayed only through the explicit command;
        // replaying it on process restart could duplicate a user-visible summary.
        if (local.status == "delivery_pending") return
        if (local.deadlineAtMillis <= nowMillis()) return
        val incoming = GlmIncomingMessage(
            logId = local.sourceLogId,
            chatId = local.chatId,
            userId = local.userId,
            messageType = "1",
            message = "",
            threadId = null,
            traceId = RequestTraceIds.from(local.chatId, local.sourceLogId)
        )
        scope.launch {
            gateway.status(local.jobId, local.chatId).getOrNull()?.let {
                startPolling(local, incoming, it)
            }
        }
    }

    private fun startPolling(local: LocalAudioJob, incoming: GlmIncomingMessage, initial: AudioAnalysisJob) {
        val launched = scope.launch(start = CoroutineStart.LAZY) {
            var remote = initial
            while (nowMillis() < local.deadlineAtMillis) {
                if (!roomCapabilityPolicy.isCurrent(
                        local.roomCapabilityRevision, local.chatId, RoomCapability.AUDIO_ANALYSIS
                    )
                ) {
                    gateway.cancel(local.jobId, local.chatId)
                    stateStore.upsert(local.copy(status = "cancelled", updatedAtMillis = nowMillis()))
                    return@launch
                }
                when (remote.status) {
                    "queued", "fetching", "validating", "normalizing", "transcribing" -> {
                        stateStore.upsert(local.copy(status = remote.status, updatedAtMillis = nowMillis()))
                        delay(settings.pollIntervalMillis)
                        remote = gateway.status(local.jobId, local.chatId).getOrElse { error ->
                            requestTraceStore.record(
                                incoming.traceId, RequestTraceStage.PROVIDER_FAILED,
                                reasonCode = (error as? AudioProxyException)?.reasonCode ?: "AUDIO_STATUS_FAILED"
                            )
                            reply(incoming, createFailureMessage(error))
                            return@launch
                        }
                    }
                    "transcribed" -> {
                        val result = remote.result
                        if (result == null || result.segments.isEmpty()) {
                            stateStore.upsert(local.copy(status = "failed", updatedAtMillis = nowMillis()))
                            reply(incoming, "음성에서 한국어 발화를 찾지 못했어요.")
                            return@launch
                        }
                        summarizeAndDeliver(incoming, local, result)
                        return@launch
                    }
                    "failed" -> {
                        stateStore.upsert(local.copy(status = "failed", updatedAtMillis = nowMillis()))
                        reply(incoming, failureMessage(remote.errorCode))
                        return@launch
                    }
                    "cancelled" -> {
                        stateStore.upsert(local.copy(status = "cancelled", updatedAtMillis = nowMillis()))
                        return@launch
                    }
                    else -> {
                        reply(incoming, "음성 분석 상태를 확인할 수 없어요.")
                        return@launch
                    }
                }
            }
            gateway.cancel(local.jobId, local.chatId)
            stateStore.upsert(local.copy(status = "failed", updatedAtMillis = nowMillis()))
            reply(incoming, "음성 분석 제한 시간을 초과해 작업을 종료했어요.")
        }
        if (pollers.putIfAbsent(local.jobId, launched) != null) {
            launched.cancel()
            return
        }
        launched.invokeOnCompletion { pollers.remove(local.jobId, launched) }
        launched.start()
    }

    private suspend fun summarizeAndDeliver(
        incoming: GlmIncomingMessage,
        local: LocalAudioJob,
        transcript: AudioTranscriptResult
    ) {
        stateStore.upsert(local.copy(status = "summarizing", updatedAtMillis = nowMillis()))
        val output = summaryGenerator.summarize(transcript, local.profile, local.engine).getOrElse { error ->
            stateStore.upsert(local.copy(status = "transcribed", updatedAtMillis = nowMillis()))
            requestTraceStore.record(
                incoming.traceId, RequestTraceStage.PROVIDER_FAILED,
                reasonCode = if (error.message == "SUMMARY_OUTPUT_INVALID") error.message else "AUDIO_SUMMARY_FAILED"
            )
            reply(incoming, "음성 전사는 완료했지만 요약에 실패했어요. ‘헤이봇 음성 재요약’으로 다시 시도할 수 있어요.")
            return
        }
        val safe = safety.apply(output.text)
        if (safe !is ReplySafetyResult.Safe) {
            stateStore.upsert(local.copy(status = "transcribed", updatedAtMillis = nowMillis()))
            reply(incoming, "음성 요약 결과를 안전하게 표시할 수 없어요.")
            return
        }
        val heading = "음성 요약 · ${local.profile.pattern.displayName}/${local.profile.view.displayName} · ${output.engine.displayName}\n"
        val parts = MultipartTextDelivery.split(heading + safe.text)
        if (parts.isEmpty()) {
            stateStore.upsert(local.copy(status = "transcribed", updatedAtMillis = nowMillis()))
            reply(incoming, "음성 요약 결과가 비어 있어 전달하지 못했어요.")
            return
        }
        val delivery = AudioDeliveryState(
            safeSummary = safe.text,
            evidenceIds = output.document.allEvidenceIds(),
            parts = parts.map(::AudioDeliveryPart)
        )
        val pending = local.copy(
            status = "delivery_pending",
            updatedAtMillis = nowMillis(),
            delivery = delivery
        )
        stateStore.upsert(pending)
        deliverPendingParts(incoming, pending, MAX_AUTOMATIC_DELIVERY_ATTEMPTS)
    }

    private suspend fun status(incoming: GlmIncomingMessage) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "확인할 음성 분석 작업이 없어요.")
            return
        }
        val remote = if (local.status in LOCAL_SUMMARY_STATUSES) null
        else gateway.status(local.jobId, local.chatId).getOrNull()
        val status = if (local.status in LOCAL_SUMMARY_STATUSES) local.status
        else remote?.status ?: local.status
        if (remote != null && local.status !in LOCAL_SUMMARY_STATUSES && remote.status != local.status) {
            stateStore.upsert(local.copy(status = remote.status, updatedAtMillis = nowMillis()))
        }
        val deliveryStatus = local.delivery?.let { delivery ->
            "\n전송 확인: ${delivery.parts.count { it.confirmedLogId != null }}/${delivery.parts.size}"
        }.orEmpty()
        reply(incoming, "음성 분석 상태: ${statusLabel(status)}\n엔진: ${local.engine.displayName}\n형식: ${local.profile.pattern.displayName}·${local.profile.view.displayName}$deliveryStatus")
    }

    private suspend fun cancel(incoming: GlmIncomingMessage) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "취소할 음성 분석 작업이 없어요.")
            return
        }
        if (local.status !in PENDING) {
            reply(incoming, "현재 음성 작업은 진행 중이 아니에요. 기록을 지우려면 ‘헤이봇 음성 삭제’를 사용해주세요.")
            return
        }
        gateway.cancel(local.jobId, local.chatId).getOrElse {
            reply(incoming, "음성 분석 취소 요청을 처리하지 못했어요.")
            return
        }
        pollers.remove(local.jobId)?.cancel()
        stateStore.upsert(local.copy(status = "cancelled", updatedAtMillis = nowMillis()))
        reply(incoming, "음성 분석을 취소했어요.")
    }

    private suspend fun resummarize(incoming: GlmIncomingMessage) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "재요약할 음성 기록이 없어요.")
            return
        }
        val remote = gateway.status(local.jobId, local.chatId).getOrNull()
        val transcript = remote?.result ?: run {
            reply(incoming, "전사가 완료된 작업만 재요약할 수 있어요.")
            return
        }
        audioContextStore.removeJob(local.chatId, local.jobId)
        val next = local.copy(
            status = "summarizing",
            engine = engineModeStore.refresh().engine,
            updatedAtMillis = nowMillis(),
            delivery = null
        )
        stateStore.upsert(next)
        reply(incoming, "저장된 전사문으로 다시 요약할게요. STT는 다시 실행하지 않아요.")
        summarizeAndDeliver(incoming, next, transcript)
    }

    private suspend fun resend(incoming: GlmIncomingMessage) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "재전송할 음성 요약이 없어요.")
            return
        }
        if (local.status != "delivery_pending" || local.delivery == null) {
            reply(incoming, "재전송 대기 중인 음성 요약이 없어요.")
            return
        }
        deliverPendingParts(incoming, local, MAX_MANUAL_DELIVERY_ATTEMPTS)
    }

    private suspend fun transcript(incoming: GlmIncomingMessage, page: Int, evidenceOnly: Boolean) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "확인할 음성 기록이 없어요.")
            return
        }
        val result = gateway.status(local.jobId, local.chatId).getOrNull()?.result ?: run {
            reply(incoming, "전사가 아직 완료되지 않았어요.")
            return
        }
        val lines = result.segments.map {
            "${it.id} [${AudioSummaryGenerator.time(it.startMs)}-${AudioSummaryGenerator.time(it.endMs)}] ${it.text}"
        }
        val pages = lines.chunked(12)
        val selected = pages.getOrNull(page - 1) ?: run {
            reply(incoming, "요청한 페이지가 없어요. 원문은 총 ${pages.size}페이지예요.")
            return
        }
        val title = if (evidenceOnly) "음성 근거" else "음성 원문"
        when (val safe = safety.apply(selected.joinToString("\n"))) {
            is ReplySafetyResult.Safe ->
                sendMultipart(incoming, "$title $page/${pages.size}\n${safe.text}", 4)
            is ReplySafetyResult.Blocked ->
                reply(incoming, "음성 원문에 안전하게 표시할 수 없는 정보가 포함되어 있어요.")
        }
    }

    private suspend fun purge(incoming: GlmIncomingMessage) {
        val local = stateStore.latest(incoming.chatId, incoming.userId) ?: run {
            reply(incoming, "삭제할 음성 기록이 없어요.")
            return
        }
        if (!gateway.purge(local.jobId, local.chatId).getOrDefault(false)) {
            reply(incoming, "음성 기록 삭제를 확인하지 못했어요.")
            return
        }
        pollers.remove(local.jobId)?.cancel()
        stateStore.remove(local.jobId)
        audioContextStore.removeJob(local.chatId, local.jobId)
        reply(incoming, "저장된 음성 전사 기록을 삭제했어요.")
    }

    private fun sendMultipart(incoming: GlmIncomingMessage, text: String, maxParts: Int = 8) {
        MultipartTextDelivery.split(text, maxParts).forEach { reply(incoming, it) }
    }

    /**
     * Sends every unconfirmed segment in order. A part is marked complete only
     * after its exact outgoing Kakao DB row is observed. One automatic replay
     * is attempted; any remaining part is durably held for `헤이봇 음성 재전송`.
     */
    private suspend fun deliverPendingParts(
        incoming: GlmIncomingMessage,
        initial: LocalAudioJob,
        maxAttemptsPerPart: Int
    ) {
        var current = initial
        val originalDelivery = current.delivery ?: return
        for (index in originalDelivery.parts.indices) {
            while (true) {
                val delivery = current.delivery ?: return
                val part = delivery.parts[index]
                if (part.confirmedLogId != null) break
                if (part.attempts >= maxAttemptsPerPart) {
                    holdForResend(incoming, current)
                    return
                }
                val rendered = if (part.attempts == 0) part.text else MultipartTextDelivery.resend(part.text)
                val attempt = current.copy(
                    status = "delivery_pending",
                    updatedAtMillis = nowMillis(),
                    delivery = delivery.withPart(index, part.copy(attempts = part.attempts + 1))
                )
                stateStore.upsert(attempt)
                current = attempt
                if (!reply(incoming, rendered)) {
                    holdForResend(incoming, current)
                    return
                }
                val logId = textDeliveryTracker?.awaitConfirmedLogId(incoming.traceId) ?: 0L
                if (logId == 0L && textDeliveryTracker != null) continue
                val confirmed = current.delivery ?: return
                current = current.copy(
                    status = "delivery_pending",
                    updatedAtMillis = nowMillis(),
                    delivery = confirmed.withPart(index, confirmed.parts[index].copy(confirmedLogId = logId))
                )
                stateStore.upsert(current)
                break
            }
        }
        val completed = current.delivery ?: return
        if (completed.parts.all { it.confirmedLogId != null }) {
            val succeeded = current.copy(status = "succeeded", updatedAtMillis = nowMillis())
            stateStore.upsert(succeeded)
            commitConversationContext(succeeded)
            requestTraceStore.record(incoming.traceId, RequestTraceStage.FINISHED)
        } else {
            holdForResend(incoming, current)
        }
    }

    private fun commitConversationContext(local: LocalAudioJob) {
        val delivery = local.delivery ?: return
        // Synthetic test delivery IDs are 0; context is available only after all
        // real Kakao DB rows exist and the original room policy remains current.
        val resultLogIds = delivery.parts.mapNotNull { it.confirmedLogId?.takeIf { id -> id > 0L } }
        if (resultLogIds.size != delivery.parts.size ||
            !roomCapabilityPolicy.isCurrent(local.roomCapabilityRevision, local.chatId, RoomCapability.AUDIO_ANALYSIS) ||
            !roomCapabilityPolicy.allows(local.chatId, RoomCapability.TEXT)
        ) return
        val now = nowMillis()
        val stored = audioContextStore.put(
            AudioConversationContext(
                chatId = local.chatId,
                ownerUserId = local.userId,
                jobId = local.jobId,
                sourceLogId = local.sourceLogId,
                resultLogIds = resultLogIds,
                profile = local.profile,
                safeSummary = delivery.safeSummary,
                evidenceIds = delivery.evidenceIds,
                capabilityRevision = local.roomCapabilityRevision,
                createdAtMillis = now,
                expiresAtMillis = now + settings.recentAudioWindowMillis
            )
        )
        if (!stored) log("Audio conversation context was not persisted job=${local.jobId}")
    }

    private fun AudioDeliveryState.withPart(index: Int, value: AudioDeliveryPart): AudioDeliveryState =
        copy(parts = parts.mapIndexed { current, part -> if (current == index) value else part })

    private suspend fun holdForResend(incoming: GlmIncomingMessage, local: LocalAudioJob) {
        stateStore.upsert(local.copy(status = "delivery_pending", updatedAtMillis = nowMillis()))
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.UNCONFIRMED,
            reasonCode = "AUDIO_MULTIPART_DB_UNCONFIRMED"
        )
        // Use a separate trace so this notice never replaces the exact part
        // digest that is still waiting in TextDeliveryTracker.
        val notice = incoming.copy(traceId = "${incoming.traceId}:audio-pending:${local.jobId.take(8)}")
        requestTraceStore.ensureReceived(notice, RequestTraceKind.AUDIO)
        reply(notice, "음성 요약 일부의 카카오톡 전송을 확인하지 못했어요. ‘헤이봇 음성 재전송’으로 확인되지 않은 부분만 다시 보낼 수 있어요.")
        log("Audio summary part pending DB confirmation job=${local.jobId}")
    }

    private fun reply(incoming: GlmIncomingMessage, text: String): Boolean {
        val message = text.take(480)
        textDeliveryTracker?.enqueued(incoming.traceId, incoming.chatId, message, incoming.threadId)
            ?: requestTraceStore.record(incoming.traceId, RequestTraceStage.ENQUEUED)
        return runCatching { replySender.send(incoming.chatId, message, incoming.threadId) }
            .fold(
                onSuccess = { true },
                onFailure = {
                    requestTraceStore.record(
                        incoming.traceId, RequestTraceStage.DISPATCH_FAILED,
                        reasonCode = "REPLY_SENDER_EXCEPTION"
                    )
                    false
                }
            )
    }

    private fun statusLabel(status: String): String = when (status) {
        "queued" -> "대기"
        "fetching" -> "파일 가져오는 중"
        "validating" -> "파일 검증 중"
        "normalizing" -> "음성 정규화 중"
        "transcribing" -> "한국어 STT 중"
        "transcribed" -> "전사 완료"
        "summarizing" -> "요약 중"
        "delivery_pending" -> "전송 확인 대기"
        "succeeded" -> "완료"
        "failed" -> "실패"
        "cancelled" -> "취소"
        else -> status
    }

    private fun createFailureMessage(error: Throwable): String = when (error) {
        is AudioTransportException -> "음성 분석 서버에 연결하지 못했어요. 다른 기능은 계속 사용할 수 있어요."
        is AudioAuthorizationException -> "음성 분석 서버 인증 설정을 확인할 수 없어요."
        is AudioHttpException -> failureMessage(error.reasonCode)
        else -> "음성 분석 요청을 처리하지 못했어요. 잠시 후 다시 요청해주세요."
    }

    private fun failureMessage(code: String?): String = when (code) {
        "SOURCE_EXPIRED" -> "음성 원본 주소가 만료됐어요. 파일을 다시 보내주세요."
        "ROOM_QUEUE_LIMIT" -> "이 방의 음성 분석이 이미 진행 중이에요."
        "SOURCE_TOO_LARGE" -> "음성 파일이 너무 커서 분석할 수 없어요."
        "AUDIO_DURATION_LIMIT" -> "음성이 120분을 넘어 분석할 수 없어요."
        "AUDIO_MAGIC_MISMATCH", "INVALID_AUDIO", "UNSUPPORTED_AUDIO_STREAMS" ->
            "실제 오디오 형식을 확인하지 못했어요. MP3·M4A·WAV 파일인지 확인해주세요."
        "AUDIO_NO_SPEECH" -> "음성에서 한국어 발화를 찾지 못했어요."
        else -> "음성 분석에 실패했어요. 잠시 후 다시 요청해주세요."
    }

    private fun AudioSummaryDocument.allEvidenceIds(): List<String> = buildList {
        addAll(oneLineEvidence)
        keyPoints.forEach { addAll(it.evidence) }
        decisions.forEach { addAll(it.evidence) }
        actionItems.forEach { addAll(it.evidence) }
        openQuestions.forEach { addAll(it.evidence) }
    }.distinct()

    private companion object {
        const val MAX_AUTOMATIC_DELIVERY_ATTEMPTS = 2
        const val MAX_MANUAL_DELIVERY_ATTEMPTS = 9
        val PENDING = setOf("queued", "fetching", "validating", "normalizing", "transcribing", "summarizing", "delivery_pending")
        val LOCAL_SUMMARY_STATUSES = setOf("summarizing", "delivery_pending", "succeeded")
    }
}
