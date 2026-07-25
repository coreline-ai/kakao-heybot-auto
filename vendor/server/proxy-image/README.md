# HeyBot Image Proxy (`proxy-image`)

Mac mini에서 실행하며 이미지 도메인 API·정책·픽셀 QC를 담당하는 **독립 이미지 프록시 패키지**다. 외부 요청은 `proxy-manager`에서 받고, Codex 실행은 내부 `proxy-codex` API에 위임한다.

현재 상태는 **구현 및 자동 테스트 완료**다. SQLite queue, Codex Job API 연동, PNG decode·픽셀 QC, 최종 artifact 보존과 streaming download가 동작한다.

## 독립성 원칙

1. 자체 `package.json`, lockfile, 설정, 테스트, 운영 스크립트를 가진다.
2. `old-bot` 코드나 패키지를 import하지 않는다.
3. 다른 서버 패키지의 소스를 import하지 않고 버전이 있는 HTTP 계약으로만 통신한다.
4. Codex CLI를 직접 실행하지 않고 `clients/codex`에서 `proxy-codex` 내부 API만 호출한다.
5. HTTP, 인증, 이미지 작업 상태, 오케스트레이션, 이미지 검증, 저장 기능을 모듈별로 분리한다.
6. 각 모듈은 공개 `index.ts` 계약만 외부에 노출하고 내부 파일의 교차 import를 제한한다.
7. `runtime`과 인증정보는 패키지 소스 및 Git에서 분리한다.

## 구현 구조

```text
proxy-image/
├── README.md
├── .env.example
├── package.json
├── package-lock.json
├── tsconfig.json
├── src/
│   ├── index.ts                 # composition root
│   ├── config/                  # 환경 설정 파싱·검증
│   ├── http/                    # 라우팅·body 제한·오류 응답
│   ├── auth/                    # Bearer 인증·timing-safe 비교
│   ├── jobs/                    # image job 상태·idempotency
│   ├── queue/                   # durable FIFO·방별/전체 한도
│   ├── orchestration/           # image job과 codex job 매핑
│   ├── clients/
│   │   └── codex/               # proxy-codex 내부 HTTP client
│   ├── images/                  # PNG decode·QC·metadata
│   ├── storage/                 # SQLite·artifact·retention
│   └── observability/           # health·ready·metric·redacted log
├── scripts/
│   ├── doctor.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── self-test.sh
│   └── launchd/
├── test/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── runtime/                     # Git 제외
    ├── jobs/
    ├── artifacts/
    ├── db/
    └── secrets/
```

## 모듈 의존 방향

```text
http ───────┐
auth ───────┼──> jobs ──> orchestration ──> clients/codex ──HTTP──> proxy-codex
config ─────┘      │              │
                   ├──> images <──┘
                   └──> storage

observability는 각 모듈의 민감정보가 제거된 상태만 읽는다.
```

금지할 방향:

- `proxy-image`에서 Codex CLI process 직접 실행
- `clients/codex`가 Codex argv·environment·workdir를 전달
- `proxy-codex` 소스 또는 런타임 파일 직접 참조
- `storage`가 Iris 또는 Kakao 타입 참조
- `auth`가 job 실행 또는 이미지 파일 접근
- 사용자 prompt가 shell 명령 또는 filesystem path로 직접 연결

## proxy-codex 연동

`proxy-image`는 이미지 요청을 검증하고 controlled image input을 만든 뒤 다음 내부 API를 호출한다.

```text
POST   http://127.0.0.1:4348/internal/v1/codex/jobs
GET    http://127.0.0.1:4348/internal/v1/codex/jobs/:jobId
GET    http://127.0.0.1:4348/internal/v1/codex/jobs/:jobId/artifacts/:artifactId
DELETE http://127.0.0.1:4348/internal/v1/codex/jobs/:jobId
```

요청 capability는 초기 `image.generate`로 고정한다. `proxy-image`는 `imageJobId`와 `codexJobId`를 매핑하고, 반환된 원시 PNG에 decode·dimensions·brightness·contrast·entropy QC를 적용한 뒤 최종 artifact로 보관한다.

## 독립 프로세스와 이미지 Queue

`proxy-image`는 manager·Codex·Iris와 분리된 OS process로 실행한다.

```text
A방 job-1 running
B방 job-2 queued
C방 job-3 queued
```

기본 생성 동시성은 `1`이며 요청을 FIFO로 하나씩 `proxy-codex`에 dispatch한다. queue는 SQLite에 저장해 재시작 후 복구하고 다음 설정을 적용한다.

```text
IMAGE_PROXY_QUEUE_CONCURRENCY=1
IMAGE_PROXY_QUEUE_MAX_PENDING=20
IMAGE_PROXY_QUEUE_MAX_PENDING_PER_ROOM=3
IMAGE_PROXY_QUEUE_POLICY=fifo
IMAGE_PROXY_QUEUE_WAIT_TIMEOUT_MS=3600000
```

각 job에는 문자열 `chatId`, `logId`, 접수 sequence와 delivery 상태를 저장한다. 완료 이미지는 마지막 알림이나 전역 방 값이 아니라 해당 job의 `chatId`로 전달한다.

Queue가 차면 `IMAGE_QUEUE_FULL`, 방별 상한이면 `ROOM_QUEUE_LIMIT`을 반환한다. 이미지 생성 중에도 신규 job 접수·상태·취소 API와 다른 프록시는 계속 동작해야 한다.

## manager 연동 계약

```text
POST   /v1/image/jobs
GET    /v1/image/jobs/:jobId
GET    /v1/image/jobs/:jobId/file
DELETE /v1/image/jobs/:jobId
GET    /health
GET    /ready
POST   /v1/self-test/readiness
POST   /v1/self-test/generate
```

`proxy-manager`는 `/v1/image` prefix를 이 프록시의 `http://127.0.0.1:4347`로 전달한다. 이미지 프록시는 loopback 내부 포트에만 바인딩한다.

PD20 연결과 인증은 `proxy-manager`가 담당하며, 이미지 프록시는 manager가 전달한 내부 인증만 검증한다.

## 구현 순서

1. `config`, `auth`, `http` 최소 골격과 fake Codex client
2. image job, SQLite 상태, 방별 제한과 idempotency
3. `proxy-codex` contract client와 job 매핑
4. 원시 artifact 다운로드와 실제 픽셀 QC
5. binary download와 artifact cleanup
6. doctor, self-test, launchd 운영

상세 기준은 [프록시 관리자](../proxy-manager/README.md), [Codex 프록시](../proxy-codex/README.md), [병렬 실행·Queue 모델](../docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md), [도입 분석서](../docs/IMAGE_PROXY_ADOPTION_ANALYSIS.md), [구현 계획](../../../dev-plan/implement_20260725_075640.md)을 따른다.
