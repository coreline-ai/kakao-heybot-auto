# 헤이봇 보안 검토 보고서

- 검토일: 2026-07-25
- 대상: PD20 Android Iris, Mac mini의 `proxy-manager`/`proxy-image`/`proxy-codex`, 배포·watchdog 스크립트
- 방법: 소스 정적 검토, 로컬 런타임 비파괴 확인, Node 프로덕션 의존성 감사
- 제외: Kakao·Z.AI·Codex CLI 공급자 인프라 침투 테스트, 외부망 포트 스캔, 실제 메시지 전송·설정 변경

## 적용 상태 (2026-07-25 P0 완료)

- P0 보안 빌드와 운영 스크립트를 PD20에 배포했다. Iris HTTP 관리 API는
  `IRIS_HTTP_API_ENABLED=false` 기본값에서 시작하지 않으며, PD20 시작 로그에서
  `Iris HTTP API disabled`와 GLM scheduler 기동을 함께 확인했다.
- 기존 Mac의 `tcp:3000` ADB forward를 제거했다. 이전 launchd mirror가 오래된 watchdog을
  실행해 forward를 되살리는 것을 확인해, 현 watchdog으로 mirror·launchd를 재설치했다.
  한 watchdog 주기 이후에도 `tcp:3000` listener/forward는 없고 이미지 경로의
  `tcp:4340` reverse만 유지되는 것을 확인했다.
- config/startup log은 PD20 `/data/local/private`의 `root:root`, mode `600`으로 확인했다.
  `/aot`, `/query`, `/decrypt` route 제거, protected route Bearer 인증, config endpoint
  비활성화와 로그 redaction은 release APK에 포함됐다.
- 과거 AOT·대화가 노출됐을 가능성은 코드 변경으로 소급 해소되지 않는다. 운영자는 Kakao
  세션/AOT 재인증 필요성을 별도로 판단해야 한다. SEC-004~006은 여전히 후속 과제다.

## 요약 (수정 전 발견 기준)

**즉시 조치가 필요한 Critical 1건**을 확인했다. 현재 실행 중인 Iris HTTP API(호스트의 `127.0.0.1:3000`으로 ADB forward됨)가 인증 없이 Kakao 대화, AOT, 발신, 복호화, 설정 변경 기능을 제공한다. 본 검토에서 응답 본문을 저장하지 않고 상태만 확인했지만, `/config`, `/dashboard/status`, `/aot`가 모두 인증 없이 `HTTP 200`을 반환했다.

그 외에는 root 프로세스의 설정 파일 위치, 평문 대화 로그, 호출어 범위 확대, 프록시 헤더 신뢰 경계, HTTP 자원 제한을 개선해야 한다. 반대로 신규 프록시 계층의 loopback 제한·비밀 분리·입력 검증·파일 검증은 양호하다.

| 심각도 | 건수 | 즉시 우선순위 |
|---|---:|---|
| Critical | 0 (과거 1) | P0 HTTP API 차단·인증 적용 완료 |
| High | 0 (과거 1) | P0 private config 경로 적용 완료 |
| Medium | 1 | 호출어 정책(SEC-004) |
| Low | 2 | 프록시 헤더, HTTP timeout |

## 확인한 긍정 통제

- 세 Node 프록시는 loopback host만 허용하고, registry도 loopback HTTP target만 수용한다.
- 관리자·gateway·내부 caller 비밀을 분리하고 timing-safe 비교를 사용한다.
- 요청 본문 크기·허용 필드·ID 형식·큐 한도를 검증하며 이미지 job은 `chatId`로 재확인한다.
- Codex runner는 사용자 prompt를 shell 인자가 아닌 stdin으로 전달하고, artifact의 symlink/경로/PNG 크기·형식을 검증한다.
- proxy secret bootstrap은 runtime 디렉터리 `700`, secret 파일 `600`을 사용한다.
- GLM API key는 Iris JSON config가 아니라 별도 파일에서 읽고 GLM endpoint는 HTTPS만 허용한다.
- `npm audit --omit=dev --json` 결과: proxy-manager, proxy-image, proxy-codex의 production 의존성 취약점 0건.
- Android 단위 테스트: debug/release 합계 200건, failures/errors 0건 (`./gradlew test` 성공).

