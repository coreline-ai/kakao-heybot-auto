# HeyBot Proxy Platform Architecture

- 작성일: 2026-07-25
- 실행 위치: Mac mini
- 상태: image/codex 구현·운영 중, draw/brush 구현 완료·기본 OFF

## 1. 핵심 구조

프록시 플랫폼은 세 계층으로 분리한다.

```text
1. Edge / Control
   proxy-manager

2. Domain Proxy
   proxy-image
   proxy-draw        # 펜 선화 → 브러시 채색, 기본 OFF
   proxy-video       # 향후
   proxy-conversation # GLM 외부 text gateway

3. Execution Engine Proxy
   proxy-codex
   proxy-brush       # 고정 Remotion pen-brush renderer, 기본 OFF
   proxy-grok        # 향후
```

```text
PD20 Iris
  -> proxy-manager
       ├── /v1/image -> proxy-image -> proxy-codex -> Codex CLI
       ├── /v1/draw  -> proxy-draw  -> proxy-codex -> proxy-brush
       └── /v1/video -> proxy-video -> proxy-grok  -> Grok CLI
       └── /v1/conversation -> proxy-conversation -> proxy-codex/proxy-grok -> text CLI
```

## 2. 설계 원칙

1. PD20은 `proxy-manager`만 호출한다.
2. manager는 gateway route와 프록시 상태·테스트·수명주기를 관리한다.
3. domain proxy는 사용자 기능, job, 정책, 도메인 QC와 최종 artifact를 소유한다.
4. engine proxy는 특정 CLI의 인증, capability, queue, process, workspace와 원시 artifact를 소유한다.
5. domain proxy는 CLI를 직접 실행하지 않고 engine proxy의 내부 API를 호출한다.
6. engine proxy는 domain·Kakao 타입을 알지 않는다.
7. 프록시는 다른 패키지의 source 또는 runtime 파일을 직접 읽지 않는다.
8. 모든 호출은 버전이 있는 HTTP·JSON·binary streaming 계약을 사용한다.
9. 자유 shell 프록시는 만들지 않고 capability allowlist만 공개한다.
10. 장애와 queue는 engine 또는 domain별로 격리한다.

## 2.1 독립 프로세스와 비차단 실행

각 프록시는 하나의 상위 애플리케이션 thread로 합치지 않고 별도 OS process·launchd service로 실행한다.

```text
proxy-manager process
proxy-image process
proxy-codex process -> Codex CLI child
proxy-draw process
proxy-brush process -> Python/Remotion child
proxy-video process
proxy-grok process  -> Grok CLI child
proxy-conversation process -> provider text relay
```

- 이미지 생성 중 GLM 텍스트 대화와 다른 프록시는 계속 동작한다.
- domain proxy는 durable queue로 여러 방 요청을 접수한다.
- engine proxy는 독립 queue로 실제 CLI 동시 실행 수를 제한한다.
- API는 비동기 job을 즉시 반환하고 생성 완료까지 HTTP 요청을 유지하지 않는다.
- CLI child는 async process API로 실행해 proxy HTTP event loop를 막지 않는다.

세부 상태·한도·방별 전달 규칙은 [독립 프로세스·병렬 실행·Queue 모델](PROXY_CONCURRENCY_AND_QUEUE_MODEL.md)을 따른다.

## 3. 패키지 역할

| 패키지 | 종류 | 외부 노출 | 핵심 책임 | 의존 |
|---|---|---:|---|---|
| `proxy-manager` | edge/control | PD20 | route·registry·health·test·lifecycle | 각 proxy 상태 |
| `proxy-image` | domain | manager 경유 | image job·prompt 정책·픽셀 QC·최종 PNG | `proxy-codex` |
| `proxy-codex` | engine | 내부 전용 | Codex CLI·capability·전역 queue·workspace | Codex CLI |
| `proxy-draw` | domain | manager 경유·기본 OFF | 방 scope job·제어된 PNG source·최종 MP4 QC | `proxy-codex`, `proxy-brush` |
| `proxy-brush` | engine | 내부 전용·기본 OFF | 10초·1장면 pen outline → brush color renderer·QA | Python, Remotion, FFmpeg |
| `proxy-video` | domain | manager 경유, 비활성 | video job·미디어 QC·최종 MP4 | `proxy-grok` |
| `proxy-grok` | engine | 내부 전용, 비활성 | Grok CLI·capability·전역 queue·workspace | Grok CLI |
| `proxy-conversation` | domain | manager 경유 | provider 선택·text contract·timeout·no-store relay | `proxy-codex`, `proxy-grok` |

