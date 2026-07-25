# Codex CLI 이미지 프록시 도입 상세 분석

- 작성일: 2026-07-25
- old-bot 기준 커밋: `ff24ff55755fee376dd6df21eba17520c640e61a`
- 현재 프로젝트: Iris + PD20 + Z.AI GLM 자동응답
- 관리자 패키지: `new-bot/vendor/server/proxy-manager`
- 이미지 패키지: `new-bot/vendor/server/proxy-image`
- Codex 실행 패키지: `new-bot/vendor/server/proxy-codex`

## 1. 결론

old-bot의 이미지 프록시는 다음 기능을 한 서비스에 포함한다.

- Android HTTP 클라이언트
- shared-secret 인증
- Codex CLI image generation 실행
- 직렬 생성 큐
- 중복 요청 합류
- timeout·취소·프로세스 트리 종료
- PNG 출력 탐색·복사·기초 검증
- 로컬 이미지/OG 게시 서버
- Cloudflare Quick Tunnel
- 공개 URL 재검증
- launchd·watchdog·self-test

현재 Iris 프로젝트에는 이 구조를 그대로 복제할 필요가 없다. Iris는 Base64/파일 기반 이미지 전송을 이미 지원하므로, 기본 경로는 다음이 적합하다.

```text
PD20 Iris -> proxy-manager -> proxy-image -> proxy-codex -> Codex CLI
PD20 Iris <- manager <- validated PNG <- proxy-image <- raw artifact <- proxy-codex
PD20 Iris -> Kakao ACTION_SEND_MULTIPLE
```

책임은 다음과 같이 분리한다.

- `proxy-manager`: PD20 단일 진입점, 외부 route 분기, registry, 상태·테스트·수명주기 관리
- `proxy-image`: 이미지 job, 이미지 요청 정책, controlled input, 픽셀 QC, 최종 PNG
- `proxy-codex`: Codex CLI 인증·capability·전역 queue·격리 workspace·process·원시 artifact

이후 `proxy-audio`, `proxy-document` 등이 Codex가 필요하면 Codex CLI를 직접 실행하지 않고 `proxy-codex`의 허용된 내부 capability를 호출한다.

Cloudflare 공개 링크와 OG 페이지는 직접 이미지 전송에 필요하지 않으므로 기본 OFF로 분리한다.

## 2. old-bot 프록시 연결 방식

### 2.1 Android에서 Mac 프록시로 연결

old-bot Android 앱은 Retrofit/OkHttp를 사용한다.

- 이미지 프록시: 기본 `http://127.0.0.1:4347`
- 인증 헤더: `X-CBot-Proxy-Auth`
- timeout: 이미지 생성 전용 장시간 timeout
- 요청: `POST /api/v1/generate`
- 응답: `postUrl`, `imageUrl`, QC metadata

개발 환경에서는 다음 ADB reverse가 Android의 loopback 요청을 Mac으로 전달한다.

```bash
adb -s <serial> reverse tcp:4347 tcp:4347
```

LAN 모드에서는 Mac의 사설 IP로 직접 연결한다. 이 경우 Android network security 설정과 LAN 노출 보안이 추가로 필요하다.

### 2.2 old-bot 요청 흐름

```text
카카오 알림
 -> ImageCommandIntentParser
 -> 방별 in-flight guard
 -> "이미지 생성 중" 텍스트 답장
 -> ImageProxyGateway
 -> POST :4347/api/v1/generate
 -> Codex CLI 생성
 -> 공개 URL 검증
 -> 카카오에 "페이지: URL" 텍스트 전송
```

old-bot은 이미지를 카카오에 직접 첨부하지 않고, Cloudflare 공개 OG 페이지 링크를 보낸다.

## 3. old-bot Codex CLI 실행 방식

### 3.1 실행 명령 계약

확인된 핵심 인자는 다음과 같다.

```text
codex exec
  --model <model>
  --enable image_generation
  --sandbox workspace-write
  -C <workdir>
  --skip-git-repo-check
  --output-last-message <result-json>
  <controlled prompt>
```

Codex에게 imagegen skill로 PNG 한 장을 생성하고 지정 경로에 저장하도록 요청한다.

### 3.2 출력 탐색 순서

1. 지정 output path 확인
2. `--output-last-message` JSON의 `imagePath` 확인
3. `~/.codex/generated_images`에서 실행 시작 시각 이후 최신 PNG 검색
4. 최종 PNG를 publish directory로 복사

