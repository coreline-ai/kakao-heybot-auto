# brushvid 파이프라인

이미지 1장을 **routes JSON**(붓 드로잉 스트로크)과 **스키마 검증된 render-props**로 변환하는
Python 패키지. Remotion 렌더(`src/`)와의 접점은 `schema/render-props.schema.json`과
routes JSON 포맷뿐이다.

## 부트스트랩

```bash
cd pipeline
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 테스트

```bash
.venv/bin/pytest tests/ -v
```

## 모듈

| 모듈 | 책임 |
| --- | --- |
| `brushvid/routes.py` | 이미지 → content mask → skeletonize → 폴리라인 추적 → RDP → seal 밴드 → 타이밍 → routes JSON (+커버리지) |
| `brushvid/layers.py` | pen-brush용 sharp outline/color/content 레이어 분리 + 선 두께 계측 |
| `brushvid/fill_routes.py` | 자동 매크로 존 → 넓은 왕복 채색 routes + 100% coverage |
| `brushvid/background.py` | 배경 생성 전략 3종(imagegen/preset/user-images) + `clean()` 종이색 키잉 |
| `brushvid/layout.py` | 빈 영역 탐지, 위젯 자동 배치, 겹침 검증(UI 겹침 hard-fail) |
| `brushvid/cues.py` | SRT 파싱→frame 환산, 긴 문장 분할, 씬 그룹핑, `title_color()` |
| `brushvid/props.py` | render-props 빌더 + `schema/render-props.schema.json` jsonschema 검증 |
| `brushvid/render.py` | `npx remotion render` 호출, 세그먼트 concat, ffmpeg 오디오 mux |
| `brushvid/qa.py` | 프레임 캡처 → `capture-manifest.json` → 콘택트시트 |
| `brushvid/project.py` | project.yaml 로드/검증 + 모드 판정 (narration/tts/whisper/ambient) |
| `brushvid/stt.py` | 더빙 오디오 → 로컬 whisper(small, ko) → SRT |
| `brushvid/audio.py` | 앰비언트 BGM 합성(numpy/wave) + 오디오-씬 길이 정합 |
| `brushvid/bgm.py` | 로컬 BGM catalog·import·SHA-256·라이선스/Content ID preflight |
| `brushvid/mix.py` | ffmpeg LUFS 정규화·gain/fade·playlist crossfade·내레이션 ducking |
| `brushvid/audit.py` | 완성 mp4 시각·LUFS/True Peak·라이선스 매니페스트 감사 |
| `brushvid/tts.py` | 공통 TTS 오케스트레이터: 대본/자막 → 44.1kHz mono 더빙 WAV + 실제 길이 기준 SRT |
| `brushvid/tts_engines/` | `supertonic`, `melo-ko`, `qwen3-base` adapter·registry·Qwen worker |
| `brushvid/voice_presets.py` | 여성 음성팩 `female-01`~`female-10` catalog 검증·별칭·style blend·preview hard gate |

## SRT-first 자동화 (bin/build.py)

`project.yaml` 하나로 완성 mp4 를 만든다. 스테이지 캐시(`data/{pid}/stages/`)와
`--from <stage>` 재개를 지원한다.

```bash
pipeline/.venv/bin/python bin/build.py examples/narration/project.yaml   # SRT 제공
pipeline/.venv/bin/python bin/build.py examples/whisper/project.yaml     # audio만 → whisper
pipeline/.venv/bin/python bin/build.py examples/ambient/project.yaml     # 입력 없음 → 앰비언트
pipeline/.venv/bin/python bin/build.py examples/ambient/project.yaml --from render  # 재개
pipeline/.venv/bin/python bin/build.py examples/ambient-bgm/project.yaml --from mix --audit
python3 bin/voice-assets.py validate                                     # 여성 음성팩 10종 + 청취 링크
pipeline/.venv/bin/python bin/qa.py examples/ambient/project.yaml        # QA 단독 재실행
pipeline/.venv/bin/python scripts/tts-doctor.py --check melo-ko          # Melo 오프라인 점검
pipeline/.venv/bin/python scripts/tts-doctor.py --check qwen3-base       # Qwen 환경·snapshot 점검
```

TTS 엔진 선택·설치·reference·manifest 규칙은 [공통 TTS 엔진 카탈로그](../skill/_shared/references/tts-engine-catalog.md)를 따른다. `--prepare`를 명시한 경우에만 패키지 설치와 모델 snapshot 준비가 실행되며, 일반 build는 local-only다.

공식 페이지에서 내려받은 외부 BGM은 `bin/bgm-assets.py import`로 등록하고
`bin/bgm-assets.py verify`를 통과한 뒤 사용한다. 원본과 증빙은 Git에서 제외된 `local-assets/`에만 둔다.
Pixabay의 원래 파일명으로 `~/Downloads`에 저장했다면 `bin/bgm-assets.py scan --attach`가
공식 URL slug와 파일명을 대조해 단일 일치 항목만 자동 등록한다. 모호하거나 일치하지 않는 파일은 건드리지 않는다.
`bin/bgm-assets.py dashboard`는 `local-assets/bgm/index.html`에 카탈로그 전체 진행도, 공식 청취 링크,
라이선스 증빙과 등록된 로컬 MP3 플레이어를 생성한다.
`bin/bgm-assets.py review`는 검증 영상 5종을 한 화면에서 재생하고 이어폰·노트북 스피커별
승인 결과를 브라우저에 보존한 뒤 JSON으로 내보내는 최종 사람 청취 게이트를 생성한다.
내보낸 파일을 `review --import-result <JSON>`으로 다시 넣으면 5개 항목이 두 환경에서 모두
`pass`인지 검증하고 SHA-256과 함께 `local-assets/bgm/listening-approval.json`에 기록한다.
마지막으로 `bin/bgm-assets.py gate`가 카탈로그 전체 준비 상태, 필수 E2E 4종의 video/audit/manifest,
사람 승인 파일을 모두 확인한다. 하나라도 빠지면 exit 1이며 `final-gate.json`에 원인을 남긴다.

## 사용 예 (routes 생성 → props → 렌더)

```python
from brushvid.routes import RouteParams, generate_routes, write_routes
from brushvid.props import build_scene, build_props, validate_props, write_props
from brushvid.render import render

data = generate_routes("public/my-proj/composed.png",
                       RouteParams(duration=300, draw_start=8, draw_end=220,
                                   pen_invisible_after=228, seed=1))
write_routes(data, "public/my-proj/routes.json")

props = build_props("my-proj", [build_scene("scene-01", "my-proj/routes.json", 300)])
validate_props(props)
write_props(props, "data/my-proj/props.json")

render("data/my-proj/props.json", "output/my-proj.mp4")
```

routes JSON 포맷(기존 호환):
`{meta: {image, width, height, fps, durationInFrames, drawStart, drawEnd, penInvisibleAfter, routeCount, ...}, strokes: [{id, kind, width, start, end, points: [[x, y], ...]}]}`