## 4. 포트

| 패키지 | 포트 | 상태 |
|---|---:|---|
| `proxy-manager` | `4340` | 계획 |
| `proxy-image` | `4347` | 계획 |
| `proxy-codex` | `4348` | 계획 |
| `proxy-video` | `4357` | 향후·비활성 |
| `proxy-grok` | `4358` | 향후·비활성 |
| `proxy-draw` | `4359` | 기본 OFF |
| `proxy-brush` | `4360` | 내부 전용·기본 OFF |
| `proxy-conversation` | `4361` | manager gateway |

PD20 ADB reverse는 manager 한 개만 사용한다.

```bash
adb -s 0123456789ABCDEF reverse tcp:4340 tcp:4340
```

## 5. 외부 데이터 흐름

### 이미지

```text
PD20
 -> manager /v1/image/jobs
 -> image job
 -> codex internal job: image.generate
 -> Codex raw PNG
 -> image pixel QC
 -> manager binary stream
 -> PD20
 -> Kakao image send
```

### 비디오

```text
PD20
 -> manager /v1/video/jobs
 -> video job
 -> grok internal job: video.generate
 -> Grok raw MP4
 -> video media QC
 -> manager binary stream
 -> PD20
 -> Kakao video send
```

비디오는 현재 비활성이며 Grok CLI 실제 capability가 doctor와 canary를 통과한 뒤에만 활성화한다.

### 펜브러쉬

```text
PD20
 -> manager /v1/draw/jobs
 -> draw job (chatId scope/idempotency)
 -> codex internal job: controlled image.generate
 -> draw source PNG QC
 -> brush internal fixed pen-brush render + frame/MP4 QA
 -> draw private artifact
 -> manager binary stream
 -> PD20
 -> Kakao native video send
```

`proxy-brush`는 사용자의 prompt·YAML·CLI argument를 직접 받지 않는다. 10초·1 scene·세로
규격은 renderer service에 고정하며, `proxy-draw`만 `draw` caller secret으로 source PNG와
deterministic seed를 전달한다.

### 텍스트 대화

```text
PD20 Iris
 -> manager /v1/conversation/respond
 -> proxy-conversation (engine=codex|grok)
 -> provider internal conversation.respond.v1
 -> text-only CLI runner
 -> bounded text response
 -> PD20 Iris
 -> Kakao text send
```

GLM은 위 경로를 사용하지 않고 Android 내부 `GlmClient`에서 바로 처리한다. Codex/Grok의
text queue는 각 provider의 image/video queue와 분리하며, conversation proxy는 응답을
파일이나 SQLite에 저장하지 않는다.

## 6. Registry와 dependency

manager registry는 모든 패키지를 관리하지만 외부 route는 `gateway` 프록시에만 부여한다.

```text
image: gateway, /v1/image, dependencies=[codex]
codex: internal, dependencies=[]
video: gateway, /v1/video, dependencies=[grok], enabled=false
grok:  internal, dependencies=[], enabled=false
draw:  gateway, /v1/draw, dependencies=[codex, brush], enabled=false
brush: internal, dependencies=[], enabled=false
conversation: gateway, /v1/conversation, dependencies=[codex, grok], enabled=true
```

검증 규칙:

- 고유 proxy id
- 고유 loopback port
- gateway만 routePrefix 보유
- prefix 중복·포함 충돌 금지
- internal proxy 외부 route 금지
- 존재하지 않는 dependency 금지
- dependency cycle 금지
- disabled dependency가 있으면 상위 proxy ready 금지