### 3.3 프로세스 관리

- detached process group 실행
- timeout 시 `SIGTERM`
- 5초 후에도 종료되지 않으면 `SIGKILL`
- PNG 크기가 두 번 연속 같고 PNG signature가 맞으면 조기 성공
- 클라이언트가 모두 끊기면 실행 취소

### 3.4 readiness

- `codex --version`
- `codex exec --enable image_generation --help`
- Codex home 존재
- output scan directory 존재
- OG server 상태
- public tunnel 상태

## 4. old-bot에서 유지할 패턴

### 4.1 반드시 유지

- Codex CLI capability probe
- 한 번에 하나의 이미지 생성
- queue backlog·timeout·취소 지표
- process group 단위 종료
- 동일 요청 idempotency
- output path confinement
- PNG signature·파일 크기 검증
- health/readiness 분리
- 생성 self-test와 비생성 readiness test 분리
- 오래된 artifact 자동 삭제

### 4.2 개선해서 유지

| old-bot | new-bot 권장 |
|---|---|
| 긴 HTTP 요청을 생성 완료까지 유지 | 비동기 Job API + polling |
| 클라이언트 연결 종료 시 작업 취소 | 명시적 cancel 전까지 서버 작업 유지 |
| 프로젝트 root에서 Codex 실행 | job별 격리 workspace |
| 전체 process env 상속 | 최소 환경변수 allowlist |
| public URL 필수 | 인증된 바이너리 다운로드 기본 |
| roomName을 요청 키로 사용 | 문자열 `chatId` + `logId` |
| PNG signature와 크기만 검증 | decode·dimensions·entropy·contrast 검증 |
| 내부 오류 문자열을 응답에 포함 | 외부 error code와 내부 로그 분리 |
| request body 무제한 | 32KB 제한과 prompt 길이 제한 |
| 일반 문자열 secret 비교 | loopback + timing-safe 비교 |

## 5. old-bot 구조의 주요 위험

### 5.1 Codex workdir가 너무 넓음

old-bot은 전체 프로젝트 root를 `workspace-write`로 연다. 사용자 프롬프트가 간접적으로 worker 지시를 교란하면 프로젝트 파일에 접근하거나 수정할 가능성이 있다.

권장:

- `runtime/jobs/<jobId>/workspace`만 Codex workdir로 사용
- output path도 job workspace 내부
- 프로젝트 소스, `.env.local`, 다른 artifact를 mount/복사하지 않음

### 5.2 자식 프로세스에 secret 환경 전체 전달

old-bot은 서버의 `process.env`를 Codex child에 거의 그대로 전달한다. 프록시 shared secret 같은 값이 child environment로 넘어갈 수 있다.

권장 child env:

```text
PATH
HOME
CODEX_HOME
TMPDIR
LANG
LC_ALL
SSL_CERT_FILE (필요한 경우)
```

서버 API secret과 다른 서비스 secret은 전달하지 않는다.

### 5.3 실제 픽셀 QC 부족

old-bot의 현재 Codex backend는 PNG signature와 최소 파일 크기는 확인하지만, 응답의 black/flat/low-contrast 필드는 실질적으로 계산하지 않는다.

권장 QC:

- PNG decode 성공
- 폭·높이 제한
- 최대 바이트 제한
- 알파만 있는 이미지 차단
- 평균 밝기·표준편차
- 색상 범위·entropy
- 검정 화면·단색 화면·극저대비 차단
- SHA-256 기록

### 5.4 공개 Tunnel 의존성

Quick Tunnel은 URL 변경, Cloudflare 429, DNS 지연이 발생한다. 카카오 직접 첨부가 가능한 현재 프로젝트에서는 기본 경로로 둘 이유가 없다.

## 6. 현재 환경 확인 결과

| 항목 | 확인 결과 |
|---|---|
| Node.js | `v24.13.1` |
| npm | `11.8.0` |
| 전역 Codex 경로 | `/usr/local/bin/codex` |
| 전역 Codex package | `@openai/codex 0.73.0` |
| Codex auth 파일 | 존재 |
| generated_images | 존재 |
| 비대화식 `codex --version` | 이번 세션에서 timeout/SIGKILL |
| image generation flag | 현재 CLI에서 검증 실패 |
| cloudflared | 설치됨 |
| `127.0.0.1:4340` manager | 미실행 |
| `127.0.0.1:4347` image | 미실행 |
| `127.0.0.1:4348` codex | 미실행 |
| PD20 reverse `4340` | 미설정 |

