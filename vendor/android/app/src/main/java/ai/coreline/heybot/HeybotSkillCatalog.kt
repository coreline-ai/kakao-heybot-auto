package ai.coreline.heybot

enum class SkillAudience { USER, ADMIN }

enum class SkillExecutionKind { CONVERSATION, LOCAL, ASYNC }

enum class SkillAsyncControl { STATUS, CANCEL, RETRY }

data class HeybotSkillDefinition(
    val id: String,
    val displayName: String,
    val aliases: Set<String>,
    val examples: List<String>,
    val description: String,
    val audience: SkillAudience,
    val capability: RoomCapability?,
    val execution: SkillExecutionKind,
    val asyncControls: Set<SkillAsyncControl> = emptySet()
)

/** Static metadata only. Execution remains in the existing bounded handlers. */
object HeybotSkillCatalog {
    private val ASYNC_CONTROLS = setOf(
        SkillAsyncControl.STATUS,
        SkillAsyncControl.CANCEL,
        SkillAsyncControl.RETRY
    )

    val skills: List<HeybotSkillDefinition> = listOf(
        skill(
            "conversation.ask", "대화", setOf("질문", "대화"),
            listOf("헤이봇 <질문>"),
            "문장 어디에 ‘헤이봇’이 있어도 질문에 답해요.",
            RoomCapability.TEXT, SkillExecutionKind.CONVERSATION
        ),
        skill(
            "memory.clear.mine", "내 기억 초기화", setOf("기억", "기억 초기화"),
            listOf("헤이봇 내 기억 초기화"),
            "이 방에서 나눈 내 대화와 이미지·음성 분석 문맥을 삭제해요.",
            RoomCapability.TEXT, SkillExecutionKind.LOCAL
        ),
        skill(
            "room.list", "카톡방", setOf("방", "방 목록"),
            listOf("헤이봇 카톡방"),
            "지원 방과 R번호, 기능별 허용 상태를 보여줘요.",
            RoomCapability.TEXT, SkillExecutionKind.LOCAL
        ),
        skill(
            "image.generate", "이미지", setOf("이미지 생성"),
            listOf("헤이봇 이미지 <설명>"),
            "설명을 바탕으로 이미지를 만들어요.",
            RoomCapability.IMAGE, SkillExecutionKind.ASYNC, ASYNC_CONTROLS
        ),
        skill(
            "image.analyze", "이미지 분석", setOf("이미지분석", "사진 분석"),
            listOf(
                "이미지 전송 또는 답장 후 헤이봇 이미지 분석",
                "분석 직후 가방은 무슨 색이야?",
                "헤이봇 그 이미지에서 가방은 무슨 색이야?"
            ),
            "이미지를 설명하고, 관련 질문은 바로 이어서 말하거나 호출어·답장으로 계속할 수 있어요.",
            RoomCapability.IMAGE_ANALYSIS, SkillExecutionKind.ASYNC
        ),
        skill(
            "image.ocr", "이미지 글자 추출", setOf("OCR", "글자 추출"),
            listOf("이미지 전송 또는 답장 후 헤이봇 이미지 글자 추출"),
            "이미지에 실제로 보이는 글자를 읽는 순서대로 추출해요.",
            RoomCapability.IMAGE_ANALYSIS, SkillExecutionKind.ASYNC
        ),
        skill(
            "image.translate_ko", "이미지 글자 번역", setOf("이미지 번역", "글자 번역"),
            listOf("이미지 전송 또는 답장 후 헤이봇 이미지 글자 번역"),
            "이미지의 글자를 추출한 뒤 한국어로 번역해요.",
            RoomCapability.IMAGE_ANALYSIS, SkillExecutionKind.ASYNC
        ),
        skill(
            "video.generate", "영상", setOf("영상 생성"),
            listOf("헤이봇 영상 <설명>"),
            "설명을 바탕으로 짧은 영상을 만들어요.",
            RoomCapability.VIDEO, SkillExecutionKind.ASYNC, ASYNC_CONTROLS
        ),
        skill(
            "youtube.download", "유튜브 다운로드", setOf("유튜브", "유튜브 영상"),
            listOf("헤이봇 유튜브 다운로드 <YouTube 링크>", "헤이봇 유튜브 상태", "헤이봇 유튜브 재전송"),
            "단일 YouTube 영상을 품질 균형 MP4로 받아 같은 방에 전송해요. 이 방의 유튜브 권한이 필요해요.",
            RoomCapability.YOUTUBE_DOWNLOAD, SkillExecutionKind.ASYNC, ASYNC_CONTROLS
        ),
        skill(
            "pen_brush.generate", "펜브러쉬", setOf("펜 브러쉬", "펜브러쉬 영상"),
            listOf("헤이봇 펜브러쉬 <설명>"),
            "펜 외곽선 뒤 브러시로 채색하는 영상을 만들어요.",
            RoomCapability.PEN_BRUSH, SkillExecutionKind.ASYNC, ASYNC_CONTROLS
        ),
        skill(
            "audio.summarize", "음성 요약", setOf("STT", "음성 분석", "녹음 요약"),
            listOf(
                "MP3·M4A·WAV 전송 후 헤이봇 음성 요약",
                "헤이봇 음성 요약 회의 회의록",
                "헤이봇 음성 원문 1",
                "헤이봇 음성 재전송"
            ),
            "같은 방에 누가 올린 음성이든 한국어로 전사하고, 확인된 요약은 이어서 질문할 수 있어요.",
            RoomCapability.AUDIO_ANALYSIS, SkillExecutionKind.ASYNC,
            setOf(SkillAsyncControl.STATUS, SkillAsyncControl.CANCEL)
        ),
        adminSkill(
            "admin.general_conversation", "일반대화 관리", setOf("일반대화"),
            listOf("헤이봇 대화 시작", "헤이봇 대화 상태", "헤이봇 대화 종료"),
            "허용방의 호출어 없는 일반대화를 전역으로 시작·확인·종료해요."
        ),
        adminSkill(
            "admin.engine", "응답 엔진", setOf("엔진", "대화 엔진"),
            listOf("헤이봇 대화 기본", "헤이봇 대화 코덱스", "헤이봇 대화 그록"),
            "호출어와 일반대화의 응답 엔진을 전역으로 선택해요."
        ),
        adminSkill(
            "admin.self_test", "자체진단", setOf("진단", "셀프 테스트"),
            listOf("헤이봇 자체진단 [빠른|통합|기기|카나리]"),
            "코드·프록시·PD20 상태를 단계별로 검사해요."
        ),
        adminSkill(
            "admin.room_policy", "방 권한", setOf("권한", "방 설정"),
            listOf("헤이봇 방 상태 <R번호>", "헤이봇 <기능> 허용|불허용 <R번호>"),
            "방별 기능 권한을 미리보기와 적용 코드로 변경해요."
        ),
        adminSkill(
            "admin.operations", "운영 상태", setOf("상태", "설정"),
            listOf("헤이봇 상태", "헤이봇 설정 보기"),
            "큐·오류·기억·정책 등 운영 상태를 확인해요."
        ),
        adminSkill(
            "admin.diagnostics", "최근 요청 진단", setOf("최근 진단", "요청 진단"),
            listOf("헤이봇 최근 진단", "헤이봇 최근 진단 R03"),
            "직전 요청이 멈춘 단계와 안정된 사유 코드만 확인해요."
        )
    )

