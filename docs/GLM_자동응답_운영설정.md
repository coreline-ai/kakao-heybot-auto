# Iris GLM 자동응답 운영 설정

## 목적

이 문서는 PD20에서 root로 실행되는 Iris에 Z.AI GLM 자동응답을 제한적으로 활성화하는 절차를 기록한다.

- 수신: Iris가 카카오톡 DB에서 복호화한 새 텍스트 메시지
- 조건: 허용된 4개 오픈방의 실제 `chat_id`와 `헤이봇` 호출어가 모두 일치
- 추론: Z.AI 일반 Chat Completion API
- 발신: 기존 Iris `Replier.sendMessage()` → 카카오톡 내부 `NotificationActionService`

`iris_bot`, Python 서버, Android NotificationListenerService는 필요하지 않다.

## 안전 기본값

- `IRIS_GLM_ENABLED=true`를 명시하지 않으면 GLM은 비활성화된다.
- API Key/토큰은 Iris JSON 설정 파일, APK, BuildConfig, Git에 저장하지 않는다.
- 수신 메시지는 `헤이봇` 호출어가 있을 때만 외부 Z.AI API에 전달된다. 단, 코어라인 AI 연구소 관리자가 전역 일반대화 모드를 켠 동안 별도의 일반대화 3개 방 allowlist 안의 일반 텍스트는 `REPLY`·`WAIT`·`IGNORE` 판정용으로만 전달될 수 있다.
- GLM 오류·시간 초과·429는 카카오톡에 자동 오류 메시지를 보내지 않는다.
- Z.AI SSE 스트리밍 응답을 내부에서 한 문장으로 합친 뒤 최종 텍스트 한 건만 전송한다. 스트리밍 토큰을 여러 메시지로 전송하지 않는다.
- 429는 `Retry-After` 헤더가 있으면 이를 우선하고, 없으면 15초·30초 간격으로 최대 `IRIS_GLM_RATE_LIMIT_RETRIES`회 재시도한다.
- 방마다 FIFO Queue와 worker를 분리하고, 전체 GLM 호출은 기본 2건까지만 병렬 실행한다.
- 방별 30초 3회, 사용자별 60초 5회 제한과 8초 동일 메시지 중복 차단을 Queue 등록 전에 적용한다.
- 대화 기억은 `(chat_id,user_id)`별 최근 4턴·30분이며 root 전용 파일에 원자적으로 저장한다.
- 관리자 명령은 닉네임이 아니라 Kakao DB의 숫자 `user_id` exact match로만 허용하며, 코어라인 AI 연구소 control room에서만 실행한다.
- 호출어·일반대화의 GLM 텍스트는 전송 직전 동일한 safety policy를 통과한다. secret-like 출력은 전체 차단하고 이메일·전화·주민번호·카드번호 형태는 고정 문구로 마스킹한다.
- 일반대화의 timeout·429·network·server 실패가 5분 안에 3건 누적되면 일반대화 mode만 자동 OFF한다. 호출어 GLM·이미지·Iris 수동 `/reply`는 계속 동작한다.

## 사전 조건

- Iris GLM 빌드 APK가 PD20의 `/data/local/tmp/Iris-glm.apk`에 있어야 한다.
- 카카오톡과 Iris가 root로 정상 기동되어 있어야 한다.
- 신규 발급한 Z.AI API Key 또는 짧은 수명의 Bearer/JWT를 준비한다.
- 키를 채팅, 소스, `config.json`, shell history, Git에 기록하지 않는다.

루팅된 단말은 장기 API Key를 완전히 보호할 수 없다. 운영 전에는 Token Broker가 발급한 짧은 수명의 토큰 사용을 권장한다.

## 비밀 파일 주입

