# 헤이봇 개발계획 인덱스

갱신 기준: `2026-07-26 15:35 KST`

여러 개발계획의 미체크 항목을 모두 독립 backlog로 취급하지 않는다. 뒤 계획이
앞 계획의 결정을 대체하거나 실제 구현이 다른 계획에서 완료된 경우 이 문서를
정본 인덱스로 사용한다.

## 문서 상태

| 문서 | 주제 | 상태 | 처리 원칙 |
|---|---|---|---|
| `implement_20260724_220139.md` | Iris Z.AI GLM 최초 이식 | 참조 | 기본 구현 완료. 남은 회귀 검증은 P1·운영 계획에서 관리 |
| `implement_20260725_075640.md` | 이미지·Codex 프록시 | 구현 완료/E2E·soak 잔여 | 이미지 파이프라인 구현 완료. 다중 방·물리 운영 검증은 `112823`에서 통합 관리 |
| `implement_20260725_083816.md` | Android GLM P1 안정성 | 구현 완료/E2E 참조 | Phase 0~6 구현 완료. 다중 방 P1 E2E는 `112823`의 통합 시나리오만 실행 |
| `implement_20260725_084850.md` | 프록시 인증·전송·운영 경계 | 부분 흡수 | 방 소유권까지 구현 완료. fake-ADB·물리 운영 검증만 유지 |
| `implement_20260725_095558.md` | 사용자별 일반대화 세션 | 대체됨 | 초기 조사 초안. 사용자별 문맥 구현 정본은 `125051`에서 관리 |
| `implement_20260725_100224.md` | 중앙 관리자·전역 일반대화 | 구현 완료/E2E 참조 | control room·전역 mode 구현 완료. 실사용 명령 검증은 `112823`에서 통합 관리 |
| `implement_20260725_102332.md` | 응답 안전·방/사용자 정책·circuit breaker | 구현 완료/E2E 참조 | Phase 1~3 구현 완료. sanitizer·circuit 실기기 검증은 `112823`에서 통합 관리 |
| `implement_20260725_112823.md` | 통합 마감·watchdog 자동화 | 활성/통합 E2E 정본 | P0/P1 자동 검증과 PD20 재배포 완료. 외부 카카오·물리 E2E의 단일 정본 |
| `implement_20260725_121224.md` | 중앙 방 capability 제어 | 구현·배포 완료/우선 자동검증·hardening·E2E 잔여 | capability별 stale-path 자동 회귀까지 완료. policy-file hardening과 실사용 검증이 남음 |
| `implement_20260725_122004.md` | Iris HTTP API P0 보안 차단 | 구현 완료/PD20 적용 | HTTP API 기본 OFF·인증·3000 forward 제거를 배포함. 기능 E2E는 `112823`에 통합 |
| `implement_20260725_125051.md` | 사용자별 일반대화 문맥 | 구현 완료/E2E 참조 | 공통 `(chatId,userId)` 문맥과 `WAIT` 임시 발화 구현 완료. 연속 대화 E2E는 `112823`에 통합 |
| `implement_20260725_194129.md` | Grok CLI OAuth 비디오 프록시 | R01 운영 활성화/카카오 E2E 대기 | `헤이봇 영상 <설명>`·VIDEO 권한·proxy-grok/video를 코어라인 AI 연구소(R01)에 배포했다. 첫 native Kakao delivery와 비용·사용량 관측만 남음 |
| `implement_20260725_210700.md` | 펜브러쉬 드로우 영상 | R01 운영 활성화/카카오 E2E 대기 | Grok 영상과 분리된 `proxy-draw`·`proxy-brush`·`PEN_BRUSH` 권한과 정밀 채색 renderer를 코어라인 AI 연구소(R01)에 배포했다. 첫 imagegen·native delivery 관측만 남음 |
| `implement_20260726_102712.md` | GLM/Codex/Grok 대화 엔진 선택 | Phase 0~4 구현·PD20 Codex 적용/Phase 5 E2E 잔여 | GLM을 기본으로 유지하면서, 코어라인 AI 연구소 관리자만 `헤이봇 대화 기본/코덱스/그록`으로 모든 허용방의 호출어·일반대화 응답 엔진을 전환한다. manager readiness, 전체 proxy 회귀, Android test/release build와 Codex text smoke는 통과했으며 PD20의 Android 10초 read timeout 수정·재배포까지 완료했다. 외부 계정의 실제 Kakao 응답 E2E가 남아 있다. |
| `implement_20260726_141425.md` | Iris Android package·vendor 디렉터리 rename | 구현·PD20 검증 완료/카카오 외부 E2E 대기 | `party.qwer.iris`를 `ai.coreline.heybot`으로 namespace·applicationId·Kotlin package·app_process 진입점으로 변경하고, 소스 디렉터리를 `vendor/Iris`에서 `vendor/android`로 정리했다. 빌드 경로·스크립트·문서 참조를 갱신했으며 release build, PD20 startup/readiness와 self-test QUICK을 통과했다. root 전용 운영 파일·KakaoTalk 데이터는 보존했다. |
| `implement_20260726_145547.md` | Android 자체진단·통합 self-test | 구현·PD20 QUICK/INTEGRATION/DEVICE 검증 완료/실제 CANARY 승인 대기 | `헤이봇 자체진단`으로 QUICK·INTEGRATION·DEVICE·CANARY를 분리했다. PD20에서 QUICK·INTEGRATION·DEVICE는 PASS, CANARY는 guard에 따라 WARN/SKIP이며 실제 provider 생성·카카오톡 전송은 실행하지 않았다. |