    fun find(name: String): HeybotSkillDefinition? {
        val normalized = name.trim().replace(Regex("\\s+"), " ")
        if (normalized.isBlank()) return null
        return skills.firstOrNull { skill ->
            skill.id.equals(normalized, ignoreCase = true) ||
                skill.displayName.equals(normalized, ignoreCase = true) ||
                skill.aliases.any { it.equals(normalized, ignoreCase = true) }
        }
    }

    fun renderAvailable(
        policy: RoomCapabilityPolicyStore,
        chatId: Long,
        includeAdmin: Boolean
    ): List<String> {
        val visible = skills.filter { skill ->
            (includeAdmin || skill.audience == SkillAudience.USER) &&
                (skill.capability == null || policy.allows(chatId, skill.capability))
        }
        val user = visible.filter { it.audience == SkillAudience.USER }
        val admin = visible.filter { it.audience == SkillAudience.ADMIN }
        return buildList {
            add(
                "헤이봇 기능\n\n" + user.joinToString("\n") {
                    "• ${it.displayName}: ${it.examples.first()}"
                } + "\n\n자세히: 헤이봇 기능 <이름>"
            )
            if (includeAdmin && admin.isNotEmpty()) {
                add(
                    "관리자 기능\n\n" + admin.joinToString("\n") {
                        "• ${it.displayName}: ${it.examples.first()}"
                    }
                )
            }
        }
    }