아래 절차는 **새로 발급한 키**를 root 전용 파일로 입력한다. 실제 키 문자열은 이 문서나 명령어 예시에 넣지 않는다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell
su root
mkdir -p /data/local/private
chmod 700 /data/local/private
umask 077
cat > /data/local/private/iris-glm.token
# 여기서 새 토큰을 한 줄로 붙여넣고 Ctrl+D를 누른다.
chmod 600 /data/local/private/iris-glm.token
exit
exit
```

파일에는 다음 중 하나만 넣는다.

```text
<Z.AI API Key>
```

또는

```text
Bearer <short-lived token>
```

Iris는 앞에 `Bearer `가 없으면 자동으로 붙인다. 파일 내용과 토큰 값은 출력하거나 `cat`으로 확인하지 않는다.

### 관리자 ID 파일

관리자로 허용할 숫자 `user_id`를 한 줄에 하나씩 저장한다. 닉네임이나 프로필명은 사용할 수 없다.
실제 ID를 모르면 먼저 Iris의 수신 DB/API에서 본인이 보낸 메시지의 `user_id`를 확인한다.

```bash
"$ADB" -s "$SERIAL" shell
su root
umask 077
cat > /data/local/private/iris-bot-admins.txt
# 숫자 user_id를 한 줄에 하나씩 입력한 뒤 Ctrl+D
chmod 600 /data/local/private/iris-bot-admins.txt
exit
exit
```

파일이 없거나 읽을 수 없으면 GLM 대화는 계속 동작하지만 관리자 명령은 모두 비활성화된다. ID 값은 이 문서·Git·로그에 쓰지 않는다.
로그에는 관리자 ID 목록이나 대화 원문을 출력하지 않는다.

### 일반대화 사용자 block 파일

일반대화에서만 제외할 숫자 ID를 root 전용 파일에 저장한다. 빈 파일은 차단 대상이 없다는
뜻이며 호출어 질문에는 영향을 주지 않는다.

```bash
"$ADB" -s "$SERIAL" shell
su root
umask 077
touch /data/local/private/iris-general-conversation-blocks.txt
chmod 600 /data/local/private/iris-general-conversation-blocks.txt
exit
exit
```

허용 형식은 한 줄에 하나이며 공백·중복·음수·0·overflow·그 밖의 구문은 허용하지 않는다.

```text
<userId>
<chatId>:<userId>
```

- `<userId>`: 모든 일반대화 허용방에서 해당 사용자를 제외
- `<chatId>:<userId>`: 지정 방의 해당 사용자만 제외
- 파일 손상·읽기 실패·잘못된 권한에서는 일반대화만 fail-closed되고 호출어 기능은 유지
- 정책 변경은 원자적 파일 교체 후 Iris 재기동으로 반영
- ID 원문은 status·설정 보기·로그에 출력하지 않음

## 실행 설정

| 환경 변수 | 설정값 | 역할 |
|---|---|---|
| `IRIS_GLM_ENABLED` | `true` | 자동응답 활성화 |
| `IRIS_GLM_BASE_URL` | `https://api.z.ai/api/paas/v4/` | Z.AI 일반 API base URL |
| `IRIS_GLM_MODEL` | `glm-4.5-flash` | PD20 실측 평균 1.19초의 주 모델 |
| `IRIS_GLM_FALLBACK_MODEL` | 미설정 | 필요할 때만 주 모델의 429 또는 시간 초과용 대체 모델을 지정 |
| `IRIS_GLM_TRIGGER` | `헤이봇` | 메시지 앞 호출어 |
| `IRIS_GLM_ALLOWED_CHAT_IDS` | `18480337854645134,18226456888539938,18243496625741211,18393359886930036` | 코어라인 AI 연구소, 주식 오픈채팅방 2곳, 윤자동과 함께 하는 업무자동화 |
| `IRIS_GLM_API_KEY_FILE` | `/data/local/private/iris-glm.token` | root 전용 비밀 파일 |
| `IRIS_GLM_TIMEOUT_MS` | `120000` | API 전체 호출 시간 제한 |
| `IRIS_GENERAL_CONVERSATION_TIMEOUT_MS` | `15000` | 호출어 없는 일반대화 판정 전용 시간 제한. 느린 판정이 방 worker를 장시간 점유하지 않게 함 |
| `IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS` | `18480337854645134,18226456888539938,18243496625741211` | 호출어 없는 일반대화에만 적용하는 명시적 방 allowlist |
| `IRIS_GENERAL_CONVERSATION_BLOCK_FILE` | `/data/local/private/iris-general-conversation-blocks.txt` | 전역 `userId` 또는 `chatId:userId` block, `600 root:root` |
| `IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS` | `300000` | 일반대화 외부 장애 sliding window |
| `IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD` | `3` | window 안에서 일반대화를 자동 OFF할 장애 횟수 |
| `IRIS_GLM_MAX_TOKENS` | `128` | 일반 대화 최종 답장의 최대 생성 토큰 |
| `IRIS_GLM_TEMPERATURE` | `0.2` | 응답 다양성 |
| `IRIS_GLM_RATE_LIMIT_RETRIES` | `2` | HTTP 429일 때 최대 재시도 횟수 |
| `IRIS_GLM_ROOM_QUEUE_CAPACITY` | `8` | 방별 대기 Queue 상한, 범위 1~100 |
| `IRIS_GLM_TOTAL_QUEUE_CAPACITY` | `24` | 전체 대기 Queue 상한, 방별 상한 이상·최대 500 |
| `IRIS_GLM_MAX_CONCURRENCY` | `2` | 전체 동시 GLM 호출 상한, 범위 1~16 |
| `IRIS_GLM_ROOM_RATE_WINDOW_MS` | `30000` | 방별 호출 제한 window |
| `IRIS_GLM_ROOM_RATE_MAX` | `3` | window당 방별 허용 건수 |
| `IRIS_GLM_USER_RATE_WINDOW_MS` | `60000` | 사용자별 호출 제한 window |
| `IRIS_GLM_USER_RATE_MAX` | `5` | window당 사용자별 허용 건수 |
| `IRIS_GLM_DUPLICATE_WINDOW_MS` | `8000` | 동일 방·사용자·정규화 메시지 중복 차단 |
| `IRIS_GLM_MEMORY_FILE` | `/data/local/private/iris-bot-memory.json` | 원자적 대화 기억 파일 |
| `IRIS_GLM_MEMORY_MAX_TURNS` | `4` | `(chat_id,user_id)`별 최근 turn 수 |
| `IRIS_GLM_MEMORY_TTL_MS` | `1800000` | 대화 기억 TTL 30분 |
| `IRIS_GLM_MEMORY_MAX_BYTES` | `1048576` | 기억 JSON 최대 1 MiB |
| `IRIS_GLM_MEMORY_MAX_CONVERSATIONS` | `512` | 최대 대화 key 수 |
| `IRIS_BOT_ADMIN_USER_IDS_FILE` | `/data/local/private/iris-bot-admins.txt` | root 전용 관리자 ID 목록 |
| `IRIS_BOT_ADMIN_CONTROL_CHAT_ID` | `18480337854645134` | 관리자 설정·전역 일반대화 제어가 가능한 코어라인 AI 연구소 방 |