## 통합 실행 순서

1. 코어라인 AI 연구소(R01)에서 `헤이봇 영상 <설명>`의 첫 native Kakao delivery와 Grok 사용량을 관측한다. 다른 방의 VIDEO 권한은 불허용을 유지한다. (`194129` Phase 5)
2. 코어라인 AI 연구소(R01)에서 `헤이봇 펜브러쉬 <설명>`의 첫 imagegen·native Kakao delivery를 관측한다. 다른 방의 PEN_BRUSH 권한은 불허용을 유지한다. (`210700` Phase 5)
3. 비봇 계정으로 호출어·관리자·일반대화·사용자별 문맥·이미지 흐름을 하나의 제한 E2E 매트릭스로 검증한다. (`112823` Phase 4)
4. sanitizer/circuit, 방 capability preview→apply→복구, FIFO·rate-limit·기억 복원을 비공개 시험 방에서 검증한다. (`112823` Phase 4)
5. Mac sleep/wake, USB 재연결, 24시간 soak를 수행한다. (`075640`/`084850`/`112823`)
6. Codex/Grok CLI text-only capability를 확인한 뒤 `proxy-conversation`과 Android 전역 대화 엔진 선택을 GLM 기본값으로 단계 적용한다. (`20260726_102712`)
7. Iris Android package rename을 수행하고 새 `ai.coreline.heybot.Main`으로 PD20 재배포·기동 검증한다. (`20260726_141425`)
8. Android 자체진단 QUICK/INTEGRATION/DEVICE를 구현·PD20 검증한 뒤 CANARY를 별도 승인·시험방에서 제한 검증한다. (`20260726_145547`)

외부 비봇 카카오 계정이 필요한 단계는 자동 단위 테스트와 분리한다. 해당 E2E가
대기 중이어도 이미 정의된 fail-closed 코드와 자동 테스트 구현은 계속할 수 있지만,
실사용 완료로 표시하지 않는다.

## 현재 자동 검증 기준

