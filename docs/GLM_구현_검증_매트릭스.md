# Iris GLM 자동응답 구현·검증 매트릭스

작성일: 2026-07-24  
대상: PD20의 root `app_process` Iris 빌드

이 문서는 개발 계획의 각 완료 조건을 코드·테스트·실기기 증거와 연결한다. API Key나 대화 원문은 기록하지 않는다.

## 완료된 구현 및 증거

| 요구사항 | 구현 위치 | 검증 증거 | 상태 |
|---|---|---|---|
| 기본값은 GLM 비활성 | `GlmSettings.load()` | `GlmSettingsTest`, PD20 재시작 로그 `GLM auto-reply disabled` | 완료 |
| 비밀은 런타임 root 파일에서만 읽기 | `GlmSettings.authorizationHeader()` | 설정 JSON·APK·소스에 키를 넣지 않는 구조, 기동 스크립트의 메타데이터 검사 | 완료 |
| Z.AI Chat Completion 요청 | `GlmClient` | MockWebServer에서 endpoint, Bearer 헤더, `stream=true`, SSE 청크 병합, `thinking.clear_thinking=true` 확인 | 완료 |
| 모델·허용 방·호출어 고정 | `GlmSettings`, `scripts/start_iris_glm_pd20.sh` | 주 모델 `glm-4.5-flash`, `헤이봇`, 실제 chat ID만 설정 | 완료 |
| 수신 메시지 필터 | `GlmAutoReplyHandler` | 텍스트 타입, 허용 방, 자기 메시지, 호출어, 빈 질의, 중복을 단위 테스트 | 완료 |
| 대화 문맥·출력 정리 | `GlmAutoReplyHandler` | 방별 4턴/30분, `<think>`, 코드펜스, 480자 제한 단위 테스트 | 완료 |
| 오류 무응답·대체 처리 | `GlmClient`, `GlmAutoReplyHandler` | 401/403/429/5xx/네트워크·타임아웃 분류, 429 제한 재시도, 주 모델의 429/시간 초과 시 대체 모델 1회 시도, 최종 실패 시 카카오 발신 0건 단위 테스트 | 완료 |
| 기존 Iris 텍스트 발신 | `Replier.sendMessage()` | PD20 `/reply` 실발신 및 자체 로그 확인 | 완료 |
| 기존 Iris 이미지 발신 | `Replier.sendPhoto()` | PD20 `/reply` 이미지 후 자체 `type=2` 로그 1건 확인 | 완료 |
| DBObserver·WebSocket·Webhook | `ObserverHelper` | PD20 텍스트 이벤트가 WebSocket과 ADB reverse 경유 임시 로컬 Webhook에 모두 수신됨 | 완료 |
| 안전한 기동·롤백 | `scripts/start_iris_glm_pd20.sh`, 운영 문서 | 토큰 미주입 시 배포·기동 전에 안전 중단, GLM 비활성 재기동 성공 | 완료 |
| 빌드 품질 | `vendor/Iris` Gradle | `:app:testDebugUnitTest :app:assembleRelease` 성공, 단위 테스트 26개 성공 | 완료 |

## 실 GLM 호출 및 발신 E2E 기록

- 비봇 계정의 허용 방 호출(log ID `1290`)을 PD20에서 확인했다.
- 당시 임시 진단 빌드로 그 로그만 한 번 재처리했다. 검증 후 과거 로그 재처리 기능과 환경 변수는 현재 운영 소스에서 제거했다.
- 당시 주 모델 `glm-4.7-flash`가 사용 불가 상태였고, 런타임 로그에 설정된 대체 모델 시도가 기록됐다.
- 대체 모델 `glm-4.5-flash`의 최종 텍스트가 기존 Iris `Replier.sendMessage()` 경로로 정확히 1건 발신됐다. 카카오 DB에는 봇 ID·`isMine=true`·텍스트 type의 새 발신 log ID `1291`이 확인됐다.
- 이후 표준 기동 스크립트로 재기동했으며, `GLM auto-reply enabled`, `Initial lastLogId: 1291`, `/config` 헬스 체크를 확인했다. 현재 소스는 과거 메시지를 재처리하지 않는다.
- 앞선 실기기 검증에서 주 모델의 HTTP 429도 확인했으며, 그때는 오류 텍스트가 카카오톡에 발신되지 않고 Iris가 계속 실행됐다.
- 이후 PD20의 동일 토큰으로 짧은 SSE 요청을 2회 측정한 결과 `glm-4.5-flash`만 정상 응답했고 평균 1.19초였다. 현재 표준 운영 스크립트의 주 모델을 `glm-4.5-flash`로 변경했으며, 대체 모델은 지정하지 않았다.

## 배포 식별값

| 항목 | 값 |
|---|---|
| Iris 기준 upstream commit | `ee1dc978ec465df11642596e40f74caff497301d` |
| GLM 작업 브랜치 | `feature/glm-autoreply` |
| release APK SHA-256 | `b8490f268979f5a8e13da8fb9702a207029233e42536342a78c2ac415ef13c37` |
| PD20 GLM APK 경로 | `/data/local/tmp/Iris-glm.apk` |

## 후속 권장 점검

- 호출어 없음·비허용 방·잘못된 토큰·네트워크 단절은 실제 운영 방이 아닌 별도 테스트 방에서 추가 점검한다.
- 장기 API Key 대신 Token Broker가 공급하는 짧은 수명의 토큰으로 교체한다.
