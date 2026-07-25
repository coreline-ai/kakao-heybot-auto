# old-bot 기능 분석 및 Iris 기반 new-bot 적용 제안

- 분석일: 2026-07-25
- 분석 대상: `/Volumes/Eprojects/project_202607/kakao-new-bot/old-bot`
- 적용 대상: `/Volumes/Eprojects/project_202607/kakao-new-bot/new-bot`
- old-bot 기준 커밋: `ff24ff55755fee376dd6df21eba17520c640e61a`
- new-bot Iris 기준 커밋: `ee1dc978ec465df11642596e40f74caff497301d`

## 1. 프로젝트 개요

### old-bot

old-bot은 다음을 결합한 대형 카카오톡 AI 비서 시스템이다.

1. Android `NotificationListenerService`로 카카오톡 알림 수신
2. 호출어·방·사용자·보안·중복·속도 제한 검사
3. 명령별 인텐트 라우팅
4. GLM/OpenAI/Codex/로컬 LLM/Grok 및 기능별 프록시 호출
5. 알림의 `RemoteInput` 답장 액션으로 카카오톡 전송
6. macOS의 다중 프록시, launchd, watchdog, ADB reverse 운영

### new-bot

new-bot은 루팅된 PD20에서 Iris를 실행하는 경량 구조다.

1. Iris가 카카오톡 DB 변경을 폴링
2. `chat_id`, `user_id`, 메시지를 직접 확보
3. `헤이봇` 호출과 허용 `chat_id` 검사
4. PD20에서 Z.AI GLM API 직접 호출
5. Iris Replier로 해당 `chat_id`에 직접 전송

따라서 old-bot의 알림 수신·RemoteInput 전송 계층은 new-bot에 필요하지 않다. 적용 대상은 명령 라우팅, 운영 정책, 메모리, 안정성, 데이터 기능이다.

## 2. 규모 요약

| 항목 | old-bot | new-bot |
|---|---:|---:|
| 디스크 사용량 | 약 1.2GB | 약 5.9MB |
| Git 객체 | 약 593MB | Iris 얕은 저장소 |
| 추적 파일 | 3,854개 | Iris 중심 소규모 구조 |
| Android 메인 Kotlin 파일 | 211개 | Iris 메인 Kotlin 파일 33개 |
| Android 테스트 파일 | 108개 | GLM 테스트 파일 4개, 총 26개 테스트 |
| 핵심 수신 처리 파일 | 4,735줄 | `GlmAutoReplyHandler.kt` 246줄 |
| 백엔드 서비스 | 15개 이상 | Z.AI 직접 호출 1개 |
| 전송 방식 | 알림 `RemoteInput` | Iris 직접 Replier |
| 루팅 정책 | 루팅 감지 시 차단 | 루팅이 필수 |

old-bot의 큰 용량은 웹 게임, 이미지, 영상 엔진, 생성 결과물과 Git 이력이 상당 부분 차지한다. 현재 헤이봇에 이 전체를 가져오는 것은 유지보수 비용만 크게 증가시킨다.

## 3. 아키텍처 비교

| 구분 | old-bot | new-bot | 판단 |
|---|---|---|---|
| 메시지 수신 | 카카오 알림 | 카카오 DB 폴링 | Iris 유지 |
| 방 식별 | 알림 `shortcutId`와 방 제목 | 숫자 `chat_id` | new-bot이 더 안정적 |
| 사용자 식별 | 발신자 표시 이름 | 숫자 `user_id` | new-bot이 권한 관리에 유리 |
| 메시지 전송 | 살아 있는 알림 Reply Action | Iris Replier | Iris 유지 |
| 선제 전송 | 캡처된 Reply 세션 필요 | 유효 `chat_id`로 전송 가능 | old 세션 계층 불필요 |
| LLM | 다중 provider 및 프록시 | `glm-4.5-flash` | 현재는 단순 구조 유지 |
| 메모리 | 방별 4턴·30분, 저장 복원 | 방별 4턴·30분, 메모리 전용 | 지속 저장만 보강 |
| 처리 큐 | 방별 mailbox/FIFO | 모든 방이 하나의 actor 공유 | 방별 큐 보강 필요 |
| 운영 설정 | Android UI/SharedPreferences | 환경변수와 루트 파일 | 환경변수 방식 유지 |
| 미디어 | Mac 생성 후 공개 링크 | Iris 직접 이미지·영상·음원 전송 가능 | Iris 방식이 단순 |