판정:

- Node 서버 구현 조건은 충족한다.
- Codex CLI는 설치 흔적과 auth 파일은 있지만 현재 비대화식 worker로 사용할 수 있는 상태가 확인되지 않았다.
- 구현 전 CLI 업그레이드/재설치와 Terminal·launchd 양쪽 capability 검증이 선행되어야 한다.
- 인증 파일 존재는 유효한 로그인과 사용 가능량을 보장하지 않는다.

## 7. 권장 연결 방식 비교

| 방식 | 장점 | 단점 | 판단 |
|---|---|---|---|
| 공개 OG URL 전송 | Android 변경이 적음 | Tunnel·공개 노출·429·URL 변경 | 선택 기능 |
| JSON Base64 응답 | 구현이 단순 | 응답 크기와 메모리 33% 이상 증가 | 비권장 |
| 서버가 Iris `/reply` 호출 | PD20 polling 불필요 | 서버가 카카오 전송까지 결합, Iris API 인증 문제 | 비권장 |
| PD20이 이미지 프록시에 직접 연결 | 구조가 단순 | 프록시 추가마다 포트·인증·ADB 변경 | 비권장 |
| PD20이 manager를 통해 PNG 다운로드 | 단일 포트·기능 분기·독립 장애 | manager 구현 필요 | **권장** |

## 8. 권장 API

아래 데이터 API는 PD20이 `proxy-manager:4340`으로 호출하고, manager가 `/v1/image` prefix를 `proxy-image:4347`에 동일 URI로 전달한다.

18자리 `chat_id`는 JavaScript 안전 정수 범위를 넘을 수 있다. 서버 JSON에서는 반드시 문자열로 전달한다.

### 8.1 작업 생성

```http
POST /v1/image/jobs
Authorization: Bearer <shared-secret>
Idempotency-Key: iris-<logId>
Content-Type: application/json
```

```json
{
  "requestId": "iris-1314",
  "chatId": "18480337854645134",
  "userId": "444000000",
  "logId": "1314",
  "prompt": "새벽 호수 위의 작은 로봇 일러스트",
  "requestedAt": "2026-07-25T07:56:40+09:00"
}
```

```json
{
  "ok": true,
  "jobId": "01J...",
  "status": "queued",
  "queuePosition": 2,
  "pollAfterMs": 2000
}
```

전체 queue 또는 방별 queue가 설정 한도에 도달하면 각각 `IMAGE_QUEUE_FULL`, `ROOM_QUEUE_LIMIT` 오류와 재시도 정보를 반환한다. 생성 완료까지 이 HTTP 요청을 유지하지 않는다.

### 8.2 상태 조회

```http
GET /v1/image/jobs/<jobId>
Authorization: Bearer <shared-secret>
```

상태:

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `expired`

성공 응답에는 filesystem path 대신 아래만 노출한다.

```json
{
  "ok": true,
  "jobId": "01J...",
  "status": "succeeded",
  "contentType": "image/png",
  "bytes": 2458102,
  "sha256": "...",
  "width": 1024,
  "height": 1024,
  "downloadPath": "/v1/image/jobs/01J.../file"
}
```

### 8.3 이미지 다운로드

```http
GET /v1/image/jobs/<jobId>/file
Authorization: Bearer <shared-secret>
```

- `Content-Type: image/png`
- `Content-Length` 필수
- `X-Content-Type-Options: nosniff`
- 인증 필수

### 8.4 취소

```http
DELETE /v1/image/jobs/<jobId>
Authorization: Bearer <shared-secret>
```

### 8.5 상태 점검

```text
GET  /health                    # 프로세스 생존, 민감정보 없음
GET  /ready                     # image storage와 proxy-codex dependency readiness
POST /v1/self-test/readiness    # 생성 비용 없음
POST /v1/self-test/generate     # 실제 이미지 1장, 명시적 실행
```

### 8.6 proxy-codex 내부 API

`proxy-image`는 다음 API를 loopback에서 직접 호출한다.