## 발견 사항

### SEC-001 — Critical — 인증 없는 Iris 고권한 HTTP API가 실제 노출됨

**상태: P0 적용 완료.** 아래 내용은 수정 전 발견 근거다. 현재 기본 배포에서는 HTTP
server가 시작하지 않고, 영구 `tcp:3000` forward도 없다.

- **위치**
  - `vendor/android/app/src/main/java/party/qwer/iris/IrisServer.kt:51-52,72-230`
  - `scripts/start_iris_glm_pd20.sh:155-160`
- **근거**
  - 서버에 인증 미들웨어나 route authorization이 없다.
  - `/dashboard/status`는 최근 복호화 대화를, `/aot`는 Kakao AOT 정보를, `/reply`는 메시지·이미지·비디오 발신을, `/query`와 `/decrypt`는 DB/복호화 기능을 제공한다.
  - `/config/{name}`은 endpoint·전송 속도·포트를 변경한다. endpoint는 이후 모든 수신 대화를 외부 URL로 POST하는 경로와 연결된다.
  - 배포 스크립트가 `adb forward tcp:3000 tcp:3000`을 유지한다. 로컬 확인에서 macOS `127.0.0.1:3000` listener가 존재했고, Authorization 없이 `/config`, `/dashboard/status`, `/aot`는 각각 HTTP 200을 반환했다. 본 확인은 본문을 버리고 상태 코드만 확인했다.
- **영향**
  - Mac의 다른 로컬 프로세스, ADB 권한 보유자, 또는 PD20 포트에 접근 가능한 주체가 대화·식별자·AOT를 읽고 봇으로 발신하거나 설정을 바꿀 수 있다.
  - `endpoint`를 바꾸면 이후 복호화된 대화와 attachment metadata가 공격자 URL로 전송될 수 있다.
  - PD20의 실제 LAN listen 범위는 이번 sandbox에서 ADB daemon을 직접 조회하지 못해 확정하지 않았다. 외부 인터페이스에서도 열려 있으면 영향은 즉시 원격 Critical이다.
- **권장 수정**
  1. P0: 인증 완료 전에는 `adb forward tcp:3000 tcp:3000` 자동 복원을 중지하거나 Iris HTTP 서버 자체를 중지한다.
  2. Iris에 인증을 기본 적용한다. `/health`만 최소 정보로 익명 허용하고, dashboard/config/aot/reply/query/decrypt/ws는 별도 admin bearer secret 또는 mTLS 터널을 요구한다.
  3. 서버 bind host를 명시적으로 loopback으로 고정하고, health check를 `/config` 대신 새 `/health`로 변경한다.
  4. `query`, `decrypt`, `aot`는 운영 HTTP surface에서 제거하거나 root 전용 local maintenance 도구로 분리한다. `config/endpoint`는 기본 비활성화하고 HTTPS allowlist를 적용한다.
- **완화책**
  - 수정 전 운영이 꼭 필요하면 ADB forward를 요청 시에만 열고, Mac 사용자 계정/ADB debugging 접근을 최소화한다. 이는 임시 조치이며 인증의 대체가 아니다.

### SEC-002 — High — root 서비스 설정이 `/data/local/tmp` 기본 경로에 저장됨

**상태: P0 적용 완료.** 기본 config path와 PD20 기동 환경을
`/data/local/private/iris-config.json`으로 전환했고 metadata 검증을 추가했다.

- **위치**
  - `vendor/android/app/src/main/java/party/qwer/iris/Configurable.kt:12-15,46-53`
  - `scripts/start_iris_glm_pd20.sh:130-146`
