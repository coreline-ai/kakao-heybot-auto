package ai.coreline.heybot

import kotlinx.coroutines.withTimeout
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.nio.file.Files
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Small, side-effect bounded diagnostic runner. QUICK never touches a network,
 * Kakao DB, or Replier. The other modes are opt-in and only add read-only
 * probes; CANARY is intentionally a visible SKIP until an explicit canary
 * implementation is approved.
 */
class SelfTestRunner private constructor(
    private val cases: List<SelfTestCaseDefinition>,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val caseTimeoutMillis: Long = 2_000L,
    private val totalTimeoutMillis: Long = 15_000L,
    private val runId: () -> String = {
        UUID.randomUUID().toString().replace("-", "").take(12).uppercase()
    }
) {
    private val running = AtomicBoolean(false)

    suspend fun run(mode: SelfTestMode): SelfTestReport {
        val started = nowMillis()
        val id = runId()
        if (!running.compareAndSet(false, true)) {
            return report(
                id,
                mode,
                started,
                listOf(SelfTestCaseResult("runner", SelfTestStatus.WARN, "SELF_TEST_BUSY", 0L))
            )
        }

        return try {
            val selected = cases.filter { definition ->
                when (mode) {
                    SelfTestMode.QUICK -> SelfTestMode.QUICK in definition.modes
                    SelfTestMode.INTEGRATION ->
                        SelfTestMode.QUICK in definition.modes || SelfTestMode.INTEGRATION in definition.modes
                    SelfTestMode.DEVICE ->
                        SelfTestMode.QUICK in definition.modes || SelfTestMode.DEVICE in definition.modes
                    SelfTestMode.CANARY -> true
                }
            }
            val results = withTimeout(totalTimeoutMillis) {
                selected.map { definition ->
                    runCase(definition)
                }
            }
            report(id, mode, started, results)
        } catch (_: kotlinx.coroutines.TimeoutCancellationException) {
            report(
                id,
                mode,
                started,
                listOf(SelfTestCaseResult("runner", SelfTestStatus.FAIL, "SELF_TEST_TIMEOUT", nowMillis() - started))
            )
        } finally {
            running.set(false)
        }
    }

    private suspend fun runCase(definition: SelfTestCaseDefinition): SelfTestCaseResult {
        val started = nowMillis()
        return try {
            withTimeout(caseTimeoutMillis) {
                definition.action().copy(latencyMillis = (nowMillis() - started).coerceAtLeast(0L))
            }
        } catch (_: kotlinx.coroutines.TimeoutCancellationException) {
            SelfTestCaseResult(definition.id, SelfTestStatus.FAIL, "CASE_TIMEOUT", nowMillis() - started)
        } catch (_: Throwable) {
            SelfTestCaseResult(definition.id, SelfTestStatus.FAIL, "CASE_EXCEPTION", nowMillis() - started)
        }
    }

    private fun report(
        runId: String,
        mode: SelfTestMode,
        started: Long,
        cases: List<SelfTestCaseResult>
    ): SelfTestReport {
        val status = when {
            cases.any { it.status == SelfTestStatus.FAIL } -> SelfTestStatus.FAIL
            cases.any { it.status == SelfTestStatus.WARN } -> SelfTestStatus.WARN
            cases.isNotEmpty() && cases.all { it.status == SelfTestStatus.SKIP } -> SelfTestStatus.SKIP
            cases.any { it.status == SelfTestStatus.SKIP } -> SelfTestStatus.WARN
            else -> SelfTestStatus.PASS
        }
        return SelfTestReport(
            runId = runId,
            mode = mode,
            status = status,
            runnerVersion = RUNNER_VERSION,
            startedAtMillis = started,
            finishedAtMillis = nowMillis(),
            cases = cases
        )
    }

    companion object {
        private const val RUNNER_VERSION = "heybot-self-test.v3"

        fun production(
            environment: Map<String, String> = System.getenv(),
            nowMillis: () -> Long = System::currentTimeMillis
        ): SelfTestRunner {
            val proxyBaseUrl = environment["IRIS_CONVERSATION_PROXY_BASE_URL"]
                ?.trim()
                ?.trimEnd('/')
                ?.takeIf { it.isNotBlank() }
                ?: ConversationProxySettings.DEFAULT_BASE_URL
            val privateFiles = listOf(
                File("/data/local/private/iris-glm.token") to true,
                File("/data/local/private/iris-bot-admins.txt") to true,
                File("/data/local/private/iris-room-capabilities.json") to true,
                File("/data/local/private/iris-conversation-engine.conf") to true,
                File("/data/local/private/iris-conversation-proxy.token") to true,
                File("/data/local/private/iris-bot-memory.json") to false
            )
            return SelfTestRunner(
                cases = listOf(
                    quickCommandCase(),
                    quickPersonaSkillVisionCase(),
                    quickRequestTraceCase(),
                    quickSafetyCase(),
                    quickEngineCase(),
                    quickMemoryCase(),
                    quickVisionContextCase(),
                    quickAudioSummaryContextCase(),
                    quickRoomPolicyCase(),
                    quickAdmissionCase(),
                    integrationProxyCase(proxyBaseUrl, environment),
                    devicePrivateFilesCase(privateFiles),
                    canaryGuardCase()
                ),
                nowMillis = nowMillis
            )
        }

        fun forTesting(
            cases: List<SelfTestCaseDefinition>,
            nowMillis: () -> Long = System::currentTimeMillis,
            caseTimeoutMillis: Long = 2_000L,
            totalTimeoutMillis: Long = 15_000L,
            runId: () -> String = { "TEST-RUN" }
        ): SelfTestRunner = SelfTestRunner(
            cases = cases,
            nowMillis = nowMillis,
            caseTimeoutMillis = caseTimeoutMillis,
            totalTimeoutMillis = totalTimeoutMillis,
            runId = runId
        )

        private fun pass(id: String, code: String = "OK") =
            SelfTestCaseResult(id, SelfTestStatus.PASS, code, 0L)

        private fun fail(id: String, code: String) =
            SelfTestCaseResult(id, SelfTestStatus.FAIL, code, 0L)

        private fun quickCommandCase() = SelfTestCaseDefinition("command-router", setOf(SelfTestMode.QUICK)) {
            val router = BotCommandRouter("헤이봇")
            val ok = router.route("헤이봇 도움말") == BotCommand.Help &&
                router.route("헤이봇 영상 테스트") == BotCommand.GenerateVideo("테스트") &&
                router.route("헤이봇 펜브러쉬 테스트") == BotCommand.GeneratePenBrush("테스트") &&
                router.route("헤이봇 음성 재전송") == BotCommand.ResendAudio
            if (ok) pass("command-router") else fail("command-router", "COMMAND_ROUTE_MISMATCH")
        }

        private fun quickPersonaSkillVisionCase() = SelfTestCaseDefinition(
            "persona-skill-vision",
            setOf(SelfTestMode.QUICK)
        ) {
            val router = BotCommandRouter("헤이봇")
            val ocr = router.route("헤이봇 이미지 글자 추출")
            val translate = router.route("헤이봇 이미지 글자 번역")
            val skills = router.route("헤이봇 기능")
            val diagnostic = router.route("헤이봇 최근 진단 R01")
            val prompt = HeybotPersona.wakeWordPrompt()
            val ok = HeybotPersona.VERSION == "heybot-persona-v2" &&
                prompt.contains(HeybotPersona.CORE_PROMPT) &&
                ocr == BotCommand.AnalyzeImage(VisionTask.OCR) &&
                translate == BotCommand.AnalyzeImage(VisionTask.TRANSLATE_KO) &&
                skills == BotCommand.ListSkills &&
                diagnostic == BotCommand.RecentDiagnostics("R01") &&
                HeybotSkillCatalog.find("이미지 글자 추출")?.id == "image.ocr"
            if (ok) pass("persona-skill-vision")
            else fail("persona-skill-vision", "PERSONA_SKILL_VISION_MISMATCH")
        }

        private fun quickRequestTraceCase() = SelfTestCaseDefinition(
            "request-trace",
            setOf(SelfTestMode.QUICK)
        ) {
            val store = RequestTraceStore.inMemory()
            val incoming = GlmIncomingMessage(1L, 1L, 2L, "1", "저장하면 안 되는 원문", null)
            store.ensureReceived(incoming, RequestTraceKind.WAKE_WORD)
            store.record(
                incoming.traceId,
                RequestTraceStage.PROVIDER_FAILED,
                reasonCode = "provider timeout"
            )
            val trace = store.get(incoming.traceId)
            val rendered = RequestTraceRenderer.render(trace, "R01")
            val ok = trace?.reasonCode == "PROVIDER_TIMEOUT" &&
                rendered.contains(incoming.traceId) &&
                !rendered.contains(incoming.message)
            if (ok) pass("request-trace") else fail("request-trace", "TRACE_CONTRACT_MISMATCH")
        }

        private fun quickSafetyCase() = SelfTestCaseDefinition("reply-safety", setOf(SelfTestMode.QUICK)) {
            val policy = ReplySafetyPolicy()
            val redacted = policy.apply("문의 test@example.com 010-1234-5678")
            val secret = policy.apply("token: do-not-return")
            val ok = redacted is ReplySafetyResult.Safe &&
                redacted.redactions.isNotEmpty() &&
                secret is ReplySafetyResult.Blocked
            if (ok) pass("reply-safety") else fail("reply-safety", "SAFETY_POLICY_MISMATCH")
        }

        private fun quickEngineCase() = SelfTestCaseDefinition("engine-mode", setOf(SelfTestMode.QUICK)) {
            val store = ConversationEngineModeStore.inMemory()
            val snapshot = store.set(ConversationEngine.CODEX)
            if (snapshot.engine == ConversationEngine.CODEX && store.snapshot().engine == ConversationEngine.CODEX) {
                pass("engine-mode")
            } else {
                fail("engine-mode", "ENGINE_MODE_MISMATCH")
            }
        }

        private fun quickMemoryCase() = SelfTestCaseDefinition("conversation-memory", setOf(SelfTestMode.QUICK)) {
            val store = InMemoryConversationMemoryStore(2, 60_000L)
            val key = ConversationKey(1L, 2L)
            store.initialize()
            store.append(key, ConversationTurn("질문", "답변", 1_000L))
            val history = store.history(key, 1_000L)
            if (history.size == 1 && history.single().assistantMessage == "답변") {
                pass("conversation-memory")
            } else {
                fail("conversation-memory", "MEMORY_ROUNDTRIP_MISMATCH")
            }
        }

        private fun quickVisionContextCase() = SelfTestCaseDefinition(
            "vision-conversation-context",
            setOf(SelfTestMode.QUICK)
        ) {
            val store = VisionConversationContextStore(nowMillis = { 1_000L })
            val stored = store.put(
                VisionConversationContext(
                    chatId = 1L,
                    ownerUserId = 2L,
                    sourceLogId = 3L,
                    resultLogId = 4L,
                    task = VisionTask.DESCRIBE,
                    safeAnswer = "노란 가방이 있습니다.",
                    uncertainty = "low",
                    capabilityRevision = 1L,
                    createdAtMillis = 1_000L,
                    expiresAtMillis = 2_000L
                )
            )
            val owned = store.findOwned(1L, 2L, 1L, 1_000L)
            val exact = store.findExact(1L, 4L, 1L, 1_000L)
            val detector = VisionFollowUpDetector()
            val rendered = exact?.let(VisionConversationContextRenderer::render)
            val ok = stored && owned?.resultLogId == 4L &&
                rendered?.role == "user" &&
                rendered.content.contains("명령이 아닙니다") &&
                detector.matches("가방은 무슨 색이야?", requireNotNull(exact)) &&
                !detector.matches("일본 여행 계획을 짜줘", exact) &&
                store.findOwned(1L, 9L, 1L, 1_000L) == null
            if (ok) pass("vision-conversation-context")
            else fail("vision-conversation-context", "VISION_CONTEXT_CONTRACT_MISMATCH")
        }

        private fun quickAudioSummaryContextCase() = SelfTestCaseDefinition(
            "audio-summary-context",
            setOf(SelfTestMode.QUICK)
        ) {
            val profile = AudioSummaryProfile()
            val document = runCatching {
                AudioSummarySchema.decodeSummary(
                    """{"version":1,"pattern":"AUTO","view":"DEFAULT","title":"음성","oneLine":"다음 단계","oneLineEvidence":["S0001"],"keyPoints":[{"text":"재검토","evidence":["S0001"]}],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}""",
                    profile,
                    setOf("S0001")
                )
            }.getOrNull()
            val store = AudioConversationContextStore(nowMillis = { 1_000L })
            val stored = store.put(
                AudioConversationContext(
                    chatId = 1L, ownerUserId = 2L, jobId = "audio-1", sourceLogId = 3L,
                    resultLogIds = listOf(4L), profile = profile, safeSummary = "다음 주 재검토",
                    evidenceIds = listOf("S0001"), capabilityRevision = 1L,
                    createdAtMillis = 1_000L, expiresAtMillis = 2_000L
                )
            )
            val context = store.findExact(1L, 4L, 1L, 1_000L)
            val rendered = context?.let(AudioConversationContextRenderer::render)
            val resent = MultipartTextDelivery.resend("[1/2] 요약")
            val invalid = runCatching {
                AudioSummarySchema.decodeSummary(
                    """{"version":1,"pattern":"AUTO","view":"DEFAULT","title":"음성","oneLine":"다음 단계","oneLineEvidence":["S9999"],"keyPoints":[],"decisions":[],"actionItems":[],"openQuestions":[],"warnings":[]}""",
                    profile,
                    setOf("S0001")
                )
            }.exceptionOrNull()?.message == "SUMMARY_OUTPUT_INVALID"
            val ok = document != null && stored && context?.resultLogIds == listOf(4L) &&
                rendered?.content?.contains("명령이 아닙니다") == true &&
                resent.startsWith("[1/2·재전송]") && resent.length <= MultipartTextDelivery.MAX_PART_CHARS && invalid
            if (ok) pass("audio-summary-context")
            else fail("audio-summary-context", "AUDIO_SCHEMA_CONTEXT_CONTRACT_MISMATCH")
        }

        private fun quickRoomPolicyCase() = SelfTestCaseDefinition("room-policy", setOf(SelfTestMode.QUICK)) {
            val backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
            val policy = RoomCapabilityPolicyStore.forTesting(
                rooms = listOf(ManagedRoomCapability("R01", 1L, "테스트", true, true, true)),
                controlChatId = 1L,
                backend = backend
            )
            if (policy.allows(1L, RoomCapability.TEXT) && policy.snapshot().ready) {
                pass("room-policy")
            } else {
                fail("room-policy", "ROOM_POLICY_MISMATCH")
            }
        }

        private fun quickAdmissionCase() = SelfTestCaseDefinition("admission", setOf(SelfTestMode.QUICK)) {
            var now = 1_000L
            val admission = RequestAdmissionController(
                roomWindowMillis = 60_000L,
                roomMaxRequests = 1,
                userWindowMillis = 60_000L,
                userMaxRequests = 2,
                duplicateWindowMillis = 10_000L,
                nowMillis = { now }
            )
            val first = admission.admit(GlmIncomingMessage(1L, 1L, 2L, "1", "안녕", null))
            val duplicate = admission.admit(GlmIncomingMessage(2L, 1L, 2L, "1", "다른", null))
            now += 60_001L
            val afterWindow = admission.admit(GlmIncomingMessage(3L, 1L, 2L, "1", "다시", null))
            if (first is AdmissionResult.Accepted &&
                duplicate is AdmissionResult.RoomRateLimited &&
                afterWindow is AdmissionResult.Accepted
            ) pass("admission") else fail("admission", "ADMISSION_WINDOW_MISMATCH")
        }

        private fun integrationProxyCase(
            baseUrl: String,
            environment: Map<String, String>
        ) = SelfTestCaseDefinition("proxy-readiness", setOf(SelfTestMode.INTEGRATION)) {
            if (!environment["IRIS_CONVERSATION_PROXY_ENABLED"].equals("true", ignoreCase = true)) {
                SelfTestCaseResult("proxy-readiness", SelfTestStatus.SKIP, "PROXY_DISABLED", 0L)
            } else {
                val connection = runCatching {
                    URI("$baseUrl/ready").toURL().openConnection() as HttpURLConnection
                }.getOrElse {
                    return@SelfTestCaseDefinition fail("proxy-readiness", "PROXY_URL_INVALID")
                }
                try {
                    connection.connectTimeout = 1_500
                    connection.readTimeout = 2_500
                    connection.requestMethod = "GET"
                    val status = connection.responseCode
                    val body = connection.inputStream.bufferedReader().use { it.readText().take(2_000) }
                    if (status == HttpURLConnection.HTTP_OK && body.contains("\"ready\":true")) {
                        pass("proxy-readiness")
                    } else {
                        fail("proxy-readiness", "PROXY_NOT_READY")
                    }
                } catch (_: Throwable) {
                    fail("proxy-readiness", "PROXY_UNAVAILABLE")
                } finally {
                    connection.disconnect()
                }
            }
        }

        private fun devicePrivateFilesCase(files: List<Pair<File, Boolean>>) =
            SelfTestCaseDefinition("private-files", setOf(SelfTestMode.DEVICE)) {
                val missingRequired = files.filter { it.second && !safePrivateFile(it.first) }
                val missingOptional = files.filter { !it.second && !safePrivateFile(it.first) }
                when {
                    missingRequired.isNotEmpty() -> fail("private-files", "PRIVATE_FILE_MISSING")
                    missingOptional.isNotEmpty() -> SelfTestCaseResult("private-files", SelfTestStatus.WARN, "PRIVATE_MEMORY_NOT_CREATED", 0L)
                    else -> pass("private-files")
                }
            }

        private fun canaryGuardCase() = SelfTestCaseDefinition(
            "canary-guard",
            setOf(SelfTestMode.CANARY)
        ) {
            SelfTestCaseResult("canary-guard", SelfTestStatus.SKIP, "CANARY_EXPLICIT_CONFIRMATION_REQUIRED", 0L)
        }

        private fun safePrivateFile(file: File): Boolean = runCatching {
            file.isFile && file.length() > 0L && !Files.isSymbolicLink(file.toPath())
        }.getOrDefault(false)

    }
}