```text
POST   /internal/v1/codex/jobs
GET    /internal/v1/codex/jobs/<jobId>
GET    /internal/v1/codex/jobs/<jobId>/artifacts/<artifactId>
DELETE /internal/v1/codex/jobs/<jobId>
```

초기 capability는 `image.generate`, 허용 caller는 `image`로 제한한다. 내부 요청은 이미지 input과 artifact 계약만 받을 수 있으며 shell command, argv, environment, workdir, output path는 받을 수 없다.

## 9. 권장 서버 내부 구조

```text
vendor/server/
├── proxy-manager/
│   ├── config/proxies.json
│   ├── src/
│   │   ├── registry/
│   │   ├── router/
│   │   ├── auth/
│   │   ├── health/
│   │   ├── test-runner/
│   │   └── lifecycle/
│   ├── scripts/
│   ├── test/
│   └── runtime/
├── proxy-image/
    ├── src/
    │   ├── config/
    │   ├── http/
    │   ├── auth/
    │   ├── jobs/
    │   ├── queue/
    │   ├── orchestration/
    │   ├── clients/codex/
    │   ├── images/
    │   ├── storage/
    │   └── observability/
    ├── scripts/
    ├── test/
    └── runtime/
└── proxy-codex/
    ├── config/capabilities.json
    ├── src/
    │   ├── auth/
    │   ├── capabilities/
    │   ├── jobs/
    │   ├── queue/
    │   ├── cli/
    │   ├── workspace/
    │   ├── artifacts/
    │   └── observability/
    ├── scripts/
    ├── test/
    └── runtime/
```

manager 원칙:

- PD20이 접근하는 유일한 포트는 `4340`
- `/v1/image`를 `127.0.0.1:4347`로 전달
- 데이터 응답은 buffer 전체 적재 대신 streaming
- manager가 job 상태나 artifact를 직접 소유하지 않음
- proxy별 health, readiness, circuit breaker를 독립 관리
- start·stop·restart는 별도 admin secret과 기본 OFF 정책
- `proxy-codex`는 `exposure: internal`로 등록하고 외부 route를 부여하지 않음

`proxy-image` 권장 저장:

- SQLite: image job, `codexJobId` 매핑, idempotency, 오류 코드, 시간
- 파일: `runtime/artifacts/<jobId>/image.png`
- retention: 기본 24시간

`proxy-codex` 권장 저장:

- SQLite: caller-scoped codex job, queue, 실행 상태
- workspace: `runtime/jobs/<jobId>/workspace`
- 원시 artifact: `runtime/artifacts/<jobId>/`
- 원시 artifact retention: 기본 1시간

## 10. 권장 실행 정책

| 정책 | 소유 패키지 | 기본값 |
|---|---|---:|
| Codex 전체 생성 동시성 | proxy-codex | 1 |
| Codex 전체 queue 상한 | proxy-codex | 8 |
| Codex queue wait timeout | proxy-codex | 10분 |
| Codex job timeout | proxy-codex | 360초 |
| 원시 artifact retention | proxy-codex | 1시간 |
| 이미지 dispatch 동시성 | proxy-image | 1 |
| 이미지 queue 총 pending | proxy-image | 20 |
| 방별 이미지 pending | proxy-image | 3 |
| 이미지 queue 정책 | proxy-image | FIFO |
| 이미지 queue wait timeout | proxy-image | 60분 |
| 이미지 prompt 최대 길이 | proxy-image | 1,000자 |
| request body | 각 프록시 | 32KB |
| 최대 이미지 크기 | proxy-image | 12MB |
| 허용 이미지 포맷 | proxy-image | PNG |
| 최소 해상도 | proxy-image | 256×256 |
| 최대 해상도 | proxy-image | 4096×4096 |
| 최종 artifact retention | proxy-image | 24시간 |

`proxy-manager`, `proxy-image`, `proxy-codex`는 각각 별도 OS process로 실행한다. 이미지 생성 중에도 manager route·image 상태 조회·GLM 대화는 응답해야 한다. 자세한 실행 상태와 복구 규칙은 [병렬 실행·Queue 모델](PROXY_CONCURRENCY_AND_QUEUE_MODEL.md)을 따른다.

## 11. Iris 적용 지점

### 11.1 신규 Kotlin 책임

```text
ImageProxySettings.kt
ImageProxyClient.kt
ImageCommandParser.kt
ImageJobCoordinator.kt
ImageReplySender.kt
```

