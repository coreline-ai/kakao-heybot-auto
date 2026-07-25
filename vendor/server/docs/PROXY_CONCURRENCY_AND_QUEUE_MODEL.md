# Proxy Concurrency and Queue Model

- 작성일: 2026-07-25
- 상태: 필수 실행 모델 확정, 런타임 구현 전

## 1. 반드시 지킬 동작

1. 각 `proxy-*` 패키지는 독립 OS 프로세스로 실행한다.
2. 한 프록시의 장시간 작업이 manager, 다른 프록시, Iris GLM 대화를 막으면 안 된다.
3. 생성 API는 장시간 HTTP 연결을 잡지 않고 `202 Accepted`로 job을 즉시 반환한다.
4. 이미지 생성은 기본 동시성 `1`로 한 장씩 순차 처리한다.
5. 대기 요청은 durable queue에 저장하며 총량·방별 상한을 환경설정으로 변경할 수 있어야 한다.
6. 각 job은 불변 `chatId`, `logId`, `requestId`를 가져 완료 결과가 정확한 방으로 전달돼야 한다.
7. 재시작 후에도 queued job과 완료 전송 대상을 복구할 수 있어야 한다.

## 2. 프로세스 모델

```text
Mac mini
├── proxy-manager process
├── proxy-image process
├── proxy-codex process
│   └── codex CLI child process (실행 중 0~N, 기본 N=1)
├── proxy-video process       # 향후
└── proxy-grok process        # 향후
    └── grok CLI child process (기본 N=1)

PD20
├── GLM text actor/coroutine
├── command router
└── image job polling/delivery coroutine
```

각 프록시는 자체 event loop, 메모리, DB connection, queue와 로그를 가진다. CLI는 engine proxy의 child process로 실행한다.

## 3. 병렬성과 직렬성

### 병렬 실행

- GLM 텍스트 응답과 이미지 생성
- manager route 처리와 Codex CLI 실행
- 이미지 job 상태 조회와 현재 이미지 생성
- `proxy-image`와 향후 `proxy-video`
- `proxy-codex`와 `proxy-grok`
- 여러 방의 job 접수·상태 조회·완료 전달

### 직렬 실행

- `proxy-image`의 실제 이미지 생성 dispatch: 기본 1개
- `proxy-codex` Codex CLI 실행: 기본 1개
- `proxy-video` 실제 비디오 생성 dispatch: 향후 기본 1개
- `proxy-grok` Grok CLI 실행: 향후 기본 1개

직렬 생성 중에도 각 프로세스의 HTTP event loop는 block하지 않아야 한다. CLI 실행에는 async child-process API를 사용하고 동기식 process API를 금지한다.

## 4. 두 계층 queue

### Domain queue

`proxy-image`가 이미지 요청의 접수 순서, 방별 제한, 사용자 취소와 최종 전달 대상을 소유한다.

```text
image queue
  A방 job-1  running
  B방 job-2  queued position=1
  C방 job-3  queued position=2
```

기본 정책:

- concurrency: `1`
- scheduling: `fifo`
- 전체 pending 상한: configurable
- 방별 pending 상한: configurable
- running job은 pending 개수와 별도로 표시

필요하면 향후 `room-round-robin` 정책을 추가할 수 있지만 초기 기본은 접수 순서를 보존하는 FIFO로 한다.

### Engine queue

`proxy-codex`는 여러 domain proxy가 Codex를 동시에 요청해도 실제 CLI 동시 실행 수를 제한한다.

```text
proxy-image ──┐
proxy-document ├──> proxy-codex global queue -> Codex CLI
proxy-review ──┘
```

Engine queue는 caller-scoped idempotency와 전체 CLI 자원 보호를 담당한다. Domain queue와 목적이 다르므로 둘 다 필요하다.

## 5. 권장 설정

### proxy-image

```text
IMAGE_PROXY_QUEUE_CONCURRENCY=1
IMAGE_PROXY_QUEUE_MAX_PENDING=20
IMAGE_PROXY_QUEUE_MAX_PENDING_PER_ROOM=3
IMAGE_PROXY_QUEUE_POLICY=fifo
IMAGE_PROXY_QUEUE_WAIT_TIMEOUT_MS=3600000
```

### proxy-codex

```text
CODEX_PROXY_QUEUE_CONCURRENCY=1
CODEX_PROXY_QUEUE_MAX_PENDING=8
CODEX_PROXY_QUEUE_WAIT_TIMEOUT_MS=600000
```

### 향후 video/grok

```text
VIDEO_PROXY_QUEUE_CONCURRENCY=1
VIDEO_PROXY_QUEUE_MAX_PENDING=4
VIDEO_PROXY_QUEUE_MAX_PENDING_PER_ROOM=1

GROK_PROXY_QUEUE_CONCURRENCY=1
GROK_PROXY_QUEUE_MAX_PENDING=4
```

모든 값은 환경변수에서 양의 정수로 검증하고, 잘못된 값이면 프록시가 ready 상태가 되지 않아야 한다.