현재 운영은 실측 성공·최저 지연 모델인 `glm-4.5-flash`만 사용한다. 대체 모델을
지정하면 주 모델의 용량 제한(HTTP 429) 또는 시간 초과일 때만 시도한다.

기동 명령:

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[p]arty.qwer.iris\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  rm -f /data/local/tmp/iris-glm-startup.log
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18226456888539938,18243496625741211,18393359886930036 \\
  IRIS_GLM_API_KEY_FILE=/data/local/private/iris-glm.token \\
  IRIS_GLM_TIMEOUT_MS=120000 \\
  IRIS_GENERAL_CONVERSATION_TIMEOUT_MS=15000 \\
  IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS=18480337854645134,18226456888539938,18243496625741211 \\
  IRIS_GENERAL_CONVERSATION_BLOCK_FILE=/data/local/private/iris-general-conversation-blocks.txt \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS=300000 \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD=3 \\
  IRIS_GLM_MAX_TOKENS=128 \\
  IRIS_GLM_TEMPERATURE=0.2 \\
  IRIS_GLM_RATE_LIMIT_RETRIES=2 \\
  IRIS_GLM_ROOM_QUEUE_CAPACITY=8 \\
  IRIS_GLM_TOTAL_QUEUE_CAPACITY=24 \\
  IRIS_GLM_MAX_CONCURRENCY=2 \\
  IRIS_GLM_ROOM_RATE_WINDOW_MS=30000 \\
  IRIS_GLM_ROOM_RATE_MAX=3 \\
  IRIS_GLM_USER_RATE_WINDOW_MS=60000 \\
  IRIS_GLM_USER_RATE_MAX=5 \\
  IRIS_GLM_DUPLICATE_WINDOW_MS=8000 \\
  IRIS_GLM_MEMORY_FILE=/data/local/private/iris-bot-memory.json \\
  IRIS_GLM_MEMORY_MAX_TURNS=4 \\
  IRIS_GLM_MEMORY_TTL_MS=1800000 \\
  IRIS_GLM_MEMORY_MAX_BYTES=1048576 \\
  IRIS_GLM_MEMORY_MAX_CONVERSATIONS=512 \\
  IRIS_BOT_ADMIN_USER_IDS_FILE=/data/local/private/iris-bot-admins.txt \\
  IRIS_BOT_ADMIN_CONTROL_CHAT_ID=18480337854645134 \\
  CLASSPATH=/data/local/tmp/Iris-glm.apk \\
  app_process / party.qwer.iris.Main \\
  > /data/local/tmp/iris-glm-startup.log 2>&1 &