### 11.2 명령

```text
헤이봇 이미지 <프롬프트>
헤이봇 그림 <프롬프트>
헤이봇 사진 <프롬프트>
헤이봇 이미지 상태
헤이봇 이미지 취소
```

### 11.3 처리 흐름

1. `헤이봇` 호출과 허용 room/user 정책 확인
2. 이미지 명령을 일반 GLM보다 먼저 분류
3. 방별 in-flight 확인
4. `이미지 생성을 시작했어요. 완료되면 보내드릴게요.` 텍스트 전송
5. `proxy-manager`를 통해 idempotent image job 생성
6. `proxy-image`가 요청을 검증하고 `image.generate` input 구성
7. `proxy-image`가 `proxy-codex` 내부 job 생성
8. `proxy-codex`가 전역 queue에서 Codex CLI 실행 후 원시 artifact 제공
9. `proxy-image`가 artifact를 내려받아 픽셀 QC 후 최종 PNG 저장
10. PD20 별도 coroutine이 image job 상태 polling
11. 성공 시 manager를 통해 PNG byte 다운로드
12. PD20에서 signature·size 재검증
13. `Replier.sendPhotoBytes(chatId, bytes)` 실행
14. 카카오 DB에 bot 이미지 로그가 생겼는지 선택적으로 확인

이미지 생성 coroutine은 GLM 텍스트 actor와 분리한다. 그렇지 않으면 3개 방의 일반 대화가 이미지 생성 시간만큼 정지한다.

여러 방에서 요청이 들어오면 `proxy-image`가 접수 sequence에 따라 FIFO queue에 저장하고 한 건씩 실행한다. 각 job의 불변 문자열 `chatId`로 완료 대상을 결정하며 전역 `currentChatId`나 마지막 알림 값을 사용하지 않는다. Android는 pending job ID를 로컬에 저장하고 별도 polling scheduler에서 복구 가능하게 조회해야 한다.

### 11.4 Replier 변경

현재 `Replier.sendPhoto`는 Base64 문자열을 다시 byte array로 변환한다. 내부 자동응답에서는 다음 API를 추가하는 편이 효율적이다.

```text
sendPhotoBytes(room: Long, imageBytes: ByteArray)
```

기존 HTTP `/reply` Base64 계약은 호환성을 위해 유지한다.

## 12. ADB와 실행 연결

권장 기본 연결:

```bash
adb -s 0123456789ABCDEF reverse tcp:4340 tcp:4340
```

Iris 환경:

```text
IRIS_IMAGE_PROXY_ENABLED=true
IRIS_PROXY_MANAGER_BASE_URL=http://127.0.0.1:4340
IRIS_PROXY_MANAGER_ROUTE_SECRET_FILE=/data/local/private/iris-proxy-manager-route.secret
IRIS_IMAGE_PROXY_JOB_TIMEOUT_MS=600000
IRIS_IMAGE_PROXY_MAX_BYTES=12582912
```

`proxy-manager`는 `127.0.0.1:4340`, `proxy-image`는 내부 `127.0.0.1:4347`, `proxy-codex`는 내부 `127.0.0.1:4348`에 바인딩한다. PD20은 manager에만 연결한다. 기능 프록시는 Codex가 필요할 때만 `proxy-codex` 내부 API를 호출한다. USB가 끊겨도 GLM 텍스트 기능은 계속 동작하고 프록시 기능만 degraded 상태가 되어야 한다.

## 13. Codex CLI 포함 방식

### 권장

- Codex CLI 관련 구현은 모두 `vendor/server/proxy-codex`에 둠
- 서버가 특정 global 경로를 하드코딩하지 않음
- `CODEX_CLI_BIN`으로 실행 파일 지정
- 프로젝트별로 검증된 Codex CLI 버전을 pin한 뒤 lockfile에 기록
- `proxy-codex/scripts/doctor.sh`에서 version, login, exec, capability 확인
- `proxy-codex/scripts/bootstrap_codex_cli.sh`가 설치 안내와 검증을 담당
- Codex auth 파일은 `proxy-image`와 `proxy-manager`에 제공하지 않음
- `proxy-codex`는 caller·capability allowlist를 통과한 내부 job만 실행
- 호출 프록시는 shell command·argv·env·workdir·output path를 지정할 수 없음

### 현재 선행 조치