- **근거**
  - `IRIS_CONFIG_PATH` 미설정 시 root 프로세스가 `/data/local/tmp/config.json`을 읽고 쓴다.
  - 현재 시작 스크립트는 `IRIS_CONFIG_PATH`를 넘기지 않는다.
  - config에는 `webServerEndpoint`, HTTP port, 전송·DB polling 설정이 있으며 endpoint는 `ObserverHelper`의 외부 POST URL이 된다.
- **영향**
  - 일반적인 Android의 `/data/local/tmp`는 private secret 디렉터리가 아닌 staging 위치다. shell/ADB 접근 또는 파일 교체가 가능한 주체가 root 프로세스의 동작을 바꾸거나 대화 유출 endpoint를 심을 위험이 있다.
- **권장 수정**
  - `/data/local/private/iris-config.json`을 `root:root`, mode `600`으로 만들고 시작 시 `IRIS_CONFIG_PATH`를 강제 지정한다.
  - 시작 전에 소유자·mode·regular-file 여부를 검사하고, 불일치하면 fail closed 한다.
  - `Configurable`의 config 전문 출력도 제거한다.
- **완화책**
  - 비밀은 이미 별도 private 파일에 두고 있어 노출 범위는 제한적이지만, endpoint와 포트 설정은 민감한 제어 데이터로 취급해야 한다.

### SEC-003 — Medium — 복호화된 대화/외부 응답을 평문 로그에 기록

**상태: P0 적용 완료.** startup log을 private 경로로 이동하고 webhook 원문·response body
출력을 제거했다. 일반 DB/운영 접근 통제는 계속 필요하다.

- **위치**
  - `vendor/android/app/src/main/java/party/qwer/iris/ObserverHelper.kt:240-268`
  - `vendor/android/app/src/main/java/party/qwer/iris/Configurable.kt:34-35,48-51`
  - `scripts/start_iris_glm_pd20.sh:145-146`
- **근거**
  - webhook이 활성화되면 URL, 전체 `jsonData`(복호화 메시지·room·sender·raw attachment metadata), HTTP response body를 `println`으로 남긴다.
  - root Iris stdout/stderr는 `/data/local/tmp/iris-glm-startup.log`로 redirect된다.
- **영향**
  - 로그 접근자·백업·수집 도구가 대화 내용과 외부 서비스 응답을 볼 수 있다. SEC-001의 endpoint 변경과 조합되면 유출 흔적도 남는다.
- **권장 수정**
  - message, attachment, response body를 기록하지 않고 event ID·status·byte length·해시만 구조화 로그로 남긴다.
  - 운영 로그 경로를 `/data/local/private`로 옮기고 mode `600`, rotation/retention을 적용한다.
  - 예외 메시지를 HTTP 응답에 그대로 보내지 않는다 (`IrisServer.kt:61-68,210-212`).

### SEC-004 — Medium — 호출어가 문장 어디에 있어도 GLM 외부 호출로 승격됨

- **위치**: `vendor/android/app/src/main/java/party/qwer/iris/BotCommandRouter.kt:27-34`
- **근거**: 현재 working tree의 미커밋 변경은 선행 호출어 파싱에 실패해도 `message.contains(trigger)`이면 전체 메시지를 `GlmQuestion`으로 보낸다. 호출어만 입력해도 전체 원문을 GLM에 보낸다.
- **영향**
  - 공개방에서 일반 대화·인용문에 호출어가 포함될 때 의도하지 않은 Z.AI 전송, 비용, 대화 내용 외부 전송이 발생한다. rate limit은 총량을 줄일 뿐 호출 의도 검증은 하지 않는다.
