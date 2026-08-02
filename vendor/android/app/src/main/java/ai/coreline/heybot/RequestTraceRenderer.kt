package ai.coreline.heybot

object RequestTraceRenderer {
    fun render(trace: RequestTrace?, roomReference: String?): String {
        if (trace == null) return "최근 진단할 요청이 없어요."
        val room = roomReference ?: "관리방"
        val elapsed = (trace.updatedAtMillis - trace.startedAtMillis).coerceAtLeast(0L)
        val reasonCode = trace.reasonCode ?: trace.rootReasonCode
        val reason = reasonCode?.let { "\n사유 코드: $it" }.orEmpty()
        val engine = trace.engine?.let { "\n응답 엔진: $it" }.orEmpty()
        return (
            "최근 요청 진단 ${trace.traceId}\n" +
                "방: $room\n" +
                "종류: ${kindLabel(trace.kind)}\n" +
                "현재 단계: ${stageLabel(trace.stage)}$engine$reason\n" +
                "처리 시간: ${elapsed}ms"
            ).take(480)
    }

    private fun kindLabel(kind: RequestTraceKind): String = when (kind) {
        RequestTraceKind.UNKNOWN -> "분류 전"
        RequestTraceKind.WAKE_WORD -> "호출어 대화"
        RequestTraceKind.GENERAL_CONVERSATION -> "일반대화"
        RequestTraceKind.LOCAL_COMMAND -> "로컬 명령"
        RequestTraceKind.IMAGE -> "이미지 생성"
        RequestTraceKind.VIDEO -> "영상 생성"
        RequestTraceKind.PEN_BRUSH -> "펜브러쉬"
        RequestTraceKind.VISION -> "이미지 분석"
        RequestTraceKind.VISION_FOLLOW_UP -> "이미지 후속 대화"
        RequestTraceKind.DIAGNOSTICS -> "진단 명령"
    }

    private fun stageLabel(stage: RequestTraceStage): String = when (stage) {
        RequestTraceStage.RECEIVED -> "DB 수신"
        RequestTraceStage.CLASSIFIED -> "명령 분류"
        RequestTraceStage.MODE_DISABLED -> "일반대화 꺼짐"
        RequestTraceStage.POLICY_ALLOWED -> "방·사용자 정책 통과"
        RequestTraceStage.POLICY_DENIED -> "정책에서 제외"
        RequestTraceStage.ADMITTED -> "처리 승인"
        RequestTraceStage.DUPLICATE -> "중복 요청 제외"
        RequestTraceStage.RATE_LIMITED -> "요청 횟수 제한"
        RequestTraceStage.QUEUE_FULL -> "처리 큐 초과"
        RequestTraceStage.PROVIDER_STARTED -> "응답 엔진 호출"
        RequestTraceStage.PROVIDER_SUCCEEDED -> "응답 엔진 완료"
        RequestTraceStage.PROVIDER_FAILED -> "응답 엔진 실패"
        RequestTraceStage.SAFETY_PASSED -> "안전 검사 통과"
        RequestTraceStage.SAFETY_BLOCKED -> "안전 검사 차단"
        RequestTraceStage.ENQUEUED -> "카카오 전송 큐 등록"
        RequestTraceStage.DISPATCHED -> "카카오 서비스 호출"
        RequestTraceStage.DISPATCH_FAILED -> "카카오 서비스 호출 실패"
        RequestTraceStage.DB_CONFIRMED -> "카카오 DB 전송 확인"
        RequestTraceStage.DB_CONFIRMED_LATE -> "카카오 DB 지연 확인"
        RequestTraceStage.UNCONFIRMED -> "카카오 DB 전송 미확인"
        RequestTraceStage.FINISHED -> "처리 완료"
    }
}
