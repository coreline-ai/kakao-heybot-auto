# proxy-youtube

`헤이봇 유튜브 다운로드 <링크>` 전용의 loopback service다. `yt-dlp`를 고정된 실행 파일과
고정 argv로만 호출하며 single public YouTube video ID, bounded MP4, chat-scoped job/file endpoint만
제공한다. cookie, playlist, arbitrary URL/options, public artifact URL은 지원하지 않는다.

## Kakao-lite 출력 규격

서버가 최종 MP4를 작게 만든 뒤 Android는 이를 그대로 카카오톡에 전달한다. Android에서
재인코딩하지 않는다. 기본 hard limit은 20 MiB, target은 18 MiB이며 긴 영상일수록 자동으로
출력 규격을 낮춘다.

| 원본 길이 | 최대 해상도 | 영상/음성 평균 비트레이트 |
| --- | --- | --- |
| 5분 이하 | 480×270, 24fps | 최대 350kbps / mono AAC 48kbps |
| 5~10분 | 426×240, 24fps | 최대 180kbps / mono AAC 40kbps |
| 10~15분 | 320×180, 24fps | 최대 110kbps / mono AAC 32kbps |

모든 결과는 H.264 Constrained Baseline, yuv420p, AAC, faststart MP4다. `maxrate`를
duration별 target으로 제한하므로 고움직임 원본도 CRF 방식처럼 예기치 않게 커지지 않는다.
운영자가 조정할 값은 `YOUTUBE_PROXY_MAX_BYTES`(hard limit)와
`YOUTUBE_PROXY_KAKAO_TARGET_BYTES`(profile target)다.

서버 활성화 전에는 `YOUTUBE_PROXY_YTDLP_BIN`, `YOUTUBE_PROXY_FFPROBE_BIN`,
`YOUTUBE_PROXY_FFMPEG_BIN`의 버전·SHA-256·라이선스
고지를 `THIRD_PARTY.md` 기준으로 검토하고 `scripts/doctor.sh`를 통과시켜야 한다.