- **권장 수정**
  - 기본 정책을 선행 호출어 + delimiter로 되돌리고, 빈 호출어는 help/무응답 중 하나로 명시한다.
  - 문장 내 호출을 제품 요구사항으로 유지한다면 room별 opt-in 및 명확한 문장 경계 규칙을 추가하고 별도 테스트를 둔다.
- **오탐 가능성**: 문장 내 호출을 의도적으로 지원하려는 변경일 수 있다. 그 경우에도 privacy/cost 동의 정책을 코드와 운영 문서에 명시해야 한다.

### SEC-005 — Low — manager가 호출자 제공 service-id 헤더를 다시 upstream에 전달

- **위치**: `vendor/server/proxy-manager/src/router/proxy.ts:40-48`
- **근거**: 코드가 `x-heybot-service-id: manager`를 설정한 뒤, 원 요청의 동일 헤더를 hop-by-hop 제외 목록에 넣지 않아 덮어쓸 수 있다.
- **영향**
  - 현재 image proxy는 이 헤더를 권한 판단에 사용하지 않아 즉시 우회는 확인되지 않았다. 다만 미래 proxy가 caller identity로 이 값을 신뢰하면 gateway 요청이 서비스 간 권한으로 혼동될 수 있다.
- **권장 수정**: `x-heybot-service-id`를 제거 대상에 추가하고 manager가 정한 값만 upstream으로 전달한다. caller identity는 인증된 서버 내부에서만 설정한다.

### SEC-006 — Low — native Node HTTP inbound timeout/limit가 명시되지 않음

- **위치**
  - `vendor/server/proxy-manager/src/http/server.ts:46`
  - `vendor/server/proxy-image/src/http/server.ts:95`
  - `vendor/server/proxy-codex/src/http/server.ts:138`
- **근거**: body size 제한은 있으나 `headersTimeout`, `requestTimeout`, `keepAliveTimeout`, `maxHeadersCount`를 명시하지 않아 Node 런타임 기본값에 의존한다.
- **영향**: loopback/ADB tunnel 접근자가 느린 요청을 다수 열어 worker와 socket을 장시간 점유할 수 있다.
- **권장 수정**: 세 서버에 공통 hardening 함수를 두고 보수적 header/request/keep-alive timeout, max headers 및 socket error handler를 설정한다. readiness/long file stream 요구와 함께 회귀 테스트한다.

## 조치 순서

1. **P0 즉시**: TCP 3000 forward 자동 복원을 중지하고 Iris HTTP API를 인증 전까지 비공개화한다. 현재 Kakao AOT가 노출 가능한 상태였으므로 Kakao 세션/AOT 재발급 가능 여부를 운영 절차로 확인한다.
2. **P0 구현**: Iris auth + loopback bind + `/health` 분리, 위험 endpoint 제거/관리 경로 분리, 보안 회귀 테스트 추가.
3. **P1**: private config 강제, message/response log redaction, endpoint HTTPS allowlist/기본 비활성화.
4. **P2**: 호출어 정책 확정, manager reserved header 차단, Node HTTP timeout 통일.
5. **P3 운영**: Android/Ktor/OkHttp 버전의 정기 dependency audit을 CI에 추가한다. 이번 Node production npm audit은 취약점 0건이었지만, Android 의존성 전용 CVE scanner는 아직 CI에 없다.

## 검증 메모

- 비파괴 런타임 확인은 HTTP status만 조회했고 응답 본문, AOT, 대화, 비밀은 저장하거나 출력하지 않았다.
- ADB daemon은 sandbox 제약으로 PD20의 `ss`/port binding을 직접 조회하지 못했다. Mac의 `127.0.0.1:3000` ADB listener와 익명 200 응답은 확인했다.
- P0 변경은 승인 후 구현·테스트·PD20 배포까지 완료했다. 선택적 HTTP admin API의
  enabled-mode end-to-end 검증은 실제 admin secret을 사용하므로, 필요 시 운영자가
  문서의 임시 forward 절차로 status-only 방식으로 수행한다.
