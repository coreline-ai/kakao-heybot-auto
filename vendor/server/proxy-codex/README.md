# HeyBot Codex Proxy (`proxy-codex`)

Mac mini의 Codex CLI를 한 곳에서 실행하고, 허용된 다른 `proxy-*` 패키지에 **버전이 있는 내부 Job API**로 제공하는 공용 실행 프록시다.

현재 상태는 **구현 및 자동 테스트 완료**다. Codex CLI `0.145.0`의 비대화식 실행과 실제 이미지 artifact 생성도 확인했다.

## 전체 위치

```text
PD20 Iris
  -> proxy-manager
  -> proxy-image
  -> proxy-codex
  -> Codex CLI
```

`proxy-codex`는 PD20이나 외부 클라이언트에 노출하지 않는다. `proxy-image`와 추후 추가될 허용된 프록시만 caller별 credential로 loopback 내부 API를 호출한다.

## 단독 책임

1. Codex CLI 경로·버전·로그인·capability readiness를 확인한다.
2. Codex 실행 전역 queue, timeout, cancel 및 process group 종료를 소유한다.
3. job별 격리 workspace와 최소 child environment를 만든다.
4. capability allowlist에 등록된 작업만 실행한다.
5. Codex 출력 탐색, path confinement, artifact 복사와 구조 검증을 수행한다.
6. 호출 프록시별 인증·허용 capability·idempotency 범위를 검증한다.
7. 실행 상태와 임시 artifact를 내부 API로 제공하고 보존기간 뒤 삭제한다.

## 소유하지 않는 책임

- 카카오톡 `chat_id`, 방 정책, 호출어 처리
- 이미지 요청의 사용자 메시지와 응답 문구
- 이미지 밝기·대비·미학적 QC
- 카카오 이미지 전송
- 외부 route 분기
- 임의 shell command, argv, 환경변수, workdir 실행

이미지 도메인 검증·프롬프트 조립·픽셀 QC·최종 파일 보관은 `proxy-image`가 담당한다.

## 구현 구조

```text
proxy-codex/
├── README.md
├── .env.example
├── config/
│   └── capabilities.example.json
├── package.json
├── package-lock.json
├── tsconfig.json
├── src/
│   ├── index.ts
│   ├── config/                  # 실행 환경·정책
│   ├── http/                    # 내부 Job API
│   ├── auth/                    # service identity·internal secret
│   ├── capabilities/            # capability registry·input schema
│   ├── jobs/                    # 상태·idempotency·cancel
│   ├── queue/                   # Codex 전역 동시성
│   ├── cli/                     # capability probe·process runner
│   ├── workspace/               # 격리 작업공간·path confinement
│   ├── artifacts/               # 결과 탐색·구조 검증·retention
│   └── observability/           # health·ready·redacted log
├── scripts/
│   ├── doctor.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── self-test.sh
│   ├── bootstrap_codex_cli.sh
│   └── launchd/
├── test/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
└── runtime/                     # Git 제외
    ├── jobs/
    ├── artifacts/
    ├── db/
    └── secrets/
```

## 내부 API

```text
POST   /internal/v1/codex/jobs
GET    /internal/v1/codex/jobs/:jobId
GET    /internal/v1/codex/jobs/:jobId/artifacts/:artifactId
DELETE /internal/v1/codex/jobs/:jobId

GET    /health
GET    /ready
POST   /internal/v1/self-test/readiness
POST   /internal/v1/self-test/capabilities/:capabilityId
```

작업 생성 예:

```json
{
  "requestId": "image-01J...",
  "capability": "image.generate",
  "input": {
    "prompt": "새벽 호수 위의 작은 로봇 일러스트"
  },
  "artifactContract": {
    "acceptedMediaTypes": ["image/png"],
    "maxArtifacts": 1,
    "maxBytesPerArtifact": 12582912
  }
}
```

요청에서 다음 필드는 받지 않는다.

- shell command
- CLI argv
- environment variables
- workdir
- output filesystem path
- sandbox 해제 옵션

## Capability 모듈

Codex 기능은 자유 명령이 아니라 등록된 capability로만 공개한다.

```text
image.generate
document.generate    # 추후
code.review          # 추후
```

각 capability는 다음을 독립적으로 가진다.

- 허용 caller proxy 목록
- 입력 JSON schema
- controlled instruction builder
- 모델 설정
- timeout과 artifact 계약
- 실행 시 허용할 Codex feature
- readiness 및 명시적 canary test

초기에는 `proxy-image`만 `image.generate`를 호출할 수 있다.

## 독립 프로세스와 Engine Queue

`proxy-codex`는 별도 OS process로 실행하고 Codex CLI를 async child process로 생성한다. 기본 전역 CLI 동시성은 `1`이며 설정으로 변경할 수 있다.

```text
CODEX_PROXY_QUEUE_CONCURRENCY=1
CODEX_PROXY_QUEUE_MAX_PENDING=8
CODEX_PROXY_QUEUE_WAIT_TIMEOUT_MS=600000
```

CLI child가 실행 중이어도 proxy-codex의 health·job status·cancel HTTP 처리는 계속 응답해야 한다. `spawnSync`, `execSync`, thread sleep처럼 event loop를 막는 구현은 금지한다.

Domain queue는 사용자 기능의 순서·방별 한도를, Codex queue는 여러 caller가 공유하는 CLI 자원을 보호한다. 두 queue는 역할이 다르며 caller-scoped idempotency로 중복 실행을 차단한다.

## 인증

```text
proxy-image
  -> service id: image
  -> caller별 proxy-codex credential
  -> caller/capability allowlist 확인
```

caller마다 서로 다른 credential을 사용한다. 예를 들어 `image` caller credential은 `proxy-image/runtime/secrets/codex-upstream.secret`과 `proxy-codex/runtime/secrets/callers/image.secret`에 각각 배치한다. 어느 패키지도 다른 패키지의 `runtime` 파일을 직접 읽지 않는다.

manager 상태·테스트 credential, caller credential, Codex 인증정보도 서로 분리한다. Codex 인증 파일은 사용자 홈의 `CODEX_HOME`에만 두고 이 패키지에 복사하지 않는다.

## Job과 artifact 소유권

- `proxy-codex`: Codex 실행 job, 전역 queue, 격리 workspace, 원시 artifact
- `proxy-image`: image job, 방별 정책, 이미지 QC, 최종 PNG와 카카오 전송 상태
- `proxy-image`는 `codexJobId`를 매핑해 polling한다.
- 원시 artifact를 받아 검증·보관한 뒤 Codex 임시 artifact 삭제를 요청할 수 있다.

## 실패 격리

- `proxy-codex` 장애 시 이를 사용하는 프록시만 degraded 상태가 된다.
- `proxy-image`는 upstream 오류를 이미지 도메인 오류 코드로 변환한다.
- manager와 GLM 텍스트 응답은 계속 동작한다.
- 다른 프록시의 job과 idempotency key는 caller scope로 분리한다.

상위 관리 구조는 [proxy-manager](../proxy-manager/README.md), 이미지 도메인은 [proxy-image](../proxy-image/README.md), 실행 규칙은 [병렬 실행·Queue 모델](../docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md), 전체 계획은 [구현 계획](../../../dev-plan/implement_20260725_075640.md)을 따른다.