'"
```

성공적으로 설정을 읽으면 시작 로그에 아래 한 줄이 나타난다.

```text
GLM auto-reply enabled
General conversation policy ready=true rooms=3 reason=READY
GLM P1 scheduler ready (concurrency=2, roomQueue=8, totalQueue=24)
Conversation memory ready (conversations=..., turns=...)
```

토큰 원문·프롬프트 원문·응답 원문은 시작 로그에 기록하지 않는다.

### 안전 기동 스크립트

위 설정값은 프로젝트의 `scripts/start_iris_glm_pd20.sh`에도 고정되어 있다. 이 스크립트는
토큰을 읽거나 출력하지 않고, 토큰 파일의 존재·권한(`600 root:root`)만 확인한 뒤 APK 배포,
분리된 중지/기동, `/config` 헬스 체크를 수행한다.

```bash
chmod +x scripts/start_iris_glm_pd20.sh
scripts/start_iris_glm_pd20.sh
```

기본값은 현재 PD20 serial, 현재 릴리스 APK 및 본 문서의 채팅방/모델 설정이다. 다른 ADB나
단말을 명시해야 할 때에만 다음처럼 환경 변수를 사용한다.

```bash
ADB=/path/to/adb SERIAL=<serial> APK=/path/to/Iris-release.apk \
  scripts/start_iris_glm_pd20.sh
```

스크립트는 `/data/local/private`가 `700 root:root`, 토큰·기존 기억·기존 관리자·일반대화
block 파일이 `600 root:root`인지 내용 노출 없이 검사한다. block 파일이 없으면 빈 root 전용
파일을 생성한다. 관리자 파일이 없으면 경고 후 기동하며 관리자 명령만 비활성화된다.

## 카카오 명령

| 명령 | 권한 | 처리 경로 |
|---|---|---|
| `헤이봇 <질문>` | 허용된 방의 일반 사용자 | 방별 FIFO → 전체 동시성 제한 → GLM |
| `헤이봇 도움말` | 일반 사용자 | 로컬 즉시 응답, GLM 미호출 |
| `헤이봇 내 기억 초기화` | 일반 사용자 | 현재 `(chat_id,user_id)` 기억만 삭제 |
| `헤이봇 상태` | 코어라인 AI 연구소의 관리자 | Queue·latency·성공/실패·제한·기억 상태 |
| `헤이봇 설정 보기` | 코어라인 AI 연구소의 관리자 | 비밀을 제외한 운영값 요약 |
| `헤이봇 전체 기억 초기화` | 코어라인 AI 연구소의 관리자 | 모든 대화 기억 삭제·저장 |
| `헤이봇 사용자 기억 초기화 <user_id>` | 코어라인 AI 연구소의 관리자 | 대상 사용자의 모든 방 기억 삭제·저장 |
| `헤이봇 대화 시작` | 코어라인 AI 연구소의 관리자 | 일반대화 allowlist 3개 방의 호출어 없는 판정 시작, circuit reset |
| `헤이봇 대화 상태` | 코어라인 AI 연구소의 관리자 | mode·정책·적용 방 수·circuit·최근 generic 사유 확인 |
| `헤이봇 대화 종료` | 코어라인 AI 연구소의 관리자 | 모든 일반대화 허용방의 호출어 없는 판정 즉시 중단 |

관리자 명령은 exact `user_id`와 `IRIS_BOT_ADMIN_CONTROL_CHAT_ID`가 모두 일치해야 한다. 다른 방의 관리자 명령은 실행되지 않는다. 로컬 명령은 GLM Queue를 거치지 않으므로 다른 방에서 120초 요청이 실행 중이어도 처리된다.
미인가 사용자는 관리자 목록을 알 수 없고 단순 거부 메시지만 받는다.

## Queue·호출 제한 동작

1. 텍스트·허용방·봇 본인 메시지 여부를 확인한다.
2. 로컬 명령이면 즉시 처리한다.
3. GLM 질문이면 중복 `log_id`와 8초 동일 메시지를 차단한다.
4. 방별·사용자별 sliding window 제한을 적용한다.
5. 방별 FIFO Queue에 등록하고 전체 GLM semaphore를 획득할 때만 API를 호출한다.
6. 방별 또는 전체 Queue가 가득 차면 GLM을 호출하지 않고 잠시 후 재시도 안내를 보낸다.

동일 메시지는 `trim → 연속 공백 축소 → 소문자화 → SHA-256` key만 메모리에 보관한다.
운영 로그에는 사용자 메시지 원문·GLM 응답 원문·Authorization 값이 남지 않는다.

## 응답 safety와 일반대화 circuit

모델이 반환한 호출어 답변과 일반대화 `REPLY`는 카카오 전송 직전에 다음 단일 경계를 통과한다.

1. `<think>`와 code fence를 제거하고 공백을 정규화한다.
2. Bearer/Authorization, API key·token·secret/password assignment, `IRIS_*=` 값,
   `/data/local/private`, root credential 형태가 있으면 답변 전체를 보내지 않는다.
3. 이메일·전화번호·주민등록번호·카드번호 형태는 각각 고정된 마스킹 문구로 치환한다.
4. 빈 결과는 보내지 않고 최종 텍스트는 480자로 제한한다.

차단된 원문과 개인정보 원문은 로그·metric에 저장하지 않는다. 상태에는 안전 차단·마스킹
누계만 표시한다. 로컬 도움말·관리자 상태와 이미지 bytes는 이 모델 출력 sanitizer 대상이 아니다.

일반대화 circuit은 일반대화 외부 GLM 호출에서 발생한 timeout·429·network·server 오류만
센다. 기본 5분 window에서 3번째 실패가 발생하면 mode epoch를 무효화하고 일반대화만 OFF한다.
대기·진행 중이던 오래된 일반대화 결과도 전송하지 않는다. 자동 재활성화는 하지 않으며
코어라인 AI 연구소 관리자가 `헤이봇 대화 시작`을 다시 실행하면 실패 window를 초기화한다.

## 제한 E2E 테스트

1. PD20의 봇 계정이 아닌 다른 카카오 계정으로 코어라인 AI 연구소 방에 다음을 전송한다.

   ```text
   헤이봇 안녕
   ```

2. 카카오톡 UI에서 최종 텍스트 답장이 정확히 한 건 발신되는지 확인한다.
3. 다음은 답장 0건이어야 한다.
   - `안녕`처럼 호출어 없는 메시지
   - 봇 계정이 보낸 메시지
   - 허용되지 않은 채팅방의 `헤이봇` 호출
   - 빈 `헤이봇`, `헤이봇:` 메시지
4. 토큰을 임시로 잘못된 값으로 바꾼 뒤 401을 만들었을 때, Iris 프로세스가 유지되고 카카오톡 오류 메시지를 보내지 않는지 확인한다.

## 중지 및 롤백

일반대화만 즉시 끄려면 코어라인 AI 연구소의 exact 관리자가 다음 명령을 보낸다.

```text
헤이봇 대화 종료
```

재기동 뒤에도 일반대화를 구성하지 않으려면 수동 기동 환경에서
`IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS`를 제거한다. 그러면 일반대화 policy가
fail-closed되어 `헤이봇 대화 시작`을 거부하지만 호출어 GLM과 이미지 기능은 유지된다.
현재 mode는 process-local이라 Iris 재기동만 해도 기본 OFF로 시작한다.

GLM만 비활성화하려면 GLM 관련 환경 변수를 빼고 동일 APK를 재기동한다. 기존 Iris 수동 `/reply`, DBObserver, `/query` 기능은 계속 사용할 수 있다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[p]arty.qwer.iris\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  rm -f /data/local/tmp/iris-glm-startup.log
  CLASSPATH=/data/local/tmp/Iris-glm.apk \\
  app_process / party.qwer.iris.Main \\
  > /data/local/tmp/iris-glm-startup.log 2>&1 &
'"
```

