package ai.coreline.heybot

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class GlmAutoReplyHandlerTest {
    @Test
    fun `only an allow-listed external text with 헤이봇 is sent to GLM`() = runBlocking {
        val gateway = RecordingGateway("<think>internal</think> 안녕하세요. 무엇을 도와드릴까요?```")
        val replies = mutableListOf<String>()
        val handler = createHandler(gateway) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇, 안녕"))

        assertEquals(1, gateway.requests.size)
        assertEquals("안녕", gateway.requests.single().messages.last().content)
        assertTrue(gateway.requests.single().messages.first().content.contains("친근하고 자연스러운 해요체"))
        assertEquals(listOf("안녕하세요. 무엇을 도와드릴까요?"), replies)
        handler.close()
    }

    @Test
    fun `skips non-trigger own duplicate and disallowed messages while accepting a bare call`() = runBlocking {
        val gateway = RecordingGateway("답변")
        val handler = createHandler(gateway) { _, _, _ -> }

        handler.process(incoming(logId = 1L, message = "그냥 메시지"))
        handler.process(incoming(logId = 2L, userId = BOT_ID, message = "헤이봇 안녕"))
        handler.process(incoming(logId = 3L, chatId = 1L, message = "헤이봇 안녕"))
        handler.process(incoming(logId = 4L, message = "헤이봇"))
        handler.process(incoming(logId = 5L, message = "헤이봇 안녕"))
        handler.process(incoming(logId = 5L, message = "헤이봇 안녕"))

        assertEquals(2, gateway.requests.size)
        handler.close()
    }

    @Test
    fun `skips non-text messages and accepts trigger mentions as questions`() = runBlocking {
        val gateway = RecordingGateway("답변")
        val handler = createHandler(gateway) { _, _, _ -> }

        handler.process(incoming(logId = 1L, message = "헤이봇 사진").copy(messageType = "2"))
        handler.process(incoming(logId = 2L, message = "헤이봇에게 안녕"))
        handler.process(incoming(logId = 3L, message = "헤이봇:   "))

        assertEquals(
            listOf("헤이봇에게 안녕", "헤이봇:"),
            gateway.requests.map { it.messages.last().content }
        )
        handler.close()
    }

    @Test
    fun `does not reply when GLM fails and keeps the handler alive`() = runBlocking {
        val replies = mutableListOf<String>()
        var calls = 0
        val gateway = GlmGateway {
            calls += 1
            if (calls == 1) Result.failure(GlmFailure.RateLimited())
            else Result.success(testResponse("정상 답변"))
        }
        val logs = mutableListOf<String>()
        val handler = createHandler(gateway, log = logs::add) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, message = "헤이봇 첫 질문"))
        handler.process(incoming(logId = 2L, message = "헤이봇 두 번째 질문"))

        assertEquals(listOf("정상 답변"), replies)
        assertTrue(logs.any { it.contains("RateLimited") })
        handler.close()
    }

    @Test
    fun `wake word replies use the single safety boundary`() = runBlocking {
        val responses = ArrayDeque(
            listOf(
                "Authorization: Bearer abcdefghijklmnop",
                "연락처는 test@example.com 또는 010-1234-5678입니다."
            )
        )
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = GlmGateway {
                Result.success(testResponse(responses.removeFirst()))
            }
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, message = "헤이봇 비밀"))
        handler.process(incoming(logId = 2L, message = "헤이봇 연락처"))

        assertEquals(1, replies.size)
        assertEquals(
            "연락처는 [이메일 마스킹] 또는 [전화번호 마스킹]입니다.",
            replies.single()
        )
        handler.close()
    }

    @Test
    fun `retries a rate-limited request before replying`() = runBlocking {
        val replies = mutableListOf<String>()
        var calls = 0
        val gateway = GlmGateway {
            calls += 1
            if (calls == 1) Result.failure(GlmFailure.RateLimited())
            else Result.success(testResponse("재시도 답변"))
        }
        val handler = createHandler(
            gateway = gateway,
            rateLimitRetries = 1,
            delayForRetry = {}
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇 재시도 확인"))

        assertEquals(2, calls)
        assertEquals(listOf("재시도 답변"), replies)
        handler.close()
    }

    @Test
    fun `uses configured fallback model when primary is rate limited`() = runBlocking {
        val requests = mutableListOf<String>()
        val replies = mutableListOf<String>()
        val gateway = GlmGateway { request ->
            requests += request.model
            if (request.model == "glm-4.5-flash") Result.failure(GlmFailure.RateLimited())
            else Result.success(testResponse("fallback 답변"))
        }
        val handler = createHandler(
            gateway = gateway,
            fallbackModel = "glm-4.7-flash"
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇 대체 모델 확인"))

        assertEquals(listOf("glm-4.5-flash", "glm-4.7-flash"), requests)
        assertEquals(listOf("fallback 답변"), replies)
        handler.close()
    }

    @Test
    fun `does not retain history when enqueueing a reply fails`() = runBlocking {
        val gateway = RecordingGateway("답변")
        var sends = 0
        val handler = createHandler(gateway) { _, _, _ ->
            sends += 1
            if (sends == 1) error("outbound queue unavailable")
        }

        handler.process(incoming(logId = 1L, message = "헤이봇 첫 질문"))
        handler.process(incoming(logId = 2L, message = "헤이봇 두 번째 질문"))

        assertEquals(2, gateway.requests.last().messages.size)
        handler.close()
    }

    @Test
    fun `keeps four recent turns and expires old context`() = runBlocking {
        var now = 1_000L
        val gateway = RecordingGateway("답변")
        val handler = createHandler(gateway, nowMillis = { now }) { _, _, _ -> }

        handler.process(incoming(logId = 1L, message = "헤이봇 첫 질문"))
        now += 100L
        handler.process(incoming(logId = 2L, message = "헤이봇 다음 질문"))
        assertTrue(gateway.requests.last().messages.any { it.role == "assistant" && it.content == "답변" })

        now += 30 * 60 * 1000L + 1L
        handler.process(incoming(logId = 3L, message = "헤이봇 새 질문"))
        assertEquals(2, gateway.requests.last().messages.size)
        handler.close()
    }

    @Test
    fun `queues concurrent automatic replies in input order`() {
        val replyLatch = CountDownLatch(2)
        val replies = mutableListOf<String>()
        val gateway = RecordingGateway("답변")
        val handler = createHandler(gateway) { _, reply, _ ->
            synchronized(replies) { replies += reply }
            replyLatch.countDown()
        }

        handler.onIncoming(incoming(logId = 1L, message = "헤이봇 첫 번째"))
        handler.onIncoming(incoming(logId = 2L, message = "헤이봇 두 번째"))

        assertTrue(replyLatch.await(3, TimeUnit.SECONDS))
        assertEquals(listOf("답변", "답변"), replies)
        assertEquals(
            listOf("첫 번째", "두 번째"),
            gateway.requests.map { it.messages.last().content }
        )
        handler.close()
    }

    @Test
    fun `removes thinking markup and caps an oversized reply`() = runBlocking {
        val replies = mutableListOf<String>()
        val handler = createHandler(
            RecordingGateway("<think>hidden reasoning</think>" + "가".repeat(600))
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇 길이 확인"))

        assertEquals(480, replies.single().length)
        assertTrue(!replies.single().contains("think"))
        handler.close()
    }

    @Test
    fun `help is answered locally without calling GLM`() = runBlocking {
        val gateway = RecordingGateway("호출되면 안 됨")
        val replies = mutableListOf<String>()
        val handler = createHandler(gateway) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇 도움말"))

        assertTrue(gateway.requests.isEmpty())
        assertEquals(2, replies.size)
        val help = replies.joinToString("\n")
        assertTrue(help.contains("문장 어디에 ‘헤이봇’이 있어도"))
        assertTrue(help.contains("헤이봇 내 기억 초기화"))
        assertTrue(help.contains("헤이봇 카톡방"))
        assertTrue(help.contains("헤이봇 이미지 <설명>"))
        assertTrue(help.contains("헤이봇 영상 상태 / 취소 / 재전송"))
        assertTrue(help.contains("헤이봇 펜브러쉬 <설명>"))
        assertTrue(replies.all { it.length <= 480 })
        handler.close()
    }

    @Test
    fun `control room admin help explains every engine and administrative feature without truncation`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val gateway = RecordingGateway("호출되면 안 됨")
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(userId = 100L, message = "헤이봇 도움말"))

        assertTrue(gateway.requests.isEmpty())
        assertEquals(5, replies.size)
        val help = replies.joinToString("\n")
        assertTrue(help.contains("헤이봇 대화 시작"))
        assertTrue(help.contains("헤이봇 대화 상태"))
        assertTrue(help.contains("헤이봇 대화 종료"))
        assertTrue(help.contains("헤이봇 대화 기본"))
        assertTrue(help.contains("Android 자체 GLM"))
        assertTrue(help.contains("헤이봇 대화 코덱스"))
        assertTrue(help.contains("헤이봇 대화 그록"))
        assertTrue(help.contains("헤이봇 자체진단 [빠른|통합|기기|카나리]"))
        assertTrue(help.contains("헤이봇 <기능> 허용|불허용 <R번호>"))
        assertTrue(help.contains("헤이봇 방 적용 <코드> / 방 취소"))
        assertTrue(replies.all { it.length <= 480 })
        handler.close()
    }

    @Test
    fun `kakao room command replies with the full supported room list`() = runBlocking {
        val replies = mutableListOf<String>()
        val policy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability("R01", CHAT_ID, "코어라인 AI 연구소", true, true, true, true, true)
            ),
            controlChatId = CHAT_ID,
            backend = object : ConversationMemoryBackend {
                override fun read(): ByteArray? = null
                override fun write(bytes: ByteArray) = Unit
                override fun quarantine(nowMillis: Long) = Unit
            }
        )
        val handler = createHandler(
            gateway = RecordingGateway("호출되면 안 됨"),
            roomCapabilityPolicy = policy
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(message = "헤이봇 카톡방"))

        assertEquals(
            "헤이봇 지원 카톡방 목록\n\n" +
            "R01. 코어라인 AI 연구소\n" +
            "텍스트: 허용 | 일반대화: 허용 | " +
            "이미지: 허용 | 영상: 허용 | 펜브러쉬: 허용",
            replies.single()
        )
        handler.close()
    }

    @Test
    fun `conversation context is separated by chat and user`() = runBlocking {
        val gateway = RecordingGateway("답변")
        val secondChatId = CHAT_ID + 1L
        val handler = createHandler(
            gateway,
            allowedChatIds = setOf(CHAT_ID, secondChatId)
        ) { _, _, _ -> }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 A 질문"))
        handler.process(incoming(logId = 2L, userId = 101L, message = "헤이봇 B 질문"))
        handler.process(
            incoming(
                logId = 3L,
                chatId = secondChatId,
                userId = 100L,
                message = "헤이봇 다른 방 질문"
            )
        )

        assertEquals(2, gateway.requests[1].messages.size)
        assertTrue(gateway.requests[1].messages.none { it.content == "A 질문" })
        assertEquals(2, gateway.requests[2].messages.size)
        assertTrue(gateway.requests[2].messages.none { it.content == "A 질문" })
        handler.close()
    }

    @Test
    fun `admin status requires an exact numeric user ID and bypasses GLM`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val gateway = RecordingGateway("호출되면 안 됨")
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 101L, message = "헤이봇 상태"))
        handler.process(incoming(logId = 2L, userId = 100L, message = "헤이봇 상태"))

        assertTrue(gateway.requests.isEmpty())
        assertTrue(replies[0].contains("관리자만"))
        assertTrue(replies[1].contains("정상 동작"))
        assertTrue(replies[1].length <= 480)
        assertTrue(!replies[1].contains("Bearer"))
        handler.close()
    }

    @Test
    fun `admin settings are rejected outside the configured Coreline control room`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val gateway = RecordingGateway("호출되면 안 됨")
        val replies = mutableListOf<Pair<Long, String>>()
        val secondChatId = CHAT_ID + 1L
        val handler = createHandler(
            gateway = gateway,
            allowedChatIds = setOf(CHAT_ID, secondChatId),
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { chatId, reply, _ -> replies += chatId to reply }

        handler.process(incoming(logId = 1L, chatId = secondChatId, userId = 100L, message = "헤이봇 상태"))
        handler.process(incoming(logId = 2L, chatId = CHAT_ID, userId = 100L, message = "헤이봇 상태"))

        assertTrue(gateway.requests.isEmpty())
        assertTrue(replies[0].second.contains("코어라인 AI 연구소"))
        assertTrue(replies[1].second.contains("정상 동작"))
        handler.close()
    }

    @Test
    fun `control room admin can enable a room and text replies take effect without restart`() = runBlocking {
        val targetRoom = CHAT_ID + 99L
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val backend = object : ConversationMemoryBackend {
            var bytes: ByteArray? = null
            override fun read(): ByteArray? = bytes
            override fun write(bytes: ByteArray) { this.bytes = bytes }
            override fun quarantine(nowMillis: Long) = Unit
        }
        val roomPolicy = RoomCapabilityPolicyStore.forTesting(
            rooms = listOf(
                ManagedRoomCapability("R01", CHAT_ID, "코어라인 AI 연구소", true, true, true),
                ManagedRoomCapability("R02", targetRoom, "대상 방", false, false, false)
            ),
            controlChatId = CHAT_ID,
            backend = backend
        )
        val gateway = RecordingGateway("답변")
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            allowedChatIds = setOf(CHAT_ID, targetRoom),
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            roomCapabilityPolicy = roomPolicy
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, chatId = targetRoom, message = "헤이봇 안녕"))
        assertTrue(gateway.requests.isEmpty())

        handler.process(incoming(logId = 2L, userId = 100L, message = "헤이봇 방 텍스트 허용 R02"))
        val nonce = Regex("헤이봇 방 적용 ([A-Z0-9]+)").find(replies.last())!!.groupValues[1]
        handler.process(incoming(logId = 3L, userId = 100L, message = "헤이봇 방 적용 $nonce"))
        handler.process(incoming(logId = 4L, chatId = targetRoom, message = "헤이봇 안녕"))

        assertEquals(1, gateway.requests.size)
        assertTrue(backend.bytes != null)
        handler.close()
    }

    @Test
    fun `control room admin enables and stops ordinary conversation in every allow-listed room`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val secondChatId = CHAT_ID + 1L
        val gateway = RecordingGateway("""{"action":"REPLY","reply":"일반대화 답변"}""")
        val replies = mutableListOf<Pair<Long, String>>()
        val modeBackend = TestConversationModeBackend()
        val handler = createHandler(
            gateway = gateway,
            allowedChatIds = setOf(CHAT_ID, secondChatId),
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            generalConversationModeStore = GeneralConversationModeStore(modeBackend)
        ) { chatId, reply, _ -> replies += chatId to reply }

        handler.process(incoming(logId = 1L, chatId = secondChatId, userId = 200L, message = "호출어 없는 질문"))
        assertTrue(gateway.requests.isEmpty())

        handler.process(incoming(logId = 2L, chatId = CHAT_ID, userId = 100L, message = "헤이봇 대화 시작"))
        assertTrue(GeneralConversationModeStore(modeBackend).status().enabled)
        handler.process(incoming(logId = 3L, chatId = secondChatId, userId = 200L, message = "호출어 없는 질문"))
        handler.process(incoming(logId = 4L, chatId = CHAT_ID, userId = 201L, message = "다른 방 질문"))

        assertEquals(2, gateway.requests.size)
        assertTrue(replies.any { it.first == secondChatId && it.second == "일반대화 답변" })
        assertTrue(replies.any { it.first == CHAT_ID && it.second == "일반대화 답변" })

        handler.process(incoming(logId = 5L, chatId = CHAT_ID, userId = 100L, message = "헤이봇 대화 종료"))
        handler.process(incoming(logId = 6L, chatId = secondChatId, userId = 200L, message = "종료 뒤 질문"))

        assertEquals(2, gateway.requests.size)
        assertFalse(GeneralConversationModeStore(modeBackend).status().enabled)
        handler.close()
    }

    @Test
    fun `general conversation only sends a strict reply decision`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val responses = ArrayDeque(
            listOf(
                """{"action":"WAIT","reply":""}""",
                """{"action":"IGNORE","reply":""}""",
                """{"action":"REPLY","reply":"답변할게요."}"""
            )
        )
        var gatewayCalls = 0
        val gateway = GlmGateway {
            gatewayCalls += 1
            Result.success(testResponse(responses.removeFirst()))
        }
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, message = "잠깐만"))
        handler.process(incoming(logId = 3L, message = "ㅋㅋ"))
        handler.process(incoming(logId = 4L, message = "질문 있어"))

        assertEquals(3, gatewayCalls)
        assertTrue(replies.any { it == "답변할게요." })
        assertEquals(2, replies.size) // start acknowledgement + one REPLY only
        handler.close()
    }

    @Test
    fun `truncated general reply is retried with a complete JSON response`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val requests = mutableListOf<GlmChatRequest>()
        val replies = mutableListOf<String>()
        var calls = 0
        val handler = createHandler(
            gateway = GlmGateway { request ->
                requests += request
                calls += 1
                if (calls == 1) {
                    Result.success(
                        testResponse("""{"action":"REPLY","reply":"잘린 답변""")
                            .copy(finishReason = "length")
                    )
                } else {
                    Result.success(
                        testResponse(
                            """{"action":"REPLY","reply":"우선순위를 정하고 실행한 뒤 마무리 점검을 하세요."}"""
                        )
                    )
                }
            },
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(
            incoming(
                logId = 2L,
                userId = 200L,
                message = "일반대화 테스트: 오늘 해야 할 일을 세 단계로 정리해줘"
            )
        )

        assertEquals(2, requests.size)
        assertEquals(384, requests.first().maxTokens)
        assertEquals(512, requests.last().maxTokens)
        assertTrue(replies.any { it.startsWith("우선순위를 정하고") })
        assertEquals(1L, handler.metricsSnapshot().generalConversationTruncationRetries)
        assertEquals(1L, handler.metricsSnapshot().generalConversationReplies)
        assertEquals(0L, handler.metricsSnapshot().generalConversationInvalidResponses)
        handler.close()
    }

    @Test
    fun `general conversation shares successful same user memory with wake word questions only`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val requests = mutableListOf<GlmChatRequest>()
        var generalReplies = 0
        val handler = createHandler(
            gateway = GlmGateway { request ->
                requests += request
                val response = when (request.kind) {
                    GlmRequestKind.GENERAL_CONVERSATION -> {
                        generalReplies += 1
                        if (generalReplies == 1) {
                            """{"action":"REPLY","reply":"첫 일반 답변"}"""
                        } else {
                            """{"action":"REPLY","reply":"이은 일반 답변"}"""
                        }
                    }
                    else -> "호출어 답변"
                }
                Result.success(testResponse(response))
            },
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            roomRateMaxRequests = 5
        ) { _, _, _ -> }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, userId = 200L, message = "첫 일반 질문"))
        handler.process(incoming(logId = 3L, userId = 200L, message = "헤이봇 호출어 질문"))
        handler.process(incoming(logId = 4L, userId = 200L, message = "이어서 알려줘"))
        handler.process(incoming(logId = 5L, userId = 201L, message = "다른 사람 질문"))

        val generalRequests = requests.filter { it.kind == GlmRequestKind.GENERAL_CONVERSATION }
        assertEquals(3, generalRequests.size)
        val sameUserFollowUp = generalRequests[1].messages
        assertTrue(sameUserFollowUp.any { it.content == "첫 일반 질문" })
        assertTrue(sameUserFollowUp.any { it.content == "첫 일반 답변" })
        assertTrue(sameUserFollowUp.any { it.content == "호출어 질문" })
        assertTrue(sameUserFollowUp.any { it.content == "호출어 답변" })
        assertEquals("현재 마지막 발화입니다.\n이어서 알려줘", sameUserFollowUp.last().content)

        val otherUserMessages = generalRequests[2].messages
        assertEquals(2, otherUserMessages.size)
        assertEquals("현재 마지막 발화입니다.\n다른 사람 질문", otherUserMessages.last().content)
        handler.close()
    }

    @Test
    fun `wait keeps a short unfinished context then commits it after a sent reply`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val responses = ArrayDeque(
            listOf(
                """{"action":"WAIT","reply":""}""",
                """{"action":"REPLY","reply":"연동하면 될 것 같아요."}""",
                """{"action":"REPLY","reply":"추가 답변"}"""
            )
        )
        val requests = mutableListOf<GlmChatRequest>()
        val handler = createHandler(
            gateway = GlmGateway { request ->
                requests += request
                Result.success(testResponse(responses.removeFirst()))
            },
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, _, _ -> }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, userId = 200L, message = "기능 붙이던지"))
        handler.process(incoming(logId = 3L, userId = 200L, message = "헤르메스 연동 하면 될 것 같아요"))
        handler.process(incoming(logId = 4L, userId = 200L, message = "그다음은요"))

        val secondRequest = requests[1].messages
        assertTrue(secondRequest.any { it.content.contains("기능 붙이던지") })
        val thirdRequest = requests[2].messages
        assertTrue(thirdRequest.any {
            it.role == "user" && it.content == "기능 붙이던지\n헤르메스 연동 하면 될 것 같아요"
        })
        assertTrue(thirdRequest.any { it.role == "assistant" && it.content == "연동하면 될 것 같아요." })
        handler.close()
    }

    @Test
    fun `failed general reply send does not enter later context`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val requests = mutableListOf<GlmChatRequest>()
        val responses = ArrayDeque(
            listOf(
                """{"action":"REPLY","reply":"전송 실패 답변"}""",
                """{"action":"REPLY","reply":"정상 답변"}"""
            )
        )
        val handler = createHandler(
            gateway = GlmGateway { request ->
                requests += request
                Result.success(testResponse(responses.removeFirst()))
            },
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ ->
            if (reply == "전송 실패 답변") error("send failure")
        }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, userId = 200L, message = "저장되면 안 되는 질문"))
        handler.process(incoming(logId = 3L, userId = 200L, message = "다음 질문"))

        assertEquals(2, requests.size)
        assertEquals(2, requests[1].messages.size)
        assertEquals("현재 마지막 발화입니다.\n다음 질문", requests[1].messages.last().content)
        handler.close()
    }

    @Test
    fun `general conversation policy blocks before admission and gateway`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val gateway = RecordingGateway("""{"action":"REPLY","reply":"응답"}""")
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            generalConversationPolicy = GeneralConversationPolicy.forTesting(
                allowedChatIds = setOf(CHAT_ID),
                globallyBlockedUserIds = setOf(200L)
            )
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, userId = 200L, message = "차단 사용자 질문"))
        handler.process(incoming(logId = 3L, userId = 201L, message = "허용 사용자 질문"))

        assertEquals(1, gateway.requests.size)
        assertTrue(replies.any { it == "응답" })
        handler.close()
    }

    @Test
    fun `general conversation reply uses the same safety boundary`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val responses = ArrayDeque(
            listOf(
                """{"action":"REPLY","reply":"token=abcdefghijk"}""",
                """{"action":"REPLY","reply":"메일 test@example.com"}"""
            )
        )
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = GlmGateway {
                Result.success(testResponse(responses.removeFirst()))
            },
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, message = "비밀 요청"))
        handler.process(incoming(logId = 3L, message = "메일 요청"))

        assertEquals(2, replies.size)
        assertTrue(replies.first().contains("일반대화 모드를 시작"))
        assertEquals("메일 [이메일 마스킹]", replies.last())
        handler.close()
    }

    @Test
    fun `general conversation cannot start when policy is unavailable`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val gateway = RecordingGateway("""{"action":"REPLY","reply":"호출되면 안 됨"}""")
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            generalConversationPolicy = GeneralConversationPolicy.disabled()
        ) { _, reply, _ -> replies += reply }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.process(incoming(logId = 2L, userId = 201L, message = "호출어 없는 질문"))

        assertTrue(gateway.requests.isEmpty())
        assertTrue(replies.single().contains("정책이 준비되지 않아"))
        handler.close()
    }

    @Test
    fun `general conversation circuit trips without disabling wake word requests and admin can reset it`() =
        runBlocking {
            val adminFile = File.createTempFile("iris-admin", ".txt").apply {
                writeText("100")
                deleteOnExit()
            }
            var now = 1_000L
            var generalCalls = 0
            var wakeWordCalls = 0
            val gateway = GlmGateway { request ->
                if (request.kind == GlmRequestKind.GENERAL_CONVERSATION) {
                    generalCalls += 1
                    Result.failure(GlmFailure.Server(503))
                } else {
                    wakeWordCalls += 1
                    Result.success(testResponse("호출어 응답"))
                }
            }
            val replies = mutableListOf<String>()
            val logs = mutableListOf<String>()
            val modeBackend = TestConversationModeBackend()
            val handler = createHandler(
                gateway = gateway,
                nowMillis = { now },
                log = logs::add,
                adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
                adminControlChatId = CHAT_ID,
                generalConversationModeStore = GeneralConversationModeStore(modeBackend)
            ) { _, reply, _ -> replies += reply }

            handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
            handler.process(incoming(logId = 2L, userId = 200L, message = "첫 번째 일반 질문"))
            handler.process(incoming(logId = 3L, userId = 201L, message = "두 번째 일반 질문"))
            handler.process(incoming(logId = 4L, userId = 202L, message = "세 번째 일반 질문"))
            handler.process(incoming(logId = 5L, userId = 203L, message = "차단 뒤 일반 질문"))
            handler.process(incoming(logId = 6L, userId = 100L, message = "헤이봇 대화 상태"))

            assertEquals(3, generalCalls)
            assertEquals(1L, handler.metricsSnapshot().generalCircuitTrips)
            assertTrue(replies.any { it.contains("회로 차단") })
            assertTrue(logs.any { it.contains("circuit tripped") && it.contains("SERVER") })
            assertFalse(GeneralConversationModeStore(modeBackend).status().enabled)

            now += 31_000L
            handler.process(incoming(logId = 7L, userId = 204L, message = "헤이봇 호출어 확인"))
            assertEquals(1, wakeWordCalls)
            assertTrue(replies.any { it == "호출어 응답" })

            handler.process(incoming(logId = 8L, userId = 100L, message = "헤이봇 대화 시작"))
            handler.process(incoming(logId = 9L, userId = 205L, message = "재시작 뒤 일반 질문"))
            assertEquals(4, generalCalls)
            handler.close()
        }

    @Test
    fun `circuit trip invalidates an in-flight general conversation reply`() = runBlocking {
        val adminFile = File.createTempFile("iris-admin", ".txt").apply {
            writeText("100")
            deleteOnExit()
        }
        val slowRoomId = CHAT_ID + 1L
        val failureRoomId = CHAT_ID + 2L
        val allowedChatIds = setOf(CHAT_ID, slowRoomId, failureRoomId)
        val slowStarted = CompletableDeferred<Unit>()
        val releaseSlow = CompletableDeferred<Unit>()
        val circuitTripped = CompletableDeferred<Unit>()
        val gateway = GlmGateway { request ->
            when (request.messages.last().content.substringAfterLast('\n')) {
                "느린 일반 질문" -> {
                    slowStarted.complete(Unit)
                    releaseSlow.await()
                    Result.success(testResponse("""{"action":"REPLY","reply":"전송되면 안 됨"}"""))
                }
                else -> Result.failure(GlmFailure.Server(503))
            }
        }
        val replies = mutableListOf<String>()
        val handler = createHandler(
            gateway = gateway,
            allowedChatIds = allowedChatIds,
            adminAuthorizer = AdminAuthorizer.fromFile(adminFile) {},
            adminControlChatId = CHAT_ID,
            log = {
                if (it.contains("circuit tripped")) circuitTripped.complete(Unit)
            }
        ) { _, reply, _ -> synchronized(replies) { replies += reply } }

        handler.process(incoming(logId = 1L, userId = 100L, message = "헤이봇 대화 시작"))
        handler.onIncoming(
            incoming(logId = 2L, chatId = slowRoomId, userId = 200L, message = "느린 일반 질문")
        )
        withTimeout(1_000L) { slowStarted.await() }

        handler.onIncoming(
            incoming(logId = 3L, chatId = failureRoomId, userId = 201L, message = "실패 질문 1")
        )
        handler.onIncoming(
            incoming(logId = 4L, chatId = failureRoomId, userId = 202L, message = "실패 질문 2")
        )
        handler.onIncoming(
            incoming(logId = 5L, chatId = failureRoomId, userId = 203L, message = "실패 질문 3")
        )
        withTimeout(1_000L) { circuitTripped.await() }
        releaseSlow.complete(Unit)
        withTimeout(1_000L) {
            while (handler.queueSnapshot().active > 0 || handler.queueSnapshot().totalPending > 0) {
                delay(10L)
            }
        }

        assertTrue(replies.none { it == "전송되면 안 됨" })
        handler.close()
    }

    @Test
    fun `slow GLM request in one room does not block another room`() = runBlocking {
        val firstStarted = CompletableDeferred<Unit>()
        val releaseFirst = CompletableDeferred<Unit>()
        val secondRoomReplied = CompletableDeferred<Unit>()
        val secondChatId = CHAT_ID + 1L
        val gateway = GlmGateway { request ->
            if (request.messages.last().content == "느린 질문") {
                firstStarted.complete(Unit)
                releaseFirst.await()
            }
            Result.success(testResponse("답변"))
        }
        val handler = createHandler(
            gateway = gateway,
            allowedChatIds = setOf(CHAT_ID, secondChatId)
        ) { chatId, _, _ ->
            if (chatId == secondChatId) secondRoomReplied.complete(Unit)
        }

        handler.onIncoming(incoming(logId = 1L, chatId = CHAT_ID, message = "헤이봇 느린 질문"))
        withTimeout(1_000L) { firstStarted.await() }
        handler.onIncoming(incoming(logId = 2L, chatId = secondChatId, message = "헤이봇 빠른 질문"))

        withTimeout(1_000L) { secondRoomReplied.await() }
        releaseFirst.complete(Unit)
        handler.close()
    }

    private fun createHandler(
        gateway: GlmGateway,
        nowMillis: () -> Long = { System.currentTimeMillis() },
        log: (String) -> Unit = {},
        rateLimitRetries: Int = 0,
        delayForRetry: suspend (Long) -> Unit = {},
        fallbackModel: String? = null,
        allowedChatIds: Set<Long> = setOf(CHAT_ID),
        adminAuthorizer: AdminAuthorizer = AdminAuthorizer.empty(),
        adminControlChatId: Long? = null,
        generalConversationModeStore: GeneralConversationModeStore =
            GeneralConversationModeStore(),
        generalConversationPolicy: GeneralConversationPolicy =
            GeneralConversationPolicy.forTesting(allowedChatIds),
        roomCapabilityPolicy: RoomCapabilityPolicyStore =
            RoomCapabilityPolicyStore.legacy(allowedChatIds),
        roomRateMaxRequests: Int = 3,
        reply: (Long, String, Long?) -> Unit
    ): GlmAutoReplyHandler = GlmAutoReplyHandler(
        settings = GlmSettings(
            baseUrl = "https://api.z.ai/api/paas/v4/",
            model = "glm-4.5-flash",
            fallbackModel = fallbackModel,
            trigger = "헤이봇",
            allowedChatIds = allowedChatIds,
            apiKeyFile = File("/not-used-in-test"),
            timeoutMillis = 60_000L,
            maxTokens = 256,
            temperature = 0.2,
            rateLimitRetries = rateLimitRetries,
            adminControlChatId = adminControlChatId,
            roomRateMaxRequests = roomRateMaxRequests
        ),
        botId = BOT_ID,
        gateway = gateway,
        replySender = GlmReplySender { chatId, message, threadId -> reply(chatId, message, threadId) },
        log = log,
        nowMillis = nowMillis,
        delayForRetry = delayForRetry,
        adminAuthorizer = adminAuthorizer,
        generalConversationModeStore = generalConversationModeStore,
        generalConversationPolicy = generalConversationPolicy,
        roomCapabilityPolicy = roomCapabilityPolicy
    )

    private class TestConversationModeBackend : ConversationMemoryBackend {
        private var bytes: ByteArray? = null

        override fun read(): ByteArray? = bytes

        override fun write(bytes: ByteArray) {
            this.bytes = bytes
        }

        override fun quarantine(nowMillis: Long) {
            bytes = null
        }
    }

    private fun incoming(
        logId: Long = 1L,
        chatId: Long = CHAT_ID,
        userId: Long = 999L,
        message: String
    ) = GlmIncomingMessage(
        logId = logId,
        chatId = chatId,
        userId = userId,
        messageType = "1",
        message = message,
        threadId = null
    )

    private class RecordingGateway(private val response: String) : GlmGateway {
        val requests = mutableListOf<GlmChatRequest>()

        override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> {
            requests += request
            return Result.success(testResponse(response))
        }
    }

    private companion object {
        const val CHAT_ID = 18480337854645134L
        const val BOT_ID = 444364619L

        fun testResponse(content: String) = GlmChatResponse(
            content = content,
            requestId = "test-request",
            finishReason = "stop",
            promptTokens = null,
            completionTokens = null,
            totalTokens = null,
            latencyMillis = 1L
        )
    }
}
