package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.ceil

data class GlmIncomingMessage(
    val logId: Long,
    val chatId: Long,
    val userId: Long,
    val messageType: String,
    val message: String,
    val threadId: Long?,
    /** Strictly parsed metadata only; decrypted attachment JSON is never retained here. */
    val imageAttachment: IncomingImageAttachment? = null,
    val traceId: String = RequestTraceIds.from(chatId, logId)
)

fun interface GlmReplySender {
    fun send(chatId: Long, message: String, threadId: Long?)
}

/**
 * Routes local commands immediately and sends GLM questions to a per-room FIFO
 * scheduler. Conversation state is separated by the exact (chatId, userId).
 */
class GlmAutoReplyHandler(
    private val settings: GlmSettings,
    private val botId: Long,
    private val gateway: GlmGateway,
    private val replySender: GlmReplySender,
    private val log: (String) -> Unit = ::println,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val delayForRetry: suspend (Long) -> Unit = { delay(it) },
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
    private val memoryStore: ConversationMemoryStore = InMemoryConversationMemoryStore(
        maxTurnsPerConversation = settings.memoryMaxTurns,
        ttlMillis = settings.memoryTtlMillis
    ),
    private val adminAuthorizer: AdminAuthorizer = AdminAuthorizer.empty(),
    private val metrics: BotMetrics = BotMetrics(nowMillis()),
    private val generalConversationModeStore: GeneralConversationModeStore = GeneralConversationModeStore(),
    private val conversationEngineModeStore: ConversationEngineModeStore = ConversationEngineModeStore.inMemory(),
    private val generalConversationArbiter: GeneralConversationArbiter = GeneralConversationArbiter(),
    private val generalConversationPendingStore: GeneralConversationPendingStore =
        GeneralConversationPendingStore(),
    private val generalConversationPolicy: GeneralConversationPolicy =
        GeneralConversationPolicy.disabled(),
    private val roomCapabilityPolicy: RoomCapabilityPolicyStore =
        RoomCapabilityPolicyStore.legacy(settings.allowedChatIds),
    private val replySafetyPolicy: ReplySafetyPolicy = ReplySafetyPolicy(),
    private val generalConversationCircuitBreaker: GeneralConversationCircuitBreaker =
        GeneralConversationCircuitBreaker(
            windowMillis = settings.generalConversation?.circuitWindowMillis
                ?: GlmSettings.DEFAULT_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MILLIS,
            failureThreshold = settings.generalConversation?.circuitFailureThreshold
                ?: GlmSettings.DEFAULT_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD,
            nowMillis = nowMillis
        ),
    private val selfTestRunner: SelfTestRunner = SelfTestRunner.production(),
    private val requestTraceStore: RequestTraceStore = RequestTraceStore.inMemory(nowMillis),
    private val textDeliveryTracker: TextDeliveryTracker? = null,
    private val visionContextStore: VisionConversationContextStore =
        VisionConversationContextStore()
) {
    private val commandRouter = BotCommandRouter(settings.trigger)
    private val visionContextResolver = VisionConversationContextResolver(
        visionContextStore,
        roomCapabilityPolicy,
        nowMillis,
        settings.visionSharedFollowUpWindowMillis
    )
    private val memoryReady = CompletableDeferred<Unit>()
    private val admission = RequestAdmissionController(
        roomWindowMillis = settings.roomRateWindowMillis,
        roomMaxRequests = settings.roomRateMaxRequests,
        userWindowMillis = settings.userRateWindowMillis,
        userMaxRequests = settings.userRateMaxRequests,
        duplicateWindowMillis = settings.duplicateWindowMillis,
        nowMillis = nowMillis
    )
    private val scheduler = GlmRoomScheduler(
        roomQueueCapacity = settings.roomQueueCapacity,
        totalQueueCapacity = settings.totalQueueCapacity,
        maxConcurrency = settings.maxConcurrency,
        parentScope = scope,
        process = { queued ->
            val modeSnapshot = queued.generalConversation
            if (modeSnapshot == null) {
                executeQuestion(
                    queued.incoming,
                    queued.question,
                    queued.roomCapabilityRevision,
                    queued.visionResultLogId
                )
            } else {
                executeGeneralConversation(
                    queued.incoming,
                    queued.question,
                    modeSnapshot,
                    queued.roomCapabilityRevision
                )
            }
        },
        log = log
    )

    init {
        scope.launch {
            runCatching {
                memoryStore.initialize()
                // Forces startup TTL pruning after a persisted file is loaded.
                val memory = memoryStore.stats(nowMillis())
                log(
                    "Conversation memory ready " +
                        "(conversations=${memory.conversations}, turns=${memory.turns})"
                )
            }.onFailure {
                log("Conversation memory initialization failed: ${it::class.simpleName}")
            }
            val vision = visionContextStore.stats(nowMillis())
            log("Vision conversation context ready=${vision.ready} contexts=${vision.contexts}")
            memoryReady.complete(Unit)
        }
        log(
            "GLM P1 scheduler ready " +
                "(concurrency=${settings.maxConcurrency}, " +
                "roomQueue=${settings.roomQueueCapacity}, totalQueue=${settings.totalQueueCapacity})"
        )
    }

    fun onIncoming(incoming: GlmIncomingMessage) {
        if (!isCandidate(incoming)) return
        requestTraceStore.ensureReceived(incoming)
        val command = commandRouter.route(incoming.message)
        if (command == null) {
            val exactVision = visionContextResolver.exact(incoming)
            val implicitVision = exactVision ?: visionContextResolver.implicit(incoming)
            if (implicitVision != null) {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.CLASSIFIED,
                    kind = RequestTraceKind.VISION_FOLLOW_UP
                )
                submitVisionFollowUp(incoming, implicitVision.resultLogId)
                return
            }
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.CLASSIFIED,
                kind = RequestTraceKind.GENERAL_CONVERSATION
            )
            if (!incoming.message.trim().startsWith(settings.trigger)) {
                submitGeneralConversation(incoming)
            }
            return
        }

        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = traceKind(command)
        )

        traceCapability(command)?.let { capability ->
            val allowed = roomCapabilityPolicy.allows(incoming.chatId, capability)
            requestTraceStore.record(
                incoming.traceId,
                if (allowed) RequestTraceStage.POLICY_ALLOWED else RequestTraceStage.POLICY_DENIED,
                reasonCode = if (allowed) null else "${capability.name}_CAPABILITY_DISABLED"
            )
            return
        }

        if (command !is BotCommand.GlmQuestion) {
            if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT) &&
                incoming.chatId != settings.adminControlChatId
            ) {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.POLICY_DENIED,
                    reasonCode = "TEXT_CAPABILITY_DISABLED"
                )
                return
            }
            requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
            scope.launch {
                memoryReady.await()
                handleLocalCommand(incoming, command)
            }
            return
        }

        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "TEXT_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        val roomCapabilityRevision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.TEXT) ?: return

        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                submitToQueue(
                    incoming,
                    command.question,
                    roomCapabilityRevision = roomCapabilityRevision
                )
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                metrics.recordDuplicateDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DUPLICATE,
                    reasonCode = "DUPLICATE_REQUEST"
                )
            }

            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
                val retryAfterMillis = when (result) {
                    is AdmissionResult.RoomRateLimited -> result.retryAfterMillis
                    is AdmissionResult.UserRateLimited -> result.retryAfterMillis
                    else -> 0L
                }
                safeReply(
                    incoming,
                    "요청이 조금 많아요. 약 ${ceil(retryAfterMillis / 1000.0).toLong()}초 뒤 다시 불러주세요."
                )
            }
        }
    }

    /**
     * Deterministic test entry point. Production input must use [onIncoming] so
     * that per-room FIFO and global concurrency controls are applied.
     */
    suspend fun process(incoming: GlmIncomingMessage) {
        if (!isCandidate(incoming)) return
        requestTraceStore.ensureReceived(incoming)
        val command = commandRouter.route(incoming.message)
        if (command == null) {
            val exactVision = visionContextResolver.exact(incoming)
            val implicitVision = exactVision ?: visionContextResolver.implicit(incoming)
            if (implicitVision != null) {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.CLASSIFIED,
                    kind = RequestTraceKind.VISION_FOLLOW_UP
                )
                memoryReady.await()
                processVisionFollowUp(incoming, implicitVision.resultLogId)
                return
            }
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.CLASSIFIED,
                kind = RequestTraceKind.GENERAL_CONVERSATION
            )
            if (!incoming.message.trim().startsWith(settings.trigger)) {
                processGeneralConversation(incoming)
            }
            return
        }
        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.CLASSIFIED,
            kind = traceKind(command)
        )
        memoryReady.await()

        traceCapability(command)?.let { capability ->
            val allowed = roomCapabilityPolicy.allows(incoming.chatId, capability)
            requestTraceStore.record(
                incoming.traceId,
                if (allowed) RequestTraceStage.POLICY_ALLOWED else RequestTraceStage.POLICY_DENIED,
                reasonCode = if (allowed) null else "${capability.name}_CAPABILITY_DISABLED"
            )
            return
        }

        if (command !is BotCommand.GlmQuestion) {
            if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT) &&
                incoming.chatId != settings.adminControlChatId
            ) {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.POLICY_DENIED,
                    reasonCode = "TEXT_CAPABILITY_DISABLED"
                )
                return
            }
            requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
            handleLocalCommand(incoming, command)
            return
        }

        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "TEXT_CAPABILITY_DISABLED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        val roomCapabilityRevision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.TEXT) ?: return

        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                executeQuestion(incoming, command.question, roomCapabilityRevision)
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                metrics.recordDuplicateDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DUPLICATE,
                    reasonCode = "DUPLICATE_REQUEST"
                )
            }

            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
                val retryAfterMillis = when (result) {
                    is AdmissionResult.RoomRateLimited -> result.retryAfterMillis
                    is AdmissionResult.UserRateLimited -> result.retryAfterMillis
                    else -> 0L
                }
                safeReply(
                    incoming,
                    "요청이 조금 많아요. 약 ${ceil(retryAfterMillis / 1000.0).toLong()}초 뒤 다시 불러주세요."
                )
            }
        }
    }

    fun queueSnapshot(): GlmQueueSnapshot = scheduler.snapshot()

    fun metricsSnapshot(): BotMetricsSnapshot = metrics.snapshot()

    fun close() {
        generalConversationModeStore.close()
        generalConversationPendingStore.clearAll()
        scheduler.close()
        scope.cancel()
    }

    private fun submitToQueue(
        incoming: GlmIncomingMessage,
        question: String,
        generalConversation: GeneralConversationModeSnapshot? = null,
        roomCapabilityRevision: Long,
        visionResultLogId: Long? = null
    ) {
        when (
            scheduler.submit(
                QueuedGlmRequest(
                    incoming,
                    question,
                    generalConversation,
                    roomCapabilityRevision,
                    visionResultLogId
                )
            )
        ) {
            is GlmQueueSubmitResult.Accepted -> Unit
            GlmQueueSubmitResult.RoomQueueFull,
            GlmQueueSubmitResult.TotalQueueFull -> {
                metrics.recordQueueFullDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.QUEUE_FULL,
                    reasonCode = "CONVERSATION_QUEUE_FULL"
                )
                if (generalConversation == null) {
                    safeReply(incoming, "지금 요청이 많이 쌓여 있어요. 잠시 후 다시 불러주세요.")
                }
            }

            GlmQueueSubmitResult.Closed -> {
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.PROVIDER_FAILED,
                    reasonCode = "CONVERSATION_SCHEDULER_CLOSED"
                )
                log("GLM scheduler is closed; request skipped")
            }
        }
    }

    private fun submitVisionFollowUp(incoming: GlmIncomingMessage, resultLogId: Long) {
        val textRevision = admitVisionFollowUp(incoming) ?: return
        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                submitToQueue(
                    incoming = incoming,
                    question = incoming.message.trim(),
                    roomCapabilityRevision = textRevision,
                    visionResultLogId = resultLogId
                )
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.DUPLICATE,
                reasonCode = "DUPLICATE_REQUEST"
            )
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
            }
        }
    }

    private suspend fun processVisionFollowUp(incoming: GlmIncomingMessage, resultLogId: Long) {
        val textRevision = admitVisionFollowUp(incoming) ?: return
        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                executeQuestion(
                    incoming,
                    incoming.message.trim(),
                    textRevision,
                    resultLogId
                )
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.DUPLICATE,
                reasonCode = "DUPLICATE_REQUEST"
            )
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
            }
        }
    }

    private fun admitVisionFollowUp(incoming: GlmIncomingMessage): Long? {
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.TEXT)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "TEXT_CAPABILITY_DISABLED"
            )
            return null
        }
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.IMAGE_ANALYSIS)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "IMAGE_ANALYSIS_CAPABILITY_DISABLED"
            )
            return null
        }
        if (!generalConversationPolicy.allowsUser(incoming.chatId, incoming.userId)) {
            metrics.recordGeneralPolicyDrop()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "VISION_FOLLOW_UP_USER_POLICY_DENIED"
            )
            return null
        }
        val revision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.TEXT)
            ?: return null
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        return revision
    }

    private fun submitGeneralConversation(incoming: GlmIncomingMessage) {
        val snapshot = generalConversationModeStore.snapshotIfEnabled()
        if (snapshot == null) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.MODE_DISABLED,
                reasonCode = "GENERAL_CONVERSATION_OFF"
            )
            return
        }
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.GENERAL_CONVERSATION)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "GENERAL_CAPABILITY_DISABLED"
            )
            return
        }
        val roomCapabilityRevision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.GENERAL_CONVERSATION) ?: return
        if (!generalConversationPolicy.allows(incoming.chatId, incoming.userId)) {
            metrics.recordGeneralPolicyDrop()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "GENERAL_USER_POLICY_DENIED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                metrics.recordGeneralConversationRequest()
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                log("General conversation queued")
                submitToQueue(
                    incoming,
                    incoming.message.trim(),
                    snapshot,
                    roomCapabilityRevision
                )
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                metrics.recordDuplicateDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DUPLICATE,
                    reasonCode = "DUPLICATE_REQUEST"
                )
            }
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
            }
        }
    }

    private suspend fun processGeneralConversation(incoming: GlmIncomingMessage) {
        val snapshot = generalConversationModeStore.snapshotIfEnabled()
        if (snapshot == null) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.MODE_DISABLED,
                reasonCode = "GENERAL_CONVERSATION_OFF"
            )
            return
        }
        if (!roomCapabilityPolicy.allows(incoming.chatId, RoomCapability.GENERAL_CONVERSATION)) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "GENERAL_CAPABILITY_DISABLED"
            )
            return
        }
        val roomCapabilityRevision = roomCapabilityPolicy.snapshot()
            .capabilityRevision(incoming.chatId, RoomCapability.GENERAL_CONVERSATION) ?: return
        if (!generalConversationPolicy.allows(incoming.chatId, incoming.userId)) {
            metrics.recordGeneralPolicyDrop()
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "GENERAL_USER_POLICY_DENIED"
            )
            return
        }
        requestTraceStore.record(incoming.traceId, RequestTraceStage.POLICY_ALLOWED)
        when (val result = admission.admit(incoming)) {
            AdmissionResult.Accepted -> {
                metrics.recordGeneralConversationRequest()
                requestTraceStore.record(incoming.traceId, RequestTraceStage.ADMITTED)
                executeGeneralConversation(
                    incoming,
                    incoming.message.trim(),
                    snapshot,
                    roomCapabilityRevision
                )
            }
            AdmissionResult.DuplicateLog,
            AdmissionResult.DuplicateMessage -> {
                metrics.recordDuplicateDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.DUPLICATE,
                    reasonCode = "DUPLICATE_REQUEST"
                )
            }
            is AdmissionResult.RoomRateLimited,
            is AdmissionResult.UserRateLimited -> {
                metrics.recordRateLimitDrop()
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.RATE_LIMITED,
                    reasonCode = "REQUEST_RATE_LIMIT"
                )
            }
        }
    }

    private suspend fun executeQuestion(
        incoming: GlmIncomingMessage,
        question: String,
        roomCapabilityRevision: Long,
        visionResultLogId: Long? = null
    ) {
        if (!roomCapabilityPolicy.isCurrent(roomCapabilityRevision, incoming.chatId, RoomCapability.TEXT)) return
        memoryReady.await()
        val now = nowMillis()
        val key = ConversationKey(incoming.chatId, incoming.userId)
        val history = memoryStore.history(key, now)
        val visionContext = if (visionResultLogId != null) {
            visionContextResolver.exact(incoming.copy(threadId = visionResultLogId))
        } else {
            visionContextResolver.forConversation(incoming)
        }
        if (visionResultLogId != null && visionContext == null) {
            requestTraceStore.record(
                incoming.traceId,
                RequestTraceStage.POLICY_DENIED,
                reasonCode = "VISION_CONTEXT_UNAVAILABLE"
            )
            safeReply(incoming, "이미지 대화 문맥이 만료되었거나 사용할 수 없어요. 이미지를 다시 분석해주세요.")
            return
        }
        val request = GlmChatRequest(
            model = settings.model,
            messages = buildPrompt(question, history, visionContext),
            temperature = settings.temperature,
            maxTokens = settings.maxTokens
        )

        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.PROVIDER_STARTED,
            engine = conversationEngineModeStore.snapshot().engine.wireValue
        )

        generateWithFailoverAndRetry(request)
            .onSuccess { response ->
                requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_SUCCEEDED)
                val reply = when (val safety = replySafetyPolicy.apply(response.content)) {
                    is ReplySafetyResult.Safe -> {
                        metrics.recordReplyPiiRedactions(safety.redactions.size)
                        requestTraceStore.record(incoming.traceId, RequestTraceStage.SAFETY_PASSED)
                        safety.text
                    }
                    is ReplySafetyResult.Blocked -> {
                        metrics.recordReplySafetyBlock()
                        metrics.recordGlmFailure("ReplySafety", nowMillis())
                        requestTraceStore.record(
                            incoming.traceId,
                            RequestTraceStage.SAFETY_BLOCKED,
                            reasonCode = "REPLY_SAFETY_BLOCKED"
                        )
                        return@onSuccess
                    }
                }

                if (!roomCapabilityPolicy.isCurrent(roomCapabilityRevision, incoming.chatId, RoomCapability.TEXT)) {
                    return@onSuccess
                }
                if (sendTracked(incoming, reply)) {
                    val confirmed = textDeliveryTracker?.awaitConfirmation(incoming.traceId) ?: true
                    if (confirmed) {
                        metrics.recordGlmSuccess(response.latencyMillis, nowMillis())
                        val persisted = memoryStore.append(
                            key,
                            ConversationTurn(
                                userMessage = question,
                                assistantMessage = reply,
                                updatedAtMillis = nowMillis()
                            )
                        )
                        if (!persisted) log("Conversation memory update was not persisted")
                    } else {
                        metrics.recordGlmFailure("ReplyUnconfirmed", nowMillis())
                        log("GLM reply was not confirmed in Kakao DB; memory was not committed")
                    }
                } else {
                    metrics.recordGlmFailure("ReplySend", nowMillis())
                    log("GLM auto-reply could not enqueue a Kakao reply")
                }
            }
            .onFailure {
                metrics.recordGlmFailure(it::class.simpleName ?: "Unknown", nowMillis())
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.PROVIDER_FAILED,
                    reasonCode = traceFailureCode(it)
                )
                log("GLM auto-reply request failed: ${it::class.simpleName}")
            }
    }

    private suspend fun executeGeneralConversation(
        incoming: GlmIncomingMessage,
        message: String,
        modeSnapshot: GeneralConversationModeSnapshot,
        roomCapabilityRevision: Long
    ) {
        if (!generalConversationModeStore.isCurrent(modeSnapshot)) return
        if (!roomCapabilityPolicy.isCurrent(roomCapabilityRevision, incoming.chatId, RoomCapability.GENERAL_CONVERSATION)) return
        memoryReady.await()
        val key = ConversationKey(incoming.chatId, incoming.userId)
        val now = nowMillis()
        val pendingMessages = generalConversationPendingStore.messages(key, now)
        val visionContext = visionContextResolver.forConversation(incoming)
        val request = generalConversationArbiter.buildRequest(
            settings = settings,
            message = message,
            history = memoryStore.history(key, now),
            pendingMessages = pendingMessages,
            visionContext = visionContext
        )

        requestTraceStore.record(
            incoming.traceId,
            RequestTraceStage.PROVIDER_STARTED,
            engine = conversationEngineModeStore.snapshot().engine.wireValue
        )

        var responseResult = generateWithFailoverAndRetry(request)
        if (responseResult.getOrNull()?.finishReason == FINISH_REASON_LENGTH) {
            metrics.recordGeneralConversationTruncationRetry()
            log("General conversation response truncated; retrying concise JSON")
            responseResult = generateWithFailoverAndRetry(
                generalConversationArbiter.buildTruncationRetryRequest(request)
            )
        }

        responseResult
            .onSuccess { response ->
                requestTraceStore.record(incoming.traceId, RequestTraceStage.PROVIDER_SUCCEEDED)
                if (response.finishReason == FINISH_REASON_LENGTH) {
                    metrics.recordGeneralConversationInvalidResponse()
                    metrics.recordGlmFailure("GeneralTruncatedResponse", nowMillis())
                    generalConversationPendingStore.clear(key)
                    log("General conversation decision=invalid reason=truncated")
                    return@onSuccess
                }
                val stillCurrent = synchronized(generalConversationCircuitBreaker) {
                    generalConversationCircuitBreaker.recordSuccess()
                    generalConversationModeStore.isCurrent(modeSnapshot)
                }
                if (!stillCurrent || !roomCapabilityPolicy.isCurrent(
                        roomCapabilityRevision,
                        incoming.chatId,
                        RoomCapability.GENERAL_CONVERSATION
                    )
                ) return@onSuccess
                when (val decision = generalConversationArbiter.parse(response.content)) {
                    is GeneralConversationDecision.Reply -> {
                        log("General conversation decision=reply")
                        val safeReply = when (val safety = replySafetyPolicy.apply(decision.text)) {
                            is ReplySafetyResult.Safe -> {
                                metrics.recordReplyPiiRedactions(safety.redactions.size)
                                requestTraceStore.record(incoming.traceId, RequestTraceStage.SAFETY_PASSED)
                                safety.text
                            }
                            is ReplySafetyResult.Blocked -> {
                                metrics.recordReplySafetyBlock()
                                requestTraceStore.record(
                                    incoming.traceId,
                                    RequestTraceStage.SAFETY_BLOCKED,
                                    reasonCode = "REPLY_SAFETY_BLOCKED"
                                )
                                return@onSuccess
                            }
                        }
                        val sent = runCatching {
                            generalConversationModeStore.dispatchIfCurrent(modeSnapshot) {
                                if (!roomCapabilityPolicy.isCurrent(
                                        roomCapabilityRevision,
                                        incoming.chatId,
                                        RoomCapability.GENERAL_CONVERSATION
                                    )
                                ) return@dispatchIfCurrent false
                                sendTracked(incoming, safeReply)
                            }
                        }.getOrElse {
                            metrics.recordGlmFailure("ReplySend", nowMillis())
                            log("General conversation reply could not enqueue")
                            false
                        }
                        if (sent) {
                            val confirmed = textDeliveryTracker?.awaitConfirmation(incoming.traceId) ?: true
                            if (confirmed) {
                                metrics.recordGeneralConversationReply()
                                metrics.recordGlmSuccess(response.latencyMillis, nowMillis())
                                val persisted = memoryStore.append(
                                    key,
                                    ConversationTurn(
                                        userMessage = buildGeneralConversationUserMessage(
                                            pendingMessages,
                                            message
                                        ),
                                        assistantMessage = safeReply,
                                        updatedAtMillis = nowMillis()
                                    )
                                )
                                if (!persisted) log("Conversation memory update was not persisted")
                                generalConversationPendingStore.clear(key)
                            } else {
                                metrics.recordGlmFailure("ReplyUnconfirmed", nowMillis())
                                log("General conversation reply was not confirmed; memory was not committed")
                            }
                        }
                    }

                    GeneralConversationDecision.Wait -> {
                        metrics.recordGeneralConversationWait()
                        log("General conversation decision=wait")
                        generalConversationModeStore.dispatchIfCurrent(modeSnapshot) {
                            if (!roomCapabilityPolicy.isCurrent(
                                    roomCapabilityRevision,
                                    incoming.chatId,
                                    RoomCapability.GENERAL_CONVERSATION
                                )
                            ) return@dispatchIfCurrent false
                            generalConversationPendingStore.append(key, message, nowMillis())
                            true
                        }
                    }

                    GeneralConversationDecision.Ignore -> {
                        metrics.recordGeneralConversationIgnore()
                        log("General conversation decision=ignore")
                        generalConversationPendingStore.clear(key)
                    }

                    GeneralConversationDecision.Invalid -> {
                        metrics.recordGeneralConversationInvalidResponse()
                        metrics.recordGlmFailure("GeneralInvalidResponse", nowMillis())
                        log("General conversation decision=invalid reason=contract")
                        generalConversationPendingStore.clear(key)
                    }
                }
            }
            .onFailure {
                metrics.recordGlmFailure(it::class.simpleName ?: "Unknown", nowMillis())
                requestTraceStore.record(
                    incoming.traceId,
                    RequestTraceStage.PROVIDER_FAILED,
                    reasonCode = traceFailureCode(it)
                )
                log("General conversation request failed: ${it::class.simpleName}")
                val tripped = synchronized(generalConversationCircuitBreaker) {
                    val didTrip = generalConversationCircuitBreaker.recordFailure(it)
                    if (didTrip) generalConversationModeStore.stop()
                    didTrip
                }
                if (tripped) {
                    generalConversationPendingStore.clearAll()
                    metrics.recordGeneralCircuitTrip()
                    log(
                        "General conversation circuit tripped " +
                            "reason=${generalConversationCircuitBreaker.status().lastReason}"
                    )
                }
            }
    }

    private suspend fun handleLocalCommand(
        incoming: GlmIncomingMessage,
        command: BotCommand
    ) {
        when (command) {
            BotCommand.Help -> {
                HeybotSkillCatalog.userHelpMessages().forEach { safeReply(incoming, it) }
                if (isControlRoomAdmin(incoming)) {
                    HeybotSkillCatalog.adminHelpMessages().forEach { safeReply(incoming, it) }
                }
            }
            BotCommand.ListSkills -> {
                HeybotSkillCatalog.renderAvailable(
                    roomCapabilityPolicy,
                    incoming.chatId,
                    isControlRoomAdmin(incoming)
                ).forEach { safeReply(incoming, it) }
            }
            is BotCommand.ShowSkill -> safeReply(
                incoming,
                HeybotSkillCatalog.renderDetail(
                    command.name,
                    roomCapabilityPolicy,
                    incoming.chatId,
                    isControlRoomAdmin(incoming)
                )
            )
            is BotCommand.RecentDiagnostics -> runAdminCommand(incoming, "recent-diagnostics") {
                val targetRoom = command.roomReference?.let { reference ->
                    roomCapabilityPolicy.snapshot().rooms.firstOrNull {
                        it.reference.equals(reference, ignoreCase = true)
                    }
                }
                if (command.roomReference != null && targetRoom == null) {
                    safeReply(incoming, "방 R번호를 찾지 못했어요. ‘헤이봇 카톡방’으로 확인해주세요.")
                    return@runAdminCommand
                }
                val targetChatId = targetRoom?.chatId ?: incoming.chatId
                val roomReference = targetRoom?.reference
                    ?: roomCapabilityPolicy.snapshot().rooms.firstOrNull { it.chatId == targetChatId }?.reference
                safeReply(
                    incoming,
                    RequestTraceRenderer.render(
                        requestTraceStore.recent(targetChatId, excludeDiagnostics = true),
                        roomReference
                    )
                )
            }
            BotCommand.ClearMyMemory -> {
                val memorySaved = memoryStore.clear(ConversationKey(incoming.chatId, incoming.userId))
                val visionSaved = visionContextStore.clear(incoming.chatId, incoming.userId)
                generalConversationPendingStore.clear(ConversationKey(incoming.chatId, incoming.userId))
                safeReply(
                    incoming,
                    if (memorySaved && visionSaved) "이 방에서 나눈 내 대화와 이미지 분석 기억을 초기화했어요."
                    else "기억은 지웠지만 저장 상태를 확인해주세요."
                )
            }

            BotCommand.Status -> runAdminCommand(incoming, "status") {
                safeReply(incoming, buildStatus())
            }

            is BotCommand.SelfTest -> runAdminCommand(incoming, "self-test-${command.mode.wireName}") {
                safeReply(incoming, selfTestRunner.run(command.mode).render())
            }

            BotCommand.ShowSettings -> runAdminCommand(incoming, "settings") {
                safeReply(incoming, buildSettingsSummary())
            }

            BotCommand.ClearAllMemory -> runAdminCommand(incoming, "clear-all-memory") {
                val memorySaved = memoryStore.clearAll()
                val visionSaved = visionContextStore.clearAll()
                generalConversationPendingStore.clearAll()
                safeReply(
                    incoming,
                    if (memorySaved && visionSaved) "전체 대화와 이미지 분석 기억을 초기화했어요."
                    else "기억은 지웠지만 저장 상태를 확인해주세요."
                )
            }

            is BotCommand.ClearUserMemory -> runAdminCommand(incoming, "clear-user-memory") {
                val memorySaved = memoryStore.clearUser(command.targetUserId)
                val visionSaved = visionContextStore.clearUser(command.targetUserId)
                generalConversationPendingStore.clearUser(command.targetUserId)
                safeReply(
                    incoming,
                    if (memorySaved && visionSaved) "해당 사용자의 대화와 이미지 분석 기억을 초기화했어요."
                    else "기억은 지웠지만 저장 상태를 확인해주세요."
                )
            }

            BotCommand.StartGeneralConversation -> runAdminCommand(incoming, "general-conversation-start") {
                val policy = generalConversationPolicy.status()
                if (!policy.ready) {
                    safeReply(
                        incoming,
                        "일반대화 정책이 준비되지 않아 시작하지 않았어요. 호출어 대화는 계속 사용할 수 있어요."
                    )
                    return@runAdminCommand
                }
                generalConversationCircuitBreaker.reset()
                generalConversationPendingStore.clearAll()
                val status = generalConversationModeStore.start()
                if (!status.enabled) {
                    safeReply(
                        incoming,
                        "일반대화 상태를 저장하지 못해 시작하지 않았어요. 호출어 대화는 계속 사용할 수 있어요."
                    )
                    return@runAdminCommand
                }
                safeReply(
                    incoming,
                    "일반대화 모드를 시작했어요. 허용된 모든 방에서 필요할 때만 답할게요. " +
                        "현재 허용방 ${roomCapabilityPolicy.snapshot().generalConversationRoomCount}개, " +
                        "상태 켜짐, 재시작 복원 ${modePersistenceLabel(status)}."
                )
            }

            BotCommand.GeneralConversationStatus -> runAdminCommand(incoming, "general-conversation-status") {
                val status = generalConversationModeStore.status()
                val policy = generalConversationPolicy.status()
                val circuit = generalConversationCircuitBreaker.status()
                val engine = conversationEngineModeStore.snapshot().engine.displayName
                safeReply(
                    incoming,
                        "일반대화 모드는 현재 ${if (status.enabled) "켜짐" else "꺼짐"}입니다. " +
                        "정책 ${if (policy.ready) "정상" else "비활성"}, " +
                        "적용 대상 ${roomCapabilityPolicy.snapshot().generalConversationRoomCount}개 방, " +
                        "회로 ${if (circuit.tripped) "차단" else "정상"}, " +
                        "최근 사유 ${circuit.lastReason?.name ?: "-"}, " +
                        "상태 저장 ${modePersistenceLabel(status)}, " +
                        "대화 엔진 $engine 입니다."
                )
            }

            is BotCommand.SetConversationEngine -> runAdminCommand(incoming, "conversation-engine-set") {
                val router = gateway as? ConversationGatewayRouter
                if (command.engine != ConversationEngine.GLM && router?.isAvailable(command.engine) != true) {
                    safeReply(incoming, "${command.engine.displayName} 대화 프록시가 준비되지 않아 변경하지 않았어요. 현재 엔진은 ${conversationEngineModeStore.snapshot().engine.displayName}입니다.")
                    return@runAdminCommand
                }
                val snapshot = conversationEngineModeStore.set(command.engine)
                safeReply(incoming, "대화 엔진을 ${snapshot.engine.displayName}(으)로 변경했어요. 모든 허용방의 새 대화부터 적용됩니다.")
            }

            BotCommand.StopGeneralConversation -> runAdminCommand(incoming, "general-conversation-stop") {
                val status = generalConversationModeStore.stop()
                generalConversationCircuitBreaker.reset()
                generalConversationPendingStore.clearAll()
                val persistenceWarning = if (
                    status.persistenceConfigured && status.lastPersistSucceeded == false
                ) {
                    " 현재 프로세스에서는 꺼졌지만 상태 저장에 실패해 파일 상태를 확인해야 해요."
                } else {
                    ""
                }
                safeReply(
                    incoming,
                    "일반대화 모드를 종료했어요. 이제 모든 방에서 다시 ‘헤이봇’ 호출어가 필요해요.$persistenceWarning"
                )
            }

            // `헤이봇 카톡방`은 현재 방 하나가 아니라, 관리자가 R번호를 보고
            // 바로 권한을 바꿀 수 있도록 전체 지원 방 목록을 보여준다.
            BotCommand.ShowCurrentRoom -> safeReply(incoming, roomCapabilityPolicy.renderList())

            BotCommand.ListRoomCapabilities -> runAdminCommand(incoming, "room-capability-list") {
                safeReply(incoming, roomCapabilityPolicy.renderList())
            }

            is BotCommand.ShowRoomCapability -> runAdminCommand(incoming, "room-capability-status") {
                safeReply(incoming, roomCapabilityPolicy.renderStatus(command.reference))
            }

            is BotCommand.PreviewRoomCapability -> runAdminCommand(incoming, "room-capability-preview") {
                safeReply(incoming, renderRoomPolicyResult(
                    roomCapabilityPolicy.preview(
                        incoming.userId,
                        command.reference,
                        command.capability,
                        command.enabled
                    )
                ))
            }

            is BotCommand.ApplyRoomCapability -> runAdminCommand(incoming, "room-capability-apply") {
                safeReply(incoming, renderRoomPolicyResult(
                    roomCapabilityPolicy.apply(incoming.userId, command.nonce)
                ))
            }

            BotCommand.CancelRoomCapability -> runAdminCommand(incoming, "room-capability-cancel") {
                safeReply(incoming, renderRoomPolicyResult(roomCapabilityPolicy.cancel(incoming.userId)))
            }

            is BotCommand.InvalidLocalCommand -> safeReply(incoming, command.reason)
            is BotCommand.AnalyzeImage,
            is BotCommand.GenerateImage,
            BotCommand.ImageStatus,
            BotCommand.CancelImage,
            BotCommand.RetryImage,
            is BotCommand.GenerateVideo,
            BotCommand.VideoStatus,
            BotCommand.CancelVideo,
            BotCommand.RetryVideo -> Unit
            is BotCommand.GeneratePenBrush,
            BotCommand.PenBrushStatus,
            BotCommand.CancelPenBrush,
            BotCommand.RetryPenBrush -> Unit
            is BotCommand.GlmQuestion -> Unit
        }
    }

    private suspend fun runAdminCommand(
        incoming: GlmIncomingMessage,
        commandName: String,
        action: suspend () -> Unit
    ) {
        if (!adminAuthorizer.isAdmin(incoming.userId)) {
            log("Bot admin command=$commandName success=false reason=not_admin")
            safeReply(incoming, "이 명령은 관리자만 사용할 수 있어요.")
            return
        }

        if (incoming.chatId != settings.adminControlChatId) {
            log("Bot admin command=$commandName success=false reason=wrong_room")
            safeReply(incoming, "관리자 설정은 코어라인 AI 연구소에서만 사용할 수 있어요.")
            return
        }

        runCatching { action() }
            .onSuccess {
                log("Bot admin command=$commandName success=true")
            }
            .onFailure {
                log("Bot admin command=$commandName success=false reason=exception")
                safeReply(incoming, "명령 처리 중 문제가 생겼어요.")
            }
    }

    private fun isControlRoomAdmin(incoming: GlmIncomingMessage): Boolean =
        adminAuthorizer.isAdmin(incoming.userId) && incoming.chatId == settings.adminControlChatId

    private fun renderRoomPolicyResult(result: RoomCapabilityMutationResult): String = when (result) {
        is RoomCapabilityMutationResult.PreviewReady ->
            "${result.preview.reference}. ${result.preview.label}\n" +
                "${result.preview.capability.statusName}: ${if (result.preview.enabled) "허용" else "불허용"} 예정\n" +
                "적용: 헤이봇 방 적용 ${result.preview.nonce}\n" +
                "취소: 헤이봇 방 취소"
        is RoomCapabilityMutationResult.Applied ->
            "${result.preview.reference}. ${result.preview.label}의 " +
                "${result.preview.capability.statusName} 권한을 " +
                "${if (result.preview.enabled) "허용" else "불허용"}으로 변경했어요."
        RoomCapabilityMutationResult.Cancelled -> "대기 중인 방 권한 변경을 취소했어요."
        is RoomCapabilityMutationResult.Rejected -> result.reason
    }

    private suspend fun buildStatus(): String {
        val queue = scheduler.snapshot()
        val metric = metrics.snapshot()
        val memory = memoryStore.stats(nowMillis())
        val circuit = generalConversationCircuitBreaker.status()
        val latency = metric.averageLatencyMillis?.let { "${it}ms" } ?: "-"
        val percentiles = if (metric.p50LatencyMillis != null && metric.p95LatencyMillis != null) {
            "${metric.p50LatencyMillis}/${metric.p95LatencyMillis}ms"
        } else {
            "-"
        }
        val persistence = when (memory.lastPersistSucceeded) {
            true -> "정상"
            false -> "확인 필요"
            null -> "메모리"
        }
        val now = nowMillis()
        val vision = visionContextStore.stats(now)
        val lastSuccess = metric.lastSuccessAtMillis?.let { elapsedLabel(now - it) } ?: "-"
        val lastFailure = metric.lastFailureAtMillis?.let {
            "${metric.lastFailureType ?: "Unknown"} ${elapsedLabel(now - it)}"
        } ?: "-"
        val roomQueues = queue.roomPending.entries
            .sortedBy { it.key }
            .joinToString(",") { (chatId, pending) -> "${chatId.toString().takeLast(5)}:$pending" }
            .ifBlank { "-" }
        return (
            "헤이봇 정상 동작 ${elapsedLabel(now - metric.startedAtMillis).removeSuffix(" 전")} | " +
                "GLM ${queue.active}/${queue.maxConcurrency}, 대기 ${queue.totalPending}[$roomQueues] | " +
                "성공 ${metric.glmSuccesses}, 실패 ${metric.glmFailures}, 평균 $latency, " +
                "p50/p95 $percentiles | 429 ${metric.rateLimitedResponses}, " +
                "timeout ${metric.timeoutResponses}, fallback ${metric.fallbackAttempts} | " +
                "최근 성공 $lastSuccess, 실패 $lastFailure | " +
                "중복 ${metric.duplicateDrops}, 제한 ${metric.rateLimitDrops}, " +
                "큐초과 ${metric.queueFullDrops} | 안전차단 ${metric.replySafetyBlocks}, " +
                "마스킹 ${metric.replyPiiRedactions}, 정책제외 ${metric.generalPolicyDrops} | " +
                "일반 요청/답변/대기/무시/오류/재시도 " +
                "${metric.generalConversationRequests}/${metric.generalConversationReplies}/" +
                "${metric.generalConversationWaits}/${metric.generalConversationIgnores}/" +
                "${metric.generalConversationInvalidResponses}/" +
                "${metric.generalConversationTruncationRetries} | " +
                "일반회로 ${if (circuit.tripped) "차단" else "정상"}/" +
                "${metric.generalCircuitTrips}회/${circuit.lastReason?.name ?: "-"} | " +
                "기억 ${memory.conversations}명/${memory.turns}턴, 저장 $persistence | " +
                "이미지문맥 ${vision.contexts}개/${if (vision.ready) "정상" else "차단"}"
            ).take(MAX_REPLY_LENGTH)
    }

    private fun elapsedLabel(elapsedMillis: Long): String {
        val seconds = elapsedMillis.coerceAtLeast(0L) / 1_000L
        return when {
            seconds < 60L -> "${seconds}초 전"
            seconds < 3_600L -> "${seconds / 60L}분 전"
            else -> "${seconds / 3_600L}시간 전"
        }
    }

    private fun buildSettingsSummary(): String =
        generalConversationPolicy.status().let { policy ->
            val circuit = generalConversationCircuitBreaker.status()
            val mode = generalConversationModeStore.status()
            (
            "헤이봇 설정 | 모델 ${settings.model} | 관리방 ${roomCapabilityPolicy.snapshot().rooms.size}개, " +
                "텍스트 ${roomCapabilityPolicy.snapshot().textRoomCount}개, " +
                "이미지 ${roomCapabilityPolicy.snapshot().imageRoomCount}개, " +
                "영상 ${roomCapabilityPolicy.snapshot().videoRoomCount}개, " +
                "펜브러쉬 ${roomCapabilityPolicy.snapshot().penBrushRoomCount}개 | " +
                "이미지분석 ${roomCapabilityPolicy.snapshot().imageAnalysisRoomCount}개 | " +
                "일반대화 ${if (mode.enabled) "켜짐" else "꺼짐"}/저장 ${modePersistenceLabel(mode)} | " +
                "일반정책 ${if (policy.ready) "정상" else "비활성"}/${policy.allowedRoomCount}방 | " +
                "일반회로 ${if (circuit.tripped) "차단" else "정상"}/" +
                "${circuit.lastReason?.name ?: "-"} | " +
                "GLM 동시성 ${settings.maxConcurrency} | 방/전체 큐 " +
                "${settings.roomQueueCapacity}/${settings.totalQueueCapacity} | " +
                "방 ${settings.roomRateWindowMillis / 1000}초 ${settings.roomRateMaxRequests}회 | " +
                "사용자 ${settings.userRateWindowMillis / 1000}초 ${settings.userRateMaxRequests}회 | " +
                "기억 ${settings.memoryMaxTurns}턴/${settings.memoryTtlMillis / 60_000}분 | " +
                "이미지문맥 ${settings.visionContextMaxPerOwner}개/" +
                "${settings.visionContextTtlMillis / 60_000}분/" +
                "공유 ${settings.visionSharedFollowUpWindowMillis / 60_000}분"
            ).take(MAX_REPLY_LENGTH)
        }

    private fun modePersistenceLabel(status: GeneralConversationModeStatus): String = when {
        !status.persistenceConfigured -> "메모리"
        status.lastPersistSucceeded == true -> "정상"
        status.lastPersistSucceeded == false -> "확인 필요"
        else -> "초기 상태"
    }

    private fun safeReply(incoming: GlmIncomingMessage, message: String) {
        if (!sendTracked(incoming, message.take(MAX_REPLY_LENGTH))) {
            log("Local bot reply could not be enqueued")
        }
    }

    private fun sendTracked(incoming: GlmIncomingMessage, message: String): Boolean {
        textDeliveryTracker?.enqueued(
            incoming.traceId,
            incoming.chatId,
            message,
            incoming.threadId
        ) ?: requestTraceStore.record(incoming.traceId, RequestTraceStage.ENQUEUED)
        return runCatching {
            replySender.send(incoming.chatId, message, incoming.threadId)
        }.fold(
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

    private fun traceKind(command: BotCommand): RequestTraceKind = when (command) {
        is BotCommand.GlmQuestion -> RequestTraceKind.WAKE_WORD
        is BotCommand.GenerateImage,
        BotCommand.ImageStatus,
        BotCommand.CancelImage,
        BotCommand.RetryImage -> RequestTraceKind.IMAGE
        is BotCommand.AnalyzeImage -> RequestTraceKind.VISION
        is BotCommand.GenerateVideo,
        BotCommand.VideoStatus,
        BotCommand.CancelVideo,
        BotCommand.RetryVideo -> RequestTraceKind.VIDEO
        is BotCommand.GeneratePenBrush,
        BotCommand.PenBrushStatus,
        BotCommand.CancelPenBrush,
        BotCommand.RetryPenBrush -> RequestTraceKind.PEN_BRUSH
        is BotCommand.RecentDiagnostics -> RequestTraceKind.DIAGNOSTICS
        else -> RequestTraceKind.LOCAL_COMMAND
    }

    private fun traceCapability(command: BotCommand): RoomCapability? = when (command) {
        is BotCommand.GenerateImage,
        BotCommand.ImageStatus,
        BotCommand.CancelImage,
        BotCommand.RetryImage -> RoomCapability.IMAGE
        is BotCommand.GenerateVideo,
        BotCommand.VideoStatus,
        BotCommand.CancelVideo,
        BotCommand.RetryVideo -> RoomCapability.VIDEO
        is BotCommand.GeneratePenBrush,
        BotCommand.PenBrushStatus,
        BotCommand.CancelPenBrush,
        BotCommand.RetryPenBrush -> RoomCapability.PEN_BRUSH
        is BotCommand.AnalyzeImage -> RoomCapability.IMAGE_ANALYSIS
        else -> null
    }

    private fun traceFailureCode(failure: Throwable): String = when (failure) {
        is GlmFailure.Timeout -> "PROVIDER_TIMEOUT"
        is GlmFailure.RateLimited -> "PROVIDER_RATE_LIMITED"
        is GlmFailure.Network -> "PROVIDER_NETWORK"
        is GlmFailure.Proxy -> failure.code
        is GlmFailure.InvalidResponse -> "PROVIDER_INVALID_RESPONSE"
        else -> "PROVIDER_FAILURE"
    }

    private fun isCandidate(incoming: GlmIncomingMessage): Boolean {
        if (incoming.messageType != TEXT_MESSAGE_TYPE) return false
        if (incoming.chatId !in settings.allowedChatIds) return false
        if (botId != 0L && incoming.userId == botId) return false
        return incoming.message.isNotBlank()
    }

    private suspend fun generateWithFailoverAndRetry(request: GlmChatRequest): Result<GlmChatResponse> {
        val firstResult = generateAndRecordExternalSignal(request)
        if (firstResult.isSuccess) return firstResult

        val failure = firstResult.exceptionOrNull()
        val fallbackModel = settings.fallbackModel
        if (
            fallbackModel != null &&
            fallbackModel != request.model &&
            (failure is GlmFailure.RateLimited || failure is GlmFailure.Timeout)
        ) {
            metrics.recordFallbackAttempt()
            log("GLM primary model unavailable; attempting configured fallback")
            return generateWithRateLimitRetry(request.copy(model = fallbackModel))
        }

        return generateWithRateLimitRetry(request, firstResult)
    }

    private suspend fun generateWithRateLimitRetry(
        request: GlmChatRequest,
        initialResult: Result<GlmChatResponse>? = null
    ): Result<GlmChatResponse> {
        var lastResult: Result<GlmChatResponse>? = initialResult
        repeat(settings.rateLimitRetries + 1) { attempt ->
            val result = if (attempt == 0 && initialResult != null) {
                initialResult
            } else {
                generateAndRecordExternalSignal(request)
            }
            if (result.isSuccess) return result
            lastResult = result

            val failure = result.exceptionOrNull()
            if (failure !is GlmFailure.RateLimited || attempt == settings.rateLimitRetries) {
                return result
            }

            val retryDelayMillis = failure.retryAfterMillis
                ?.coerceIn(MIN_RATE_LIMIT_RETRY_DELAY_MILLIS, MAX_RATE_LIMIT_RETRY_DELAY_MILLIS)
                ?: (DEFAULT_RATE_LIMIT_RETRY_DELAY_MILLIS shl attempt)
            log("GLM rate limited; retrying after ${retryDelayMillis}ms")
            delayForRetry(retryDelayMillis)
        }
        return requireNotNull(lastResult)
    }

    private suspend fun generateAndRecordExternalSignal(
        request: GlmChatRequest
    ): Result<GlmChatResponse> {
        val startedAt = nowMillis()
        val result = gateway.generate(request)
        result.exceptionOrNull()?.let { failure ->
            metrics.recordExternalFailure(failure)
            val category = failure::class.simpleName ?: "Unknown"
            val engine = conversationEngineModeStore.snapshot().engine.wireValue
            val proxyCode = (failure as? GlmFailure.Proxy)?.code
            val elapsedMillis = (nowMillis() - startedAt).coerceAtLeast(0L)
            val budgetMillis = request.timeoutMillis ?: settings.timeoutMillis
            log(
                "Conversation external failure engine=$engine " +
                    "kind=${request.kind.metricLabel} category=$category " +
                    "code=${proxyCode ?: "-"} " +
                    "elapsedMs=$elapsedMillis budgetMs=$budgetMillis"
            )
        }
        return result
    }

    private fun buildPrompt(
        question: String,
        history: List<ConversationTurn>,
        visionContext: VisionConversationContext? = null
    ): List<GlmMessage> = buildList {
        add(GlmMessage(role = "system", content = HeybotPersona.wakeWordPrompt()))
        history.forEach { turn ->
            add(GlmMessage(role = "user", content = turn.userMessage))
            add(GlmMessage(role = "assistant", content = turn.assistantMessage))
        }
        visionContext?.let { add(VisionConversationContextRenderer.render(it)) }
        add(GlmMessage(role = "user", content = question))
    }

    private fun buildGeneralConversationUserMessage(
        pendingMessages: List<String>,
        message: String
    ): String = (pendingMessages + message)
        .map(String::trim)
        .filter(String::isNotEmpty)
        .joinToString(separator = "\n")

    private companion object {
        const val TEXT_MESSAGE_TYPE = "1"
        const val FINISH_REASON_LENGTH = "length"
        const val MAX_REPLY_LENGTH = 480
        const val DEFAULT_RATE_LIMIT_RETRY_DELAY_MILLIS = 15_000L
        const val MIN_RATE_LIMIT_RETRY_DELAY_MILLIS = 5_000L
        const val MAX_RATE_LIMIT_RETRY_DELAY_MILLIS = 60_000L
    }
}