    fun renderDetail(
        name: String,
        policy: RoomCapabilityPolicyStore,
        chatId: Long,
        includeAdmin: Boolean
    ): String {
        val skill = find(name) ?: return "기능을 찾지 못했어요. ‘헤이봇 기능’으로 목록을 확인해주세요."
        if (skill.audience == SkillAudience.ADMIN && !includeAdmin) {
            return "이 기능은 코어라인 AI 연구소 관리자만 확인할 수 있어요."
        }
        val availability = when {
            skill.capability == null -> "사용 가능"
            policy.allows(chatId, skill.capability) -> "현재 방에서 사용 가능"
            else -> "현재 방에서 불허용"
        }
        val controls = skill.asyncControls
            .takeIf { it.isNotEmpty() }
            ?.joinToString("/") { it.name.lowercase() }
            ?.let { "\n작업 제어: $it" }
            .orEmpty()
        return buildString {
            append(skill.displayName).append("\n")
            append(skill.description).append("\n")
            append("상태: ").append(availability).append("\n")
            append("예시\n")
            skill.examples.forEach { append("• ").append(it).append("\n") }
            append(controls)
        }.trim().take(MAX_RENDER_CHARS)
    }

    fun userHelpMessages(): List<String> = listOf(
        "헤이봇 사용법 1/5\n\n" +
            "[대화]\n• 헤이봇 <질문>\n  ${requireSkill("conversation.ask").description}\n\n" +
            "[기억]\n• 헤이봇 내 기억 초기화\n  ${requireSkill("memory.clear.mine").description}\n\n" +
            "[카톡방]\n• 헤이봇 카톡방\n  ${requireSkill("room.list").description}\n\n" +
            "[기능 찾기]\n• 헤이봇 기능 / 헤이봇 기능 <이름>",
        "헤이봇 사용법 2/5\n\n" +
            "[이미지]\n• 헤이봇 이미지 <설명>\n• 헤이봇 이미지 상태 / 취소 / 재전송\n" +
            "• 이미지 전송 후 헤이봇 이미지 분석\n  답장한 이미지 또는 최근 이미지를 설명해요.\n\n" +
            "• 헤이봇 이미지 글자 추출 / 이미지 글자 번역\n  이미지 속 글자를 추출하거나 한국어로 번역해요.\n\n" +
            "• 분석 직후 <관련 질문>\n  예: 선인장은 어느 쪽에 있어? / 가방은 무슨 색이야?\n" +
            "  분석한 사람은 30분 동안 바로 이어서 묻고, 다른 참여자는 최근 5분 동안 관련 질문을 이어갈 수 있어요.\n" +
            "• 헤이봇 그 이미지에서 <질문>\n  시간이 지난 뒤에는 호출어를 쓰거나 결과 메시지에 답장해주세요.\n\n" +
            "[영상]\n• 헤이봇 영상 <설명>\n• 헤이봇 영상 상태 / 취소 / 재전송\n\n" +
            "카카오톡이 영상을 처리하는 동안에는 최대 20분까지 확인하며, 자동 재전송하지 않아요.\n\n" +
            "[펜브러쉬 영상]\n• 헤이봇 펜브러쉬 <설명>\n• 헤이봇 펜브러쉬 상태 / 취소 / 재전송",
        "헤이봇 사용법 3/5\n\n" +
            "[음성 STT·요약]\n" +
            "• MP3·M4A·WAV를 보낸 뒤 헤이봇 음성 요약\n" +
            "  기본값: 자동 유형·기본 보기\n" +
            "• 헤이봇 음성 요약 <유형> <보기>\n" +
            "  유형: 자동·일반·회의·인터뷰·강의·통화·상담·업무보고·질의응답\n" +
            "  보기: 짧게·기본·상세·액션·타임라인·회의록\n" +
            "  예: 헤이봇 음성 요약 회의 회의록",
        "헤이봇 사용법 4/5\n\n" +
            "[유튜브 다운로드]\n" +
            "• 헤이봇 유튜브 다운로드 <YouTube 링크>\n" +
            "• 헤이봇 유튜브 상태 / 취소 / 재전송 / 삭제\n" +
            "단일 공개 YouTube 영상만 품질 균형 MP4로 전송해요. 짧은 영상은 최대 480p, 중간 영상은 360p, 긴 영상은 270p로 조정되며 세로 영상의 긴 변도 보존해요. 카카오 처리 확인은 최대 20분 기다리고 자동 재전송하지 않아요. 지연 뒤 재전송 명령으로 한 번만 다시 시도해요. 재생목록·로그인·DRM 영상은 지원하지 않으며, 이 방의 유튜브 권한이 필요해요.",
        "헤이봇 사용법 5/5\n\n" +
            "[음성 작업 관리]\n" +
            "• 헤이봇 음성 상태 / 헤이봇 음성 취소\n" +
            "• 헤이봇 음성 재요약 / 헤이봇 음성 재전송 / 헤이봇 음성 삭제\n" +
            "• 헤이봇 음성 원문 [페이지] / 음성 근거 [페이지]\n" +
            "재요약은 STT를 반복하지 않고 현재 대화 엔진으로 다시 요약해요. 재전송은 DB 확인이 안 된 part만 다시 보내요.\n\n" +
            "DB 확인된 요약에는 30분 동안 바로 이어 질문하거나 결과 메시지에 답장할 수 있어요.\n" +
            "답장하면 그 음성을, 답장 없으면 같은 방에 최근 30분 안에 올라온 최신 음성을 사용해요.\n" +
            "현재 방에서 허용된 기능만 동작해요."
    )