## 4. old-bot 구성 요소별 분석

### 4.1 Android 자동응답 코어

주요 구성:

- `WakeWordParser`: 호출어와 구두점 정규화
- `ResponseTargetFilter`: 방·발신자 정책
- `RecentMessageGuard`: 8초 중복 방지
- `RoomBurstThrottle`: 방별 30초 3건 제한
- `ReplySanitizer`: 민감정보 형식 차단
- `HandleIncomingNotificationUseCase`: 전체 인텐트 우선순위와 실행
- `InMemoryMonitoringStore`: 최근 요청·성공·실패·latency 기록

적용 가치:

- 중복 방지, 방별 burst 제한, 관리자 권한 분리는 new-bot에 직접적인 가치가 있다.
- 호출어 파서는 `헤이봇` 단일 호출어를 유지하면서 제로폭 문자·전각 구두점만 보강할 수 있다.
- old-bot의 방 이름과 닉네임 기반 정책보다 new-bot의 `chat_id`·`user_id` exact match가 안전하다.

주의:

- `HandleIncomingNotificationUseCase`가 4,735줄인 God Object다.
- 기능을 계속 추가한 결과 라우팅, 네트워크, 응답, 모니터링이 한 파일에 집중되어 있다.
- new-bot에는 이 구조를 복사하지 말고 작은 `CommandRouter`와 기능별 handler로 분리해야 한다.

### 4.2 대화 메모리

old-bot 기본 대화 메모리:

- 방별 최근 4턴
- 30분 TTL
- 성공적으로 전송된 답변만 저장
- 암호화 SharedPreferences에 저장하여 앱 재시작 후 복원

new-bot 현재 메모리:

- `chat_id`별 최근 4턴
- 30분 TTL
- 성공적으로 전송된 답변만 저장
- Iris 재시작 시 소멸

적용 제안:

- old-bot의 “성공 전송 후 commit” 원칙은 현재도 지켜지고 있으므로 유지한다.
- `chat_id` 기반 JSON 또는 SQLite 저장을 추가한다.
- 루트 전용 파일을 사용하고 권한을 `600`으로 제한한다.
- 오픈채팅에서는 방 전체 공유 문맥과 사용자별 문맥을 선택 가능하게 한다.
- 기본값은 `(chat_id, user_id)` 사용자별 문맥을 권장한다.

### 4.3 호출어 없는 일반대화 모드

old-bot은 방별로 30분 동안 일반대화 모드를 활성화할 수 있다.

- `코비서 일반대화 시작/상태/멈춰`
- 모델이 `REPLY`, `WAIT`, `IGNORE` JSON을 반환
- 사람끼리의 잡담에는 답하지 않도록 arbitration 수행
- 최신 입력 epoch 검증으로 오래된 응답 전송 차단
- 15초 cooldown과 짧은 문맥 사용

new-bot 적용 가능성:

- `헤이봇 대화 시작`, `헤이봇 대화 상태`, `헤이봇 대화 종료`로 재구현 가능하다.
- 단, 공개 오픈채팅에서 잘못 답할 위험과 GLM 요청량 증가가 있으므로 기본 OFF가 적합하다.
- 방 전체가 아니라 명령을 실행한 `user_id`의 대화만 활성화하는 방식이 안전하다.

### 4.4 방별 큐와 응답 세션

old-bot은 방별 mailbox, FIFO, 세션 TTL, stale 결과 차단을 구현한다.

new-bot에 필요한 부분:

- 방별 FIFO
- 방별 최대 대기 개수
- 오래된 요청 취소 또는 폐기
- 전체 GLM 동시 호출 수 제한
- 한 방의 느린 요청이 다른 방을 막지 않도록 공정성 확보

new-bot에 불필요한 부분:

- `RemoteInput`
- Reply Action capability 캡처
- pinned lease
- 알림 세션 재사용

현재 new-bot은 세 방의 메시지를 단일 actor에서 처리한다. GLM 호출이 최대 120초 지연되면 다른 두 방도 함께 대기한다. 다중 방 운영에서 가장 먼저 개선해야 할 구조다.