- Iris Android JVM test: `135`개 통과, 실패 `0` (debug/release 동일)
- Iris release APK build: 통과
- Node server test: `proxy-manager` 9개, `proxy-image` 6개, `proxy-video` 2개, `proxy-draw` 3개, `proxy-brush` 2개, `proxy-codex` 11개, `proxy-grok` 4개, `proxy-conversation` 1개 통과 및 clean exit
- 단일 스택 검증: `vendor/server/scripts/self-test-stack.sh`가 readiness·전체 proxy test·Android `test assembleRelease`를 한 번에 통과
- PD20: room capability catalog `4개`, 현재 bootstrap상 text·일반대화·이미지 동적 허용은 각 `3개` (`R02` 전체 불허용)
- PD20 startup: GLM·일반대화 정책·대화 기억·room capability 준비와 5초 뒤 Iris process 지속 실행, Iris HTTP API 기본 비활성화 확인
- 이미지 proxy reverse: `tcp:4340` 유지 확인
- Android self-test: PD20 QUICK `PASS` 6/6, INTEGRATION `PASS` 7/7, DEVICE `PASS` 7/7, CANARY `WARN` (guard `SKIP`)

## 현재 남은 검증

- Grok video 기능은 2026-07-26에 registry·launchd·ADB reverse·PD20 Iris를 R01 전용으로 활성화했다. 서비스 readiness는 통과했고, 실제 native Kakao delivery와 계정 사용량 관측이 남음
- 펜브러쉬 드로우 영상은 2026-07-26에 registry·launchd·ADB reverse·PD20 Iris를 R01 전용으로 활성화했다. 서비스 readiness는 통과했고, 실제 imagegen·native Kakao delivery 관측이 남음
- 외부 비봇 계정으로 호출어 텍스트, 관리자 start/status/stop, 일반대화 연속성·무혼입 확인
- 동적 허용 3개 방의 이미지 FIFO·원래 방 전달·GLM 병행, sanitizer/circuit, policy preview→apply→복구 제한 E2E
- Mac sleep/wake, 물리 USB 재연결, 24시간 soak

## 2026-07-25 정합성 판정

| 판정 | 문서 | 정리 결과 |
|---|---|---|
| 역사 참조 | `220139`, `084850`, `095558` | 미체크 체크박스는 신규 backlog가 아니다. 각각 후속 계획 또는 보류 설계의 근거로만 유지한다. |
| 구현 완료·검증 중복 제거 | `075640`, `083816`, `100224`, `102332`, `122004`, `125051` | 구현·배포 사실은 각 문서에 보존하고, 외부 카카오/물리 검증은 `112823` Phase 4 하나로 통합한다. |
| 구현·우선 자동검증 완료 | `121224` | image capability code와 delivery/retry·독립 capability 조합 자동 회귀가 완료됐다. policy-file hardening과 실사용 검증은 남는다. |
| 통합 실행 정본 | `112823` | 자동 회귀·PD20 readiness는 완료됐다. 외부 카카오·물리 E2E만 남는다. |
| R01 운영 활성화·delivery 관측 | `194129` | video package, registry, launchd, ADB reverse, Iris VIDEO coordinator를 R01 전용으로 활성화했다. ZDR 재시도·사용량·Kakao native delivery 관측을 계속한다. |
| R01 운영 활성화·delivery 관측 | `210700` | 별도 `draw` domain의 pen-brush renderer·권한·정밀 채색 보강을 R01 전용으로 활성화했다. 실제 imagegen·카카오 native delivery 관측을 계속한다. |

숫자 `3개 방`은 이전 문서의 당시 운영 범위 또는 현재 동적 허용 범위를 뜻한다. 현재 정적 관리 상한은 4개 방이며, 실제 입력 허용은 root 전용 room capability policy가 결정한다. HTTP API는 P0 보안 변경 후 기본 OFF이므로 이전 문서의 `/config` health 확인을 현재 기준으로 사용하지 않는다.

## 문서 관리 규칙

- 대체된 계획의 체크박스를 억지로 완료 처리하지 않는다.
- 흡수된 계획은 고유한 미구현 항목만 이 인덱스와 활성 계획으로 이전한다.
- 실기기·물리 조작·장시간 검증은 코드 완료와 별도 상태로 기록한다.
- 새로운 기능 계획은 이 인덱스에서 활성 문서와 실행 순서를 먼저 갱신한다.