    fun adminHelpMessages(): List<String> = listOf(
        "[관리자 도움말 1/4 · 일반대화]\n코어라인 AI 연구소에서만 실행할 수 있어요.\n\n" +
            "• 헤이봇 대화 시작\n  허용방에서 호출어 없는 일반대화를 켜요.\n" +
            "• 헤이봇 대화 상태\n  ON/OFF, 적용 방, 안전회로, 현재 엔진을 확인해요.\n" +
            "• 헤이봇 대화 종료\n  일반대화를 끄고 ‘헤이봇’ 호출 방식만 유지해요.",
        "[관리자 도움말 2/4 · 응답 엔진]\n" +
            "• 헤이봇 대화 기본\n  Android 자체 GLM을 응답 엔진으로 사용해요.\n" +
            "• 헤이봇 대화 코덱스\n  Codex 프록시를 응답 엔진으로 사용해요.\n" +
            "• 헤이봇 대화 그록\n  Grok 프록시를 응답 엔진으로 사용해요.\n\n" +
            "엔진 변경은 호출어·일반대화 모두에 전역 적용돼요.",
        "[관리자 도움말 3/4 · 운영/방 권한]\n" +
            "• 헤이봇 상태 / 설정 보기\n" +
            "• 헤이봇 최근 진단 [R번호]\n" +
            "• 헤이봇 전체 기억 초기화\n" +
            "• 헤이봇 사용자 기억 초기화 <user_id>\n" +
            "• 헤이봇 자체진단 [빠른|통합|기기|카나리]\n" +
            "• 헤이봇 방 목록 / 방 상태 <R번호>\n" +
            "• 헤이봇 <기능> 허용|불허용 <R번호>\n" +
            "  기능: 텍스트, 일반대화, 이미지, 영상, 유튜브, 펜브러쉬, 이미지분석, 음성, 음성자동\n" +
            "• 헤이봇 방 적용 <코드> / 방 취소",
        "[관리자 도움말 4/4 · 음성 권한]\n" +
            "• 헤이봇 음성 허용|불허용 <R번호>\n" +
            "  사용자가 명령으로 STT·요약할 권한이에요.\n" +
            "• 헤이봇 음성자동 허용|불허용 <R번호>\n" +
            "  파일 전송 즉시 자동 분석할 권한이에요.\n" +
            "• 헤이봇 방 적용 <확인코드>\n" +
            "변경 전 ‘헤이봇 방 상태 <R번호>’로 현재 상태를 확인하세요. 음성자동은 텍스트·음성이 모두 허용돼야 해요."
    )

    private fun requireSkill(id: String): HeybotSkillDefinition = skills.first { it.id == id }

    private fun skill(
        id: String,
        displayName: String,
        aliases: Set<String>,
        examples: List<String>,
        description: String,
        capability: RoomCapability,
        execution: SkillExecutionKind,
        controls: Set<SkillAsyncControl> = emptySet()
    ) = HeybotSkillDefinition(
        id, displayName, aliases, examples, description,
        SkillAudience.USER, capability, execution, controls
    )

    private fun adminSkill(
        id: String,
        displayName: String,
        aliases: Set<String>,
        examples: List<String>,
        description: String
    ) = HeybotSkillDefinition(
        id, displayName, aliases, examples, description,
        SkillAudience.ADMIN, null, SkillExecutionKind.LOCAL
    )

    private const val MAX_RENDER_CHARS = 480
}