기동 로그의 `GLM auto-reply disabled`를 확인한다.

## 장애 코드

| 증상 | 의미 | 조치 |
|---|---|---|
| `GLM auto-reply disabled: ...` | 환경 변수 형식 또는 필수 값 누락 | 키를 제외한 환경 변수와 chat_id 형식을 확인 |
| `GlmFailure.Unauthorized` | 토큰/키 인증 실패 | 키를 재발급하거나 짧은 토큰을 갱신 |
| `GlmFailure.RateLimited` | API 사용량 또는 속도 제한 | 잠시 대기하고 Z.AI 사용량 정책 확인 |
| `GlmFailure.Timeout` | 호출어 질문 120초 또는 일반대화 판정 15초 안에 완료되지 않음 | `kind`·`elapsedMs`·`budgetMs` 진단 로그로 경로를 구분하고 네트워크·Z.AI 상태 확인 |
| 일반대화 mode가 자동으로 꺼짐 | 5분 안에 일반대화 외부 장애 3건으로 circuit trip | `헤이봇 대화 상태`의 generic 사유와 Z.AI 상태 확인 후 `헤이봇 대화 시작`으로 수동 복구 |
| `일반대화 정책이 준비되지 않아...` | allowlist 누락/범위 오류 또는 block 파일 형식·권한 오류 | 시작 로그의 generic policy reason과 root 파일 metadata를 확인 |
| 답장 없음, 오류 로그 없음 | 호출어/방/텍스트 타입 필터에서 제외 | `헤이봇 <질문>` 형식과 허용 chat_id 확인 |
