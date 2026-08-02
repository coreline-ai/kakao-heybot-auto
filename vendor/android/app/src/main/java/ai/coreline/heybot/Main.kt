// SendMsg : ye-seola/go-kdb
// Kakaodecrypt : jiru/kakaodecrypt
package ai.coreline.heybot

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

const val IMAGE_DIR_PATH: String = "/sdcard/Android/data/com.kakao.talk/files"

class Main {
    companion object {
        @JvmStatic
        fun main(args: Array<String>) {
            try {
                if (args.firstOrNull() == SELF_TEST_ARGUMENT) {
                    val mode = SelfTestMode.parse(args.getOrNull(1))
                    if (mode == null) {
                        println("SELF_TEST_MODE_INVALID")
                        return
                    }
                    val report = runBlocking {
                        SelfTestRunner.production().run(mode)
                    }
                    println(Json.encodeToString(report))
                    return
                }
                val wsEventFlow = MutableSharedFlow<String>()

                val notificationReferer = readNotificationReferer()

                Replier.startMessageSender()
                println("Message sender thread started")

                val kakaoDb = KakaoDB()
                val roomCapabilityPolicy = createRoomCapabilityPolicy()
                val selfTestRunner = SelfTestRunner.production()
                val requestTraceStore = RequestTraceStore(
                    backend = AndroidAtomicFileBackend(
                        File(
                            System.getenv("IRIS_REQUEST_TRACE_FILE")
                                ?: "/data/local/private/iris-request-traces.json"
                        )
                    )
                )
                val textDeliveryTracker = TextDeliveryTracker(
                    botId = Configurable.botId,
                    traces = requestTraceStore
                )
                val glmAutoReplyHandler = createGlmAutoReplyHandler(
                    notificationReferer,
                    roomCapabilityPolicy,
                    selfTestRunner,
                    requestTraceStore,
                    textDeliveryTracker
                )
                val imageJobCoordinator = createImageJobCoordinator(
                    notificationReferer,
                    roomCapabilityPolicy,
                    requestTraceStore,
                    textDeliveryTracker
                )
                val videoJobCoordinator = createVideoJobCoordinator(
                    notificationReferer,
                    roomCapabilityPolicy,
                    requestTraceStore,
                    textDeliveryTracker
                )
                val penBrushJobCoordinator = createPenBrushJobCoordinator(
                    notificationReferer,
                    roomCapabilityPolicy,
                    requestTraceStore,
                    textDeliveryTracker
                )
                val imageAnalysisCoordinator = createImageAnalysisCoordinator(
                    kakaoDb,
                    notificationReferer,
                    roomCapabilityPolicy,
                    requestTraceStore,
                    textDeliveryTracker
                )
                val observerHelper = ObserverHelper(
                    kakaoDb,
                    wsEventFlow,
                    glmAutoReplyHandler,
                    imageJobCoordinator,
                    videoJobCoordinator,
                    penBrushJobCoordinator,
                    imageAnalysisCoordinator,
                    textDeliveryTracker = textDeliveryTracker,
                    requestTraceStore = requestTraceStore
                )

                val dbObserver = DBObserver(kakaoDb, observerHelper)
                dbObserver.startPolling()
                println("DBObserver started")

                val notificationPoller = NotificationPoller()
                notificationPoller.startPolling()
                println("Notification Poller started")

                val imageDeleter = ImageDeleter(IMAGE_DIR_PATH, TimeUnit.HOURS.toMillis(1))
                imageDeleter.startDeletion()
                println("ImageDeleter started, and will delete images older than 1 hour.")

                when (val httpSecurity = IrisHttpSecuritySettings.load()) {
                    IrisHttpSecuritySettingsLoadResult.Disabled -> {
                        println("Iris HTTP API disabled")
                    }

                    is IrisHttpSecuritySettingsLoadResult.Invalid -> {
                        System.err.println("Iris HTTP API disabled: ${httpSecurity.code}")
                    }

                    is IrisHttpSecuritySettingsLoadResult.Ready -> {
                        IrisServer(
                            dbObserver,
                            observerHelper,
                            notificationReferer,
                            wsEventFlow,
                            httpSecurity.settings,
                            selfTestRunner
                        ).startServer()
                        println("Iris HTTP API started on loopback")
                    }
                }

                // app_process terminates when this entry point returns, even
                // though Iris has scheduled/background workers. Keep the
                // process owner alive until the deployment script stops it.
                println("Iris process lifetime ready")
                CountDownLatch(1).await()
            } catch (e: Exception) {
                System.err.println("Iris Error")
            }
        }

        private const val SELF_TEST_ARGUMENT = "--self-test"

        private fun readNotificationReferer(): String {
            val appPath = PathUtils.getAppPath()
            val prefsFile = File("${appPath}shared_prefs/KakaoTalk.hw.perferences.xml")
            val data = prefsFile.bufferedReader().use {
                it.readText()
            }
            val regex = Regex("""<string name="NotificationReferer">(.*?)</string>""")
            val match = regex.find(data) ?: throw Exception("failed to extract referer from data")

            val referer =
                match.groups[1]?.value ?: throw Exception("failed to extract referer from data")

            return referer
        }

        private fun createRoomCapabilityPolicy(): RoomCapabilityPolicyStore = when (
            val config = GlmSettings.load()
        ) {
            is GlmSettingsLoadResult.Ready -> RoomCapabilityPolicyStore.load(
                settings = config.settings.roomCapabilities,
                managedChatIds = config.settings.allowedChatIds,
                controlChatId = config.settings.adminControlChatId
            ).also { policy ->
                val snapshot = policy.snapshot()
                println(
                    "Room capability policy ready=${snapshot.ready} " +
                        "rooms=${snapshot.rooms.size} revision=${snapshot.revision}"
                )
            }
            else -> RoomCapabilityPolicyStore.legacy(emptySet())
        }

        private fun createGlmAutoReplyHandler(
            notificationReferer: String,
            roomCapabilityPolicy: RoomCapabilityPolicyStore,
            selfTestRunner: SelfTestRunner,
            requestTraceStore: RequestTraceStore,
            textDeliveryTracker: TextDeliveryTracker
        ): GlmAutoReplyHandler? {
            return when (val config = GlmSettings.load()) {
                GlmSettingsLoadResult.Disabled -> {
                    println("GLM auto-reply disabled")
                    null
                }

                is GlmSettingsLoadResult.Invalid -> {
                    System.err.println("GLM auto-reply disabled: ${config.reason}")
                    null
                }

                is GlmSettingsLoadResult.Ready -> {
                    println("GLM auto-reply enabled")
                    val settings = config.settings
                    val proxyConfig = ConversationProxySettings.load()
                    val modeStore = ConversationEngineModeStore(
                        file = when (proxyConfig) {
                            is ConversationProxySettingsLoadResult.Ready -> proxyConfig.settings.modeFile
                            else -> File(ConversationProxySettings.DEFAULT_MODE_FILE)
                        }
                    )
                    val glmGateway = GlmClient(settings)
                    val codexGateway = (proxyConfig as? ConversationProxySettingsLoadResult.Ready)
                        ?.let { ConversationProxyClient(it.settings, ConversationEngine.CODEX) }
                    val grokGateway = (proxyConfig as? ConversationProxySettingsLoadResult.Ready)
                        ?.let { ConversationProxyClient(it.settings, ConversationEngine.GROK) }
                    val conversationGateway = ConversationGatewayRouter(
                        modeStore = modeStore,
                        glm = glmGateway,
                        codex = codexGateway,
                        grok = grokGateway
                    )
                    println(
                        "Conversation engine ready=${modeStore.snapshot().engine.displayName} " +
                            "proxy=${if (proxyConfig is ConversationProxySettingsLoadResult.Ready) "configured" else "disabled"}"
                    )
                    val generalConversationPolicy =
                        GeneralConversationPolicy.load(settings.generalConversation)
                    val policyStatus = generalConversationPolicy.status()
                    println(
                        "General conversation policy " +
                            "ready=${policyStatus.ready} " +
                            "rooms=${policyStatus.allowedRoomCount} " +
                            "reason=${policyStatus.reason}"
                    )
                    val generalConversationModeStore = settings.generalConversation?.let {
                        GeneralConversationModeStore(
                            backend = AndroidAtomicFileBackend(it.modeFile)
                        )
                    } ?: GeneralConversationModeStore()
                    val generalModeStatus = generalConversationModeStore.status()
                    println(
                        "General conversation mode " +
                            "restored=${generalModeStatus.enabled} " +
                            "persistence=${if (generalModeStatus.persistenceConfigured) "configured" else "memory"} " +
                            "healthy=${generalModeStatus.lastPersistSucceeded ?: "initial"}"
                    )
                    GlmAutoReplyHandler(
                        settings = settings,
                        botId = Configurable.botId,
                        gateway = conversationGateway,
                        replySender = GlmReplySender { chatId, message, threadId ->
                            Replier.sendMessage(
                                notificationReferer,
                                chatId,
                                message,
                                threadId
                            ) { result ->
                                textDeliveryTracker.dispatched(chatId, message, result)
                            }
                        },
                        memoryStore = AtomicJsonConversationMemoryStore(
                            backend = AndroidAtomicFileBackend(settings.memoryFile),
                            maxTurnsPerConversation = settings.memoryMaxTurns,
                            ttlMillis = settings.memoryTtlMillis,
                            maxBytes = settings.memoryMaxBytes,
                            maxConversations = settings.memoryMaxConversations
                        ),
                        adminAuthorizer = AdminAuthorizer.fromFile(settings.adminUserIdsFile),
                        generalConversationPolicy = generalConversationPolicy,
                        generalConversationModeStore = generalConversationModeStore,
                        roomCapabilityPolicy = roomCapabilityPolicy,
                        conversationEngineModeStore = modeStore,
                        selfTestRunner = selfTestRunner,
                        requestTraceStore = requestTraceStore,
                        textDeliveryTracker = textDeliveryTracker
                    )
                }
            }
        }

        private fun createImageJobCoordinator(
            notificationReferer: String,
            roomCapabilityPolicy: RoomCapabilityPolicyStore,
            requestTraceStore: RequestTraceStore,
            textDeliveryTracker: TextDeliveryTracker
        ): ImageJobCoordinator? {
            return when (val config = ImageProxySettings.load()) {
                ImageProxySettingsLoadResult.Disabled -> {
                    println("Image proxy disabled")
                    null
                }

                is ImageProxySettingsLoadResult.Invalid -> {
                    System.err.println("Image proxy disabled: ${config.reason}")
                    null
                }

                is ImageProxySettingsLoadResult.Ready -> {
                    val settings = config.settings
                    println("Image proxy enabled")
                    ImageJobCoordinator(
                        settings = settings,
                        trigger = System.getenv()["IRIS_GLM_TRIGGER"]?.trim()
                            ?.takeIf { it.isNotBlank() } ?: "헤이봇",
                        botId = Configurable.botId,
                        gateway = ImageProxyClient(settings),
                        textSender = ImageTextReplySender { chatId, message, threadId ->
                            Replier.sendMessage(notificationReferer, chatId, message, threadId) {
                                textDeliveryTracker.dispatched(chatId, message, it)
                            }
                        },
                        imageSender = ImageBytesReplySender { chatId, bytes ->
                            Replier.sendPhotoBytes(chatId, bytes)
                        },
                        stateStore = AtomicJsonImageJobStateStore(
                            AndroidAtomicFileBackend(settings.stateFile)
                        ),
                        roomCapabilityPolicy = roomCapabilityPolicy,
                        requestTraceStore = requestTraceStore,
                        textDeliveryTracker = textDeliveryTracker
                    )
                }
            }
        }

        private fun createVideoJobCoordinator(
            notificationReferer: String,
            roomCapabilityPolicy: RoomCapabilityPolicyStore,
            requestTraceStore: RequestTraceStore,
            textDeliveryTracker: TextDeliveryTracker
        ): VideoJobCoordinator? {
            return when (val config = VideoProxySettings.load()) {
                VideoProxySettingsLoadResult.Disabled -> {
                    println("Video proxy disabled")
                    null
                }

                is VideoProxySettingsLoadResult.Invalid -> {
                    System.err.println("Video proxy disabled: ${config.reason}")
                    null
                }

                is VideoProxySettingsLoadResult.Ready -> {
                    val settings = config.settings
                    println("Video proxy enabled")
                    VideoJobCoordinator(
                        settings = settings,
                        trigger = System.getenv()["IRIS_GLM_TRIGGER"]?.trim()
                            ?.takeIf { it.isNotBlank() } ?: "헤이봇",
                        botId = Configurable.botId,
                        gateway = VideoProxyClient(settings),
                        textSender = VideoTextReplySender { chatId, message, threadId ->
                            Replier.sendMessage(notificationReferer, chatId, message, threadId) {
                                textDeliveryTracker.dispatched(chatId, message, it)
                            }
                        },
                        videoSender = VideoBytesReplySender { chatId, bytes ->
                            Replier.sendVideoBytes(chatId, bytes)
                        },
                        stateStore = AtomicJsonVideoJobStateStore(
                            AndroidAtomicFileBackend(settings.stateFile)
                        ),
                        roomCapabilityPolicy = roomCapabilityPolicy,
                        requestTraceStore = requestTraceStore,
                        textDeliveryTracker = textDeliveryTracker
                    )
                }
            }
        }

        private fun createImageAnalysisCoordinator(
            kakaoDb: KakaoDB,
            notificationReferer: String,
            roomCapabilityPolicy: RoomCapabilityPolicyStore,
            requestTraceStore: RequestTraceStore,
            textDeliveryTracker: TextDeliveryTracker
        ): ImageAnalysisCoordinator? {
            return when (val config = ImageAnalysisSettings.load()) {
                ImageAnalysisSettingsLoadResult.Disabled -> {
                    println("Vision proxy disabled")
                    null
                }
                is ImageAnalysisSettingsLoadResult.Invalid -> {
                    System.err.println("Vision proxy disabled: ${config.reason}")
                    null
                }
                is ImageAnalysisSettingsLoadResult.Ready -> {
                    val settings = config.settings
                    println("Vision proxy enabled")
                    ImageAnalysisCoordinator(
                        settings = settings,
                        trigger = System.getenv()["IRIS_GLM_TRIGGER"]?.trim()
                            ?.takeIf { it.isNotBlank() } ?: "헤이봇",
                        botId = Configurable.botId,
                        gateway = ImageAnalysisProxyClient(settings),
                        replySender = ImageAnalysisReplySender { chatId, message, threadId ->
                            Replier.sendMessage(notificationReferer, chatId, message, threadId) {
                                textDeliveryTracker.dispatched(chatId, message, it)
                            }
                        },
                        roomCapabilityPolicy = roomCapabilityPolicy,
                        attachmentLookup = KakaoDbImageAttachmentLookup(
                            source = KakaoDbImageLogSource(kakaoDb),
                            parser = KakaoImageAttachmentParser()
                        ),
                        requestTraceStore = requestTraceStore,
                        textDeliveryTracker = textDeliveryTracker
                    )
                }
            }
        }

        private fun createPenBrushJobCoordinator(
            notificationReferer: String,
            roomCapabilityPolicy: RoomCapabilityPolicyStore,
            requestTraceStore: RequestTraceStore,
            textDeliveryTracker: TextDeliveryTracker
        ): PenBrushJobCoordinator? {
            return when (val config = PenBrushProxySettings.load()) {
                PenBrushProxySettingsLoadResult.Disabled -> {
                    println("Pen-brush proxy disabled")
                    null
                }

                is PenBrushProxySettingsLoadResult.Invalid -> {
                    System.err.println("Pen-brush proxy disabled: ${config.reason}")
                    null
                }

                is PenBrushProxySettingsLoadResult.Ready -> {
                    val settings = config.settings
                    println("Pen-brush proxy enabled")
                    PenBrushJobCoordinator(
                        settings = settings,
                        trigger = System.getenv()["IRIS_GLM_TRIGGER"]?.trim()
                            ?.takeIf { it.isNotBlank() } ?: "헤이봇",
                        botId = Configurable.botId,
                        gateway = PenBrushProxyClient(settings),
                        textSender = PenBrushTextReplySender { chatId, message, threadId ->
                            Replier.sendMessage(notificationReferer, chatId, message, threadId) {
                                textDeliveryTracker.dispatched(chatId, message, it)
                            }
                        },
                        videoSender = PenBrushBytesReplySender { chatId, bytes ->
                            Replier.sendVideoBytes(chatId, bytes)
                        },
                        stateStore = AtomicJsonPenBrushJobStateStore(
                            AndroidAtomicFileBackend(settings.stateFile)
                        ),
                        roomCapabilityPolicy = roomCapabilityPolicy,
                        requestTraceStore = requestTraceStore,
                        textDeliveryTracker = textDeliveryTracker
                    )
                }
            }
        }
    }
}