## 6. Job 상태

### Domain job

```text
accepted
 -> queued
 -> dispatching
 -> generating
 -> validating
 -> succeeded

queued/generating -> cancel_requested -> cancelled
queued/generating/validating -> failed
queued -> expired
```

### Engine job

```text
queued -> running -> succeeded
queued/running -> cancelled
queued/running -> failed
queued -> expired
```

상태 전이는 SQLite transaction으로 저장한다. 프로세스 재시작 시:

- `queued`: queue에 복구
- `running`: `interrupted`로 기록 후 capability 정책에 따라 재시도 또는 실패
- `succeeded`이지만 미전달: Android polling 대상 유지

## 7. 요청 접수

```http
POST /v1/image/jobs
```

서버는 생성 완료를 기다리지 않고 즉시 응답한다.

```json
{
  "ok": true,
  "jobId": "01J...",
  "status": "queued",
  "queuePosition": 2,
  "pollAfterMs": 2000
}
```

Queue가 찼을 때:

```json
{
  "ok": false,
  "error": {
    "code": "IMAGE_QUEUE_FULL",
    "retryAfterMs": 60000
  }
}
```

방별 한도 초과:

```json
{
  "ok": false,
  "error": {
    "code": "ROOM_QUEUE_LIMIT",
    "maxPendingPerRoom": 3
  }
}
```

## 8. 여러 오픈방 처리

모든 image job에 다음 값을 저장한다.

```text
requestId
chatId          # JSON decimal string
logId           # JSON decimal string
requesterId     # JSON decimal string
prompt
acceptedAt
sequence
status
codexJobId
deliveryStatus
```

Idempotency key:

```text
image:<chatId>:<logId>
```

예:

```text
09:00 A방 이미지 요청 -> job-A queued
09:01 B방 이미지 요청 -> job-B queued
09:02 C방 일반 대화   -> GLM이 즉시 별도 응답
09:03 job-A 완료      -> A방 이미지 전송
09:03 job-B 시작
09:04 A방 일반 대화   -> GLM이 즉시 별도 응답
09:06 job-B 완료      -> B방 이미지 전송
```

전역 `currentChatId` 또는 마지막 알림 값을 사용하면 안 된다. 각 job에 저장된 불변 `chatId`로 전달한다.

## 9. Android 비차단 처리

Iris의 처리 경계:

```text
CommandRouter
├── 일반 메시지 -> GlmAutoReplyHandler
└── 이미지 명령 -> ImageJobCoordinator
```

`ImageJobCoordinator`는 GLM actor와 별도 coroutine scope를 사용한다.

1. 이미지 명령을 접수한다.
2. `POST /v1/image/jobs` 결과만 기다린다.
3. 원래 방에 접수·queue 위치를 알린다.
4. pending job ID를 Android 로컬 저장소에 보관한다.
5. background polling scheduler가 여러 job을 비차단 조회한다.
6. 완료 PNG를 다운로드한다.
7. job의 `chatId`로 `Replier.sendPhotoBytes()`를 호출한다.
8. 전송 성공 후 pending job을 정리한다.

이미지 생성 coroutine에서 `Thread.sleep`, 동기 네트워크 호출, GLM actor 점유를 금지한다.

## 10. 다른 기능과의 독립성

```text
proxy-image queue full
  -> GLM text 정상
  -> manager health 정상
  -> proxy-video 정상
  -> proxy-grok 정상

proxy-codex generation running
  -> 신규 image job 접수·조회 가능
  -> 다른 room 대화 가능
  -> Grok video 생성 가능
```

단, 동일 engine을 공유하는 domain proxy는 해당 engine의 전역 queue를 공유한다. 예를 들어 향후 `proxy-document`도 Codex를 사용하면 image와 document의 CLI 실행은 Codex queue 정책에 따라 조정된다.

## 11. Queue metric

각 domain/engine proxy는 다음 지표를 제공한다.

```text
queue_pending
queue_running
queue_capacity
queue_oldest_wait_ms
queue_accepted_total
queue_rejected_total
queue_cancelled_total
job_succeeded_total
job_failed_total
```

Domain proxy 추가 지표:

```text
active_rooms
delivery_pending
delivery_failed
```

manager는 지표를 집계하지만 queue를 직접 소유하거나 실행 순서를 변경하지 않는다.

## 12. 필수 테스트

- A방 이미지 생성 중 B·C방 일반 GLM 대화
- A방 생성 중 B방 이미지 접수와 queue position
- A → B → C 순서 생성과 각 `chatId` 전달
- queue 총량·방별 상한 경계값
- queue full 중 다른 프록시와 GLM 무회귀
- Codex child 실행 중 manager health·job status 응답
- 동일 `chatId+logId` 중복 요청 1건 처리
- 한 방 취소가 다른 방 job에 영향 없음
- proxy-image 재시작 후 queued job 복구
- PD20 재시작 후 pending delivery polling 복구
- Codex queue와 Grok queue의 병렬 실행