## 7. Capability 계약

Engine proxy는 CLI 명령을 그대로 노출하지 않는다.

```text
proxy-codex
  image.generate -> allowed callers: image, draw

proxy-brush
  pen-brush.render.v1 -> allowed caller: draw

proxy-grok
  video.generate -> allowed caller: video

proxy-codex / proxy-grok
  conversation.respond.v1 -> allowed caller: conversation
```

각 capability는 다음을 가진다.

- capability id와 version
- allowed callers
- input JSON schema
- controlled instruction builder
- model environment key
- timeout
- accepted artifact types와 크기
- readiness·canary 정책

금지 입력:

- shell command
- CLI argv
- child environment
- workdir
- output path
- sandbox 해제 옵션

## 8. Job 소유권

```text
imageJobId -> codexJobId
videoJobId -> grokJobId
drawJobId  -> codexJobId -> brushJobId
```

Domain proxy:

- 사용자 요청 idempotency
- 방별 제한
- 사용자 상태·취소
- 최종 artifact
- 도메인 QC

Engine proxy:

- caller-scoped idempotency
- CLI 전역 queue
- 실행 timeout·cancel
- 격리 workspace
- 원시 artifact

manager는 job을 소유하지 않고 route와 상태 조회를 전달한다.

Queue 소유:

- `proxy-image`: 이미지 접수 순서·방별 한도·완료 전달
- `proxy-codex`: Codex CLI 전체 동시성·caller 간 자원 보호
- `proxy-video`: 비디오 접수 순서·방별 한도
- `proxy-grok`: Grok CLI 전체 동시성
- `proxy-draw`: 펜브러쉬 접수 순서·방별 한도·최종 MP4 보관
- `proxy-brush`: 고정 renderer 한 건 실행

각 queue의 concurrency와 pending limit는 서로 독립적으로 설정한다.

## 9. 인증 경계

```text
PD20 route credential
  -> manager

manager client credential
  -> domain/engine health·route·test

domain caller credential
  -> engine capability API

CLI authentication
  -> 해당 engine proxy만 접근
```

caller별 credential을 사용한다. 한 패키지가 다른 패키지의 runtime secret을 직접 읽지 않고 bootstrap 과정에서 필요한 credential 복사본을 각 패키지에 배치한다.

## 10. 테스트 계층

### Contract

- manager ↔ domain proxy
- domain proxy ↔ engine proxy
- capability input·artifact schema
- binary streaming

### Unit

- registry와 dependency graph
- caller/capability allowlist
- job state transition
- queue·timeout·cancel
- 도메인별 artifact QC

### Integration

- fake CLI
- fake engine proxy
- 실제 CLI doctor
- 명시적 실제 artifact canary
- dependency 장애 격리

### E2E

- PD20 → manager → domain → engine → CLI
- artifact → PD20 → Kakao
- ADB 단절 시 GLM 텍스트 무회귀

## 11. 신규 프록시 추가 절차

### Domain proxy

1. `proxy-<기능>` 독립 패키지 생성
2. 외부 `/v1/<기능>` Job API 정의
3. 필요한 engine capability client 구현
4. 도메인 QC와 최종 artifact 저장 구현
5. manager에 `exposure: gateway`와 dependency 등록
6. contract·장애 격리 테스트

### Engine proxy

1. `proxy-<엔진>` 독립 패키지 생성
2. CLI doctor와 인증 확인
3. 내부 capability Job API 정의
4. caller·capability allowlist 구성
5. queue·workspace·process·artifact 구현
6. manager에 `exposure: internal` 등록
7. 사용하는 domain proxy에 dependency와 client 추가

## 12. 현재 구현 우선순위

1. `proxy-manager`
2. `proxy-codex`
3. `proxy-image`
4. PD20 이미지 E2E
5. `proxy-grok` doctor와 capability 검증
6. `proxy-video`
7. PD20 비디오 E2E

`proxy-grok`와 `proxy-video`는 구조만 예약하며 현재 이미지 구현 범위를 방해하지 않도록 비활성 상태를 유지한다.
