# proxy-youtube

`헤이봇 유튜브 다운로드 <링크>` 전용의 loopback service다. `yt-dlp`를 고정된 실행 파일과
고정 argv로만 호출하며 single public YouTube video ID, bounded MP4, chat-scoped job/file endpoint만
제공한다. cookie, playlist, arbitrary URL/options, public artifact URL은 지원하지 않는다.

## 품질 균형 출력 규격

서버가 최종 MP4를 작게 만든 뒤 Android는 이를 그대로 카카오톡에 전달한다. Android에서
재인코딩하지 않는다. 카카오톡이 direct-share 이후 다시 처리할 수 있으므로, 서버가 240p처럼
지나치게 작은 입력을 만들지 않는다. 기본 hard limit은 42 MiB, target은 38 MiB이며 긴 영상일수록
안전한 범위에서 출력 규격을 낮춘다.

| 원본 길이 | 최대 해상도 | 영상/음성 평균 비트레이트 |
| --- | --- | --- |
| 3분 이하 | 가로 854×480·세로 480×854, 24fps | 최대 1.1Mbps / AAC 96kbps |
| 3~5분 | 가로 854×480·세로 480×854, 24fps | 최대 880kbps / AAC 96kbps |
| 5~10분 | 가로 640×360·세로 360×640, 24fps | 최대 400kbps / AAC 64kbps |
| 10~15분 | 가로 480×270·세로 270×480, 24fps | 최대 260kbps / AAC 64kbps |

실제 bitrate는 38MiB target VBV ceiling보다 높아지지 않는다. 모든 결과는 H.264 Constrained
Baseline, yuv420p, AAC, faststart MP4다. `maxrate`를 duration별 target으로 제한하므로 고움직임
원본도 CRF 방식처럼 예기치 않게 커지지 않는다.
운영자가 조정할 값은 `YOUTUBE_PROXY_MAX_BYTES`(hard limit)와
`YOUTUBE_PROXY_KAKAO_TARGET_BYTES`(profile target)다.

서버 활성화 전에는 `YOUTUBE_PROXY_YTDLP_BIN`, `YOUTUBE_PROXY_FFPROBE_BIN`,
`YOUTUBE_PROXY_FFMPEG_BIN`의 버전·SHA-256·라이선스
고지를 `THIRD_PARTY.md` 기준으로 검토하고 `scripts/doctor.sh`를 통과시켜야 한다.