### 4.5 사용자 권한과 관리 명령

old-bot은 일반 사용자 호출과 설정 변경 권한을 분리한다. 다만 표시 이름의 부분 문자열 비교와 방 이름 하드코딩이 섞여 있다.

new-bot 권장 방식:

- 일반 호출 허용: `chat_id`
- 관리자 명령 허용: exact `user_id`
- 선택적 사용자 제한: `chat_id -> allowed user_id set`
- 상태 변경 명령은 관리자만 허용
- 설정값과 API 키는 응답이나 로그에 노출하지 않음

현재 세 오픈방에서는 모든 참여자가 GLM을 호출할 수 있으므로 비용·도배 방지 측면에서 우선 적용 가치가 높다.

### 4.6 도움말·상태·자체 점검

old-bot은 기능 카탈로그와 실제 gateway를 통과하는 self-test를 제공한다.

new-bot 적용 명령 예:

- `헤이봇 도움말`
- `헤이봇 상태`
- `헤이봇 기억 초기화`
- `헤이봇 내 기억 초기화`
- `헤이봇 모델 상태`

상태 응답에 포함할 항목:

- 실행 여부
- 모델명
- 활성 방 수
- 현재 방 큐 길이
- 최근 성공 시각
- 최근 오류 종류
- 마지막 latency

제외할 항목:

- API 키
- 키 파일 경로
- 원문 예외 메시지
- 다른 방 이름과 대화 내용

### 4.7 주식·시장 기능

old-bot `stock_proxy`는 다음을 지원한다.

- 국내 종목: Naver Finance 비공식 공개 엔드포인트
- 해외 종목: Alpha Vantage
- 보조 시세·캔들: Yahoo Finance
- 선물·원자재: Barchart 공개 페이지
- VIX 참고: CBOE
- 단일 종목, 비교, 섹터, ETF, 선물, 거시 질의
- 수집 데이터 기반 LLM 요약

현재 활성 방 중 두 곳이 주식방이므로 기능 가치가 매우 높다.

권장 적용:

1. `StockIntentParser`로 주식 질문을 일반 GLM 대화보다 먼저 분류
2. 실제 시세 데이터 수집
3. 데이터·기준 시각·출처를 GLM 프롬프트에 함께 제공
4. 응답에 기준 시각과 지연 가능성을 표시
5. 수집 실패 시 숫자를 추측하지 않고 전용 실패 문구 반환

주의:

- Naver/Yahoo/Barchart 경로는 비공식이므로 변경과 rate limit에 취약하다.
- 주식 수치는 GLM 지식만으로 답하게 하면 안 된다.
- Android 단독을 유지하려면 최소 국내 시세 기능만 직접 구현하고, 복잡한 거시·해외 데이터는 선택형 프록시로 분리하는 편이 낫다.

### 4.8 날씨·뉴스 기능

old-bot:

- 날씨: Open-Meteo
- 뉴스: Google News RSS와 fallback

new-bot 적용성:

- 외부 유료 LLM 도구 없이 구현 가능
- PD20에서 직접 HTTP 호출 가능
- 응답이 짧고 데이터 구조가 단순
- 기능별 실패를 일반 대화로 넘기지 않는 것이 안전

권장 명령:

- `헤이봇 서울 날씨`
- `헤이봇 경제 뉴스`
- `헤이봇 IT 뉴스`

### 4.9 URL 요약

old-bot `summary_proxy`:

- YouTube 자막 우선, metadata fallback
- GitHub README 우선
- Web article/main 본문 우선
- 사설 IP, localhost, credential URL, 파일 URL 차단

new-bot 적용 제안:

- GitHub와 일반 웹페이지 요약부터 적용
- URL fetch 전에 SSRF 차단 필수
- 응답 크기·redirect 횟수·timeout 제한
- YouTube 자막은 변동성이 크므로 후순위

### 4.10 대화 아카이브와 검색

old-bot:

- 지정 방 메시지 비동기 수집
- 방별 SQLite
- 7일 보관
- 요약·통계·주제 검색
- 선택적 RAG

new-bot의 장점:

- Iris가 이미 카카오 DB와 `chat_id`에 접근한다.
- 별도 Notification outbox 없이 직접 읽을 수 있다.

권장 적용:

- 명시적으로 opt-in한 방만 대상
- 호출어 없는 대화 수집 여부를 별도 설정
- 우선 `최근 1시간/오늘 요약`을 DB에서 on-demand 조회
- 메시지 수·문자 수 제한 후 GLM에 전달
- 다른 방과 데이터가 섞이지 않도록 `chat_id` exact scope 사용
- 보관·삭제·관리자 권한·개인정보 안내를 먼저 설계

### 4.11 예약·선제 발송

old-bot은 AlarmManager와 살아 있는 Reply Action 세션을 결합한다.

new-bot은 `chat_id` 직접 전송이 가능하므로 Reply Action 세션은 필요하지 않다. 다만 Iris 프로세스가 종료되면 예약 실행도 중단될 수 있다.

적용 조건:

- 예약 데이터 영속화
- 재부팅 후 Iris 자동 시작
- 누락된 예약의 catch-up 정책
- 관리자 전용 생성·삭제
- 방별 전송 제한

### 4.12 이미지·영상·HTML·작업 기능

old-bot은 Mac의 Codex CLI, Remotion, Cloudflare Quick Tunnel을 사용한다.

현재 new-bot에는 즉시 이식하지 않는 것이 좋다.

- Android 단독 요구와 맞지 않음
- Mac 상시 실행 의존성 발생
- launchd, tunnel, publish server까지 운영 범위 확대
- 현재 Iris 자체 이미지·영상·음원 전송은 이미 확인됨
- 필요한 것은 “생성 백엔드”이지 전송 계층이 아님

## 5. 핵심 이슈

### P1. 단일 actor로 인한 방 간 블로킹

세 방이 하나의 GLM 큐를 공유한다. 느린 요청 하나가 모든 방을 막을 수 있다.

### P1. 공개방 사용자 제한 부재

허용 방 안에서는 누구나 `헤이봇`을 호출할 수 있다. 방별·사용자별 quota와 관리자 권한이 필요하다.

### P1. 메모리 재시작 소실

최근 문맥이 프로세스 재시작 때 사라진다.

### P1. 주식 질문의 실시간 근거 부재

주식방에서 일반 GLM 지식만으로 현재 시세나 시장 상황을 답하면 정확성 문제가 발생할 수 있다.

### P2. 방 전체 공유 문맥

현재 `chat_id` 단위이므로 서로 다른 참여자의 질문이 같은 문맥에 섞인다.

### P2. 운영 상태 확인 수단 부족

API가 살아 있는지는 확인할 수 있지만 GLM 큐, 최근 실패, room별 처리 상태를 한 번에 확인하기 어렵다.

### P2. old-bot 라이선스 제한

old-bot `LICENSE`는 검토·참조만 허용하고, 별도 서면 합의 없이는 복사·수정·운영·배포 권한을 부여하지 않는다.

따라서 권리 관계가 별도로 확인되지 않는 한:

- old-bot 코드를 직접 복사하지 않는다.
- 기능 개념과 운영 요구사항을 바탕으로 new-bot에서 새로 구현한다.
- 직접 이식이 필요하면 먼저 Coreline AI의 별도 사용 허가를 확인한다.

## 6. 권장 적용 우선순위

### P1: 즉시 적용 권장

| 순서 | 기능 | 효과 | 구현 난이도 |
|---:|---|---|---|
| 1 | 방별 큐 + 전체 동시성 제한 | 방 간 블로킹 방지 | 중 |
| 2 | 방별 burst throttle + 사용자 quota | 공개방 도배·비용 방지 | 하 |
| 3 | 관리자 `user_id` allowlist | 설정 명령 보호 | 하 |
| 4 | 지속 메모리 + 기억 초기화 | 재시작 후 문맥 복원 | 중 |
| 5 | 도움말·상태·구조화 모니터링 | 운영 진단 단순화 | 중 |
| 6 | 사용자별 문맥 선택 | 오픈방 문맥 혼선 방지 | 중 |

### P2: 실사용 가치가 높은 기능