1. 현재 전역 `@openai/codex 0.73.0` 비대화식 실행 실패 원인 확인
2. 공식 설치 경로로 CLI 업데이트 또는 재설치
3. `codex --login` 또는 기존 로그인 상태 검증
4. `codex exec` non-interactive smoke
5. image generation capability canary
6. 검증된 버전과 명령만 서버 설정으로 고정

공식 문서는 CLI 설치와 로그인 흐름을 안내하지만, old-bot이 사용하는 `--enable image_generation` 플래그를 안정된 공개 계약으로 보장한다고 단정할 근거는 확인되지 않았다. 반드시 실행 시 capability probe를 통과해야 한다.

## 14. 공개 URL 기능

직접 이미지 전송이 성공한 뒤 선택적으로 추가한다.

```text
IMAGE_PUBLIC_PUBLISH_ENABLED=false
```

활성화할 경우:

- 로컬 static server
- OG post page
- Cloudflare Tunnel 또는 고정 HTTPS
- public HEAD/GET 검증
- tunnel 429 cooldown
- URL에 secret·로컬 경로 미노출

직접 카카오 첨부가 실패했을 때 자동으로 public URL을 뿌리는 fallback은 기본 OFF를 권장한다. 이미지가 공개되는 개인정보 범위가 달라지기 때문이다.

## 15. 테스트 계획

### 서버 단위 테스트

- manager: registry, prefix 분기, dependency graph, 인증 경계
- image: 18자리 ID, prompt, image job, codex job mapping, PNG QC
- codex: caller/capability allowlist, global queue, timeout, cancel
- codex: 임의 argv·env·workdir·output path 입력 거부
- codex: child process timeout과 process group kill
- codex: path traversal·symlink 차단
- image/codex: 각 artifact retention과 error redaction

### 통합 테스트

- manager registry 중복 id·prefix 충돌·비-loopback target 차단
- manager `/v1/image` 분기와 binary streaming
- route secret·internal secret·admin secret 경계
- proxy별 health·readiness 집계와 장애 격리
- proxy-image → proxy-codex contract와 service identity
- fake Codex job 성공·실패·지연·취소
- 서로 다른 caller의 idempotency와 queue 격리
- A방 이미지 생성 중 B·C방 이미지 queue 접수와 FIFO 순서
- A방 이미지 생성 중 모든 방의 GLM 텍스트 응답 무회귀
- queue 총량·방별 한도와 queue full 오류
- 각 완료 이미지가 job의 원래 `chatId`로 전달되는지 확인
- proxy-image·PD20 재시작 후 queued/pending delivery 복구
- 실제 Codex CLI readiness
- 실제 Codex 이미지 1장 canary
- ADB reverse를 통한 PD20 다운로드
- Iris `sendPhotoBytes`
- 허용 방 E2E
- 중복 호출과 동시 방 호출
- USB reverse 해제 시 텍스트 기능 무회귀

## 16. 라이선스

old-bot `LICENSE`는 별도 계약 없이는 복사·수정·운영을 허용하지 않는 검토·참조 전용 라이선스다.

이번 도입은 다음 원칙을 지킨다.

- old-bot source file 복사 금지
- 클래스·코드 직접 이식 금지
- 기능 요구사항과 실패 사례만 참고
- new-bot에서 별도 파일명·구조·테스트로 독립 구현
- 직접 재사용이 필요하면 Coreline AI 권리 확인 선행

## 17. 최종 권장 범위

### 1차 완료 기준

- Codex CLI readiness 통과
- `proxy-manager` registry·분기·인증·상태 집계
- `proxy-image` 독립 패키지
- `proxy-codex` 공용 capability Job API와 전역 queue
- `proxy-image`에 Codex CLI 직접 실행 코드가 없음
- async job API
- 직렬 queue와 취소
- 실제 픽셀 QC
- 인증 binary download
- ADB reverse
- `헤이봇 이미지 ...`
- Iris 카카오 직접 이미지 전송
- 3개 허용 방 중 정책상 허용된 방 E2E

### 2차 완료 기준

- 서버 재시작 job 복구
- launchd + watchdog
- 관리자 상태/취소 명령
- artifact cleanup
- 장시간 안정성 테스트

### 선택 완료 기준

- public OG URL
- Cloudflare Tunnel
- 인포그래픽
- 다중 이미지