| 순서 | 기능 | 권장 범위 |
|---:|---|---|
| 1 | 국내 주식 데이터 응답 | 국내 종목·한국 시장부터 |
| 2 | 날씨 | Open-Meteo 직접 호출 |
| 3 | 뉴스 | 경제·IT RSS 브리핑 |
| 4 | GitHub/Web 요약 | SSRF 차단 포함 |
| 5 | 호출어 없는 일반대화 모드 | 관리자 활성화, 사용자 단위, 기본 OFF |
| 6 | 최근 대화 요약 | opt-in 방, on-demand, 짧은 보관 |

### P3: 운영 기반 확장 후 검토

- 예약·선제 발송
- 다중 LLM provider와 fallback
- 이미지 자동 생성
- 인포그래픽·문서 HTML
- 장기 RAG

### 적용 제외 권장

- NotificationListener/RemoteInput 전송 계층
- Reply Action/pinned session 관리
- 루팅 탐지 후 자동응답 차단
- NDK에 API 키를 분할 임베딩하는 방식
- old-bot의 방 이름·닉네임 하드코딩
- 전체 Mac 프록시 스택 일괄 이식
- 게임·웹 포털·영상 작업 시스템 일괄 이식

## 7. 권장 new-bot 구조

```text
Iris DB Observer
  └─ IncomingMessageNormalizer
      └─ BotPolicy
          ├─ allowed chat_id
          ├─ allowed/admin user_id
          ├─ duplicate guard
          └─ room/user throttle
              └─ CommandRouter
                  ├─ HelpStatusHandler
                  ├─ MemoryControlHandler
                  ├─ StockHandler
                  ├─ WeatherHandler
                  ├─ NewsHandler
                  ├─ LinkSummaryHandler
                  └─ GeneralGlmHandler
                      └─ RoomQueueManager
                          ├─ per-room FIFO
                          ├─ global concurrency limit
                          └─ stale request guard
                              └─ Iris Replier
```

권장 저장소:

```text
/data/local/private/
  iris-glm.token
  iris-bot-settings.json
  iris-bot-memory.json
  iris-bot-metrics.json
```

민감 파일은 `root:root`, 권한 `600`을 유지한다.

## 8. 단계별 구현안

### 1단계: 운영 안전성

1. 현재 GLM handler를 policy, queue, memory, router로 분리
2. 방별 throttle과 사용자 quota 추가
3. 관리자 `user_id` 설정 추가
4. 사용자별 문맥 옵션 추가
5. 테스트와 PD20 3개 방 회귀 검증

### 2단계: 지속 상태와 진단

1. 원자적 JSON 저장 또는 SQLite 저장
2. `도움말`, `상태`, `기억 초기화`
3. latency·queue·error metrics
4. 재시작 후 복원 테스트

### 3단계: 데이터 기능

1. 날씨
2. 국내 주식
3. 뉴스
4. GitHub/Web 요약

각 기능은 일반 GLM보다 먼저 명령을 분류하고, 데이터 수집 실패 시 일반 LLM 추측으로 내려가지 않는다.

### 4단계: 대화 확장

1. 사용자 단위 일반대화 세션
2. 최근 대화 요약 opt-in
3. 예약 전송

## 9. 종합 평가

old-bot은 기능과 운영 자동화가 풍부하지만 현재 Iris 기반 헤이봇에 그대로 합치기에는 지나치게 크고, 전송 구조도 다르다.

가장 가치 있는 자산은 다음 세 가지다.

1. 공개방 운영 안전성: 중복·burst·권한·stale 응답 차단
2. 명령 라우팅: 일반 LLM보다 근거 기반 기능을 먼저 처리
3. 운영 진단: 상태·self-test·실패 분류

현재 new-bot은 직접 `chat_id`와 `user_id`를 확보하고 Iris로 바로 전송하므로 old-bot보다 핵심 경로가 단순하다. 이 장점을 유지하면서 P1 안정성 기능과 주식 데이터 기능만 선별적으로 새로 구현하는 것이 가장 적절하다.

## 10. 분석 검증 범위

- old-bot Git 상태: `main`, `origin/main`과 동일, working tree clean
- old-bot HEAD: `ff24ff55755fee376dd6df21eba17520c640e61a`
- new-bot Iris branch: `feature/glm-autoreply`
- new-bot Iris GLM 변경사항: 아직 미커밋
- 정적 소스, 문서, Git 이력, 파일·라인·테스트 파일 수 분석 완료
- old-bot 전체 프록시 기동과 Android 빌드는 이번 분석에서 실행하지 않음

