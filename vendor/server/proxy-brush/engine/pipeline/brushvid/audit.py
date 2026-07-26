"""audit.py — 완성 mp4 하나만으로 결함을 자동 검출하는 독립 검수기.

독립성 1급: brushvid 타 모듈 import 금지 — ffmpeg/ffprobe(서브프로세스) + numpy/PIL 만 사용.
임계값은 FIELD-LOG 2026-07-11 실측(city 하드컷 12~23% / develop 스파이크 2.97% / 수정 후 2.7~5.3%) 기반.

2-패스 스캔:
  ① 저해상도(192px 그레이) rawvideo 파이프 → 전 프레임 diff/밝기 스트림
  ② 후보 지점(경계·스파이크)만 원본 해상도로 정밀 재측정

검출 6종: 경계 하드컷 / 씬 중간 스파이크 / 정지(freeze) / 오디오(무음·클리핑·전체무음)
          / 규격(해상도·fps·코덱·쇼츠 길이) / 레터박스 밴드
씬 경계: props 제공 시 정확 판정, 없으면 순백 근접 프레임으로 추정 (추정 실패 시에도
        하드컷은 스파이크 검출기가 잡는다 — props 없이도 FAIL 검출 가능해야 함).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── 임계값 (단위: 인접 프레임 mean abs diff %, 그레이 0~255 기준) ──
BOUNDARY_WARN = 6.0      # 씬 경계 diff (수정 후 실측 2.7~5.3 / 수정 전 12~23)
BOUNDARY_FAIL = 10.0
SPIKE_WARN = 1.5         # 씬 중간 스파이크 절대치 + 롤링 중앙값 배수
SPIKE_WARN_RATIO = 3.0
SPIKE_FAIL = 2.5         # develop 스파이크 실측 2.97 / 수정 후 최대 0.94
SPIKE_FAIL_RATIO = 4.0
CANDIDATE_MIN = 1.0      # 저해상도 1차 후보 임계 (원본 해상도 재측정 대상)
CANDIDATE_RATIO = 2.5
BOUNDARY_REMEASURE = 3.5  # 경계 저해상 diff 가 이 이상이면 원본 해상도 재측정
FREEZE_EPS = 0.05        # 정지 판정 diff
FREEZE_MIN_SEC = 3.0
FREEZE_TAIL_SEC = 2.0    # 씬 끝/영상 끝 이내면 감상 구간(info)
SILENCE_NOISE_DB = -50
SILENCE_MIN_SEC = 2.0
CLIP_DB = -0.5
TRUE_PEAK_WARN_DBTP = -1.0
LOUDNESS_LOW_LUFS = -24.0
LOUDNESS_HIGH_LUFS = -13.0
FULL_SILENCE_FRAC = 0.98  # 무음 합산이 전체의 이 비율 이상이면 전체 무음
SHORTS_MAX_SEC = 180.0
SCAN_WIDTH = 192         # 1-패스 분석 폭
SCAN_THREADS = 4         # 저해상도 전체 스캔의 decoder/filter worker 수
WHITE_LUM = 238.0        # 씬 경계 추정용 순백 근접 밝기 (0~255)
WHITE_MIN_RUN = 5        # 순백 유지 최소 프레임 (1~2프레임 화이트 플래시는 경계가 아님)
ROLL_WIN = 61            # 롤링 중앙값 창 (약 2초)
ROLL_FLOOR = 0.05        # 중앙값 하한 (정지 구간 나눗셈 폭주 방지)
WASH_CORR = 0.90         # 구도 유지(워시) 판정 Pearson 상관 임계
REVERT_FRAC = 0.5        # transient(번쩍 후 복귀) 판정: 복귀 잔차 < 피크 × 이 비율
OUTRO_WINDOW = 36        # 씬 끝 직전(경계 앞 프레임 수) — 워시 온셋 연출 허용 창
MAX_REMEASURE = 240      # 2-패스 재측정 상한
REMEASURE_WORKERS = 4    # 독립적인 ffmpeg seek 재측정의 상한 (본편 렌더와 동시 실행하지 않음)
EVIDENCE_MAX = 8         # 증거 스틸 저장 이슈 상한
CANON_SIZES = ((1920, 1080), (3840, 2160), (1080, 1920))
# city 무펄스 정상본의 콘텐츠 채움 변동 최대 1.30, 기존 체감 펄스는 luma +6.
# 정상 공간 채움은 허용하고 실제 역방향 혹만 검토/실패시킨다.
COMPLETION_REVERSAL_WARN = 2.0   # luma 0~255, 완료 구간 반대 방향 이동
COMPLETION_REVERSAL_FAIL = 4.0

SEV_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2}


@dataclass
class Issue:
    """검출 이슈 1건. severity: FAIL(배포 불가) / WARN(검토) / INFO(참고)."""

    severity: str
    kind: str
    message: str
    frame: int | None = None
    timeSec: float | None = None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"severity": self.severity, "kind": self.kind, "message": self.message,
                "frame": self.frame, "timeSec": self.timeSec, "metrics": self.metrics}


def fmt_ts(sec: float | None) -> str:
    if sec is None:
        return "-"
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m):02d}:{s:04.1f}"


# ── ffprobe / 규격 ──

def probe_summary(video: str | Path) -> dict:
    """ffprobe 요약: 해상도/fps/코덱/길이/프레임수/오디오 유무."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise ValueError(f"비디오 스트림 없음: {video}")
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    nb = int(v.get("nb_frames") or 0) or int(round(duration * fps))
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": fps,
            "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
            "hasAudio": a is not None, "duration": duration, "nbFrames": nb}


def check_spec(s: dict) -> list[Issue]:
    """규격 검사 (순수 함수): 캐논 해상도/30fps/h264/aac + 쇼츠 180초 한도."""
    issues: list[Issue] = []
    size = (s["width"], s["height"])
    if size not in CANON_SIZES:
        issues.append(Issue("WARN", "spec", f"비표준 해상도 {size[0]}×{size[1]} (표준: 1920×1080 | 3840×2160 | 1080×1920)"))
    if abs(s["fps"] - 30.0) > 0.05:
        issues.append(Issue("WARN", "spec", f"비표준 fps {s['fps']:.3f} (표준: 30)"))
    if s["vcodec"] != "h264":
        issues.append(Issue("WARN", "spec", f"비표준 비디오 코덱 {s['vcodec']} (표준: h264)"))
    if not s["hasAudio"]:
        issues.append(Issue("FAIL", "audio-missing", "오디오 스트림 없음 (전체 무음)"))
    elif s["acodec"] != "aac":
        issues.append(Issue("WARN", "spec", f"비표준 오디오 코덱 {s['acodec']} (표준: aac)"))
    if s["height"] > s["width"] and s["duration"] > SHORTS_MAX_SEC:
        issues.append(Issue("FAIL", "spec",
                            f"쇼츠(세로) 길이 {s['duration']:.1f}s > 한도 {SHORTS_MAX_SEC:.0f}s"))
    # UHD 장편 납품은 컨테이너 duration만 600초이고 AAC mux가 마지막 영상 패킷을
    # 잘라내는 경우를 별도로 막는다. nbFrames는 ffprobe 스트림 메타데이터의 실제
    # 비디오 패킷 수이며, 600초 × 30fps = 정확히 18,000이어야 한다.
    if size == (3840, 2160) and abs(float(s.get("duration", 0)) - 600.0) <= 0.05 \
            and s.get("nbFrames") is not None and int(s["nbFrames"]) != 18000:
        issues.append(Issue("FAIL", "spec",
                            f"UHD 장편 프레임 수 {s['nbFrames']} (필수: 18000)"))
    return issues


# ── 1-패스: 저해상도 스캔 ──

def _read_exact(stream, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def scan_lowres(video: str | Path, width: int, height: int) -> dict:
    """전 프레임 저해상 그레이 스캔 → diffs(%), lums(밝기), meanFrame(레터박스용)."""
    sw = SCAN_WIDTH
    sh = max(2, int(round(height * sw / width / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-threads", str(SCAN_THREADS),
           "-filter_threads", str(SCAN_THREADS), "-i", str(video),
           "-vf", f"scale={sw}:{sh},format=gray", "-f", "rawvideo", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    fsz = sw * sh
    diffs = [0.0]
    lums: list[float] = []
    acc = np.zeros(fsz, dtype=np.float64)
    acc_n = 0
    prev: np.ndarray | None = None
    i = 0
    while True:
        raw = _read_exact(proc.stdout, fsz)
        if len(raw) < fsz:
            break
        arr = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        lums.append(float(arr.mean()))
        if prev is not None:
            diffs.append(float(np.abs(arr - prev).mean()) / 255.0 * 100.0)
        if i % 15 == 0:  # 레터박스 검출용 시간 평균 (15프레임 샘플)
            acc += arr
            acc_n += 1
        prev = arr
        i += 1
    proc.stdout.close()
    proc.wait()
    if i == 0:
        raise RuntimeError(f"프레임 디코드 실패: {video}")
    return {"diffs": np.asarray(diffs), "lums": np.asarray(lums),
            "meanFrame": (acc / max(1, acc_n)).reshape(sh, sw), "size": (sw, sh)}


def rolling_median(x: np.ndarray, win: int = ROLL_WIN) -> np.ndarray:
    """중앙 정렬 롤링 중앙값 (경계는 가용 창으로 축소)."""
    n = len(x)
    half = win // 2
    out = np.empty(n)
    for i in range(n):
        out[i] = np.median(x[max(0, i - half):min(n, i + half + 1)])
    return out


# ── 씬 경계 ──

def boundaries_from_props(props_path: str | Path) -> tuple[list[int], int]:
    """props.json 씬 길이 누적합 → 경계 프레임 목록 (마지막 씬 끝 제외) + 총 프레임."""
    props = json.loads(Path(props_path).read_text(encoding="utf-8"))
    durations = [int(s["durationInFrames"]) for s in props["scenes"]]
    cum: list[int] = []
    t = 0
    for d in durations[:-1]:
        t += d
        cum.append(t)
    return cum, t + durations[-1]


def completion_windows_from_props(props_path: str | Path) -> list[dict]:
    """props/public routes에서 표준 완료 구간과 pen→brush 채색 구간을 계산한다.

    pen→brush의 넓은 왕복 채색은 한 프레임의 큰 면적 변화를 만들 수 있다. 이는
    씬 중간 워시 점프가 아니라 명시된 ``paint`` route의 정상 스트로크이므로,
    ``auditKind=pen-brush-paint`` 으로 별도 기록한다. 표준 integrated-develop만
    완료 펄스 분석 대상으로 유지한다.
    """
    path = Path(props_path).resolve()
    props = json.loads(path.read_text(encoding="utf-8"))
    root = path.parents[2] if len(path.parents) >= 3 else path.parent
    public = root / "public"
    windows: list[dict] = []

    def load_route(route_ref: object) -> dict | None:
        if not isinstance(route_ref, str) or not route_ref:
            return None
        route_path = Path(route_ref)
        if not route_path.is_absolute():
            route_path = public / route_path
        if not route_path.is_file():
            return None
        return json.loads(route_path.read_text(encoding="utf-8"))

    offset = 0
    for scene in props.get("scenes") or []:
        duration = int(scene.get("durationInFrames", 0))
        route_ref = scene.get("routes")
        if scene.get("completionMode") == "integrated-develop" and route_ref:
            routes = load_route(route_ref)
            if routes is not None:
                strokes = routes.get("strokes") or []
                meta = routes.get("meta") or {}
                last_end = max((float(s.get("end", 0)) for s in strokes),
                               default=float(meta.get("drawEnd", 0)))
                develop_end = last_end + int(scene.get("developFrames", 0))
                settle_end = develop_end + int(scene.get("colorSettleFrames", 0))
                windows.append({
                    "auditKind": "integrated-develop",
                    "sceneId": scene.get("id", "<scene>"),
                    "offset": offset,
                    "drawStart": offset + float(meta.get("drawStart", 0)),
                    "lastStrokeEnd": offset + last_end,
                    "developEnd": offset + develop_end,
                    "colorSettleEnd": offset + settle_end,
                    "outroStart": offset + duration - int(scene.get("outroFadeFrames", 0)),
                    "sceneEnd": offset + duration,
                })

        # pen→brush는 색이 보이는 paint phase만 기록한다. outline과 paint의 위상은
        # 겹치지 않는다는 렌더 계약이므로 이 구간 안의 correlated 변화는 정상 채색이다.
        for phase in scene.get("drawingPhases") or []:
            if not isinstance(phase, dict) or phase.get("kind") != "paint":
                continue
            routes = load_route(phase.get("routes"))
            if routes is None:
                continue
            strokes = routes.get("strokes") or []
            meta = routes.get("meta") or {}
            starts = [float(stroke.get("start", meta.get("drawStart", 0))) for stroke in strokes]
            draw_start = min(starts, default=float(meta.get("drawStart", 0)))
            last_end = max((float(stroke.get("end", 0)) for stroke in strokes),
                           default=float(meta.get("drawEnd", 0)))
            windows.append({
                "auditKind": "pen-brush-paint",
                "sceneId": scene.get("id", "<scene>"),
                "offset": offset,
                "drawStart": offset + draw_start,
                "lastStrokeEnd": offset + last_end,
                "developEnd": offset + last_end,
                "colorSettleEnd": offset + last_end,
                "outroStart": offset + duration - int(scene.get("outroFadeFrames", 0)),
                "sceneEnd": offset + duration,
            })
        offset += duration
    return windows


def completion_reversal_metrics(lums: np.ndarray) -> dict:
    """완료 luma가 최종 방향과 반대로 움직인 최대 폭을 계산한다."""
    values = np.asarray(lums, dtype=np.float64)
    if values.size < 3:
        return {"direction": "flat", "delta": 0.0, "reversal": 0.0}
    if values.size >= 5:
        values = np.convolve(values, np.ones(5) / 5.0, mode="valid")
    delta = float(values[-1] - values[0])
    if delta < -0.25:
        running = np.minimum.accumulate(values)
        reversal = float(np.max(values - running))
        direction = "darker"
    elif delta > 0.25:
        running = np.maximum.accumulate(values)
        reversal = float(np.max(running - values))
        direction = "lighter"
    else:
        reversal = float(values.max() - values.min())
        direction = "flat"
    return {"direction": direction, "delta": round(delta, 3),
            "reversal": round(reversal, 3)}


def completion_pulse_issues(lums: np.ndarray, windows: list[dict], fps: float) -> tuple[list[Issue], list[dict]]:
    issues: list[Issue] = []
    stats: list[dict] = []
    for window in windows:
        # paint route는 개별 스트로크 폭이 크므로 completion-pulse 대상이 아니다.
        if window.get("auditKind", "integrated-develop") != "integrated-develop":
            continue
        start = max(0, round(float(window["lastStrokeEnd"])))
        end = min(len(lums) - 1, round(float(window["colorSettleEnd"])))
        metrics = completion_reversal_metrics(lums[start:end + 1])
        stat = {**window, **metrics, "startFrame": start, "endFrame": end}
        stats.append(stat)
        reversal = float(metrics["reversal"])
        severity = "FAIL" if reversal > COMPLETION_REVERSAL_FAIL \
            else ("WARN" if reversal > COMPLETION_REVERSAL_WARN else None)
        if severity:
            issues.append(Issue(
                severity, "completion-pulse",
                f"{window['sceneId']} 완료 구간 밝기 역전 {reversal:.2f} luma "
                f"(방향 {metrics['direction']}, f{start}~f{end})",
                frame=start, timeSec=start / fps,
                metrics={"reversal": reversal, "delta": metrics["delta"],
                         "direction": metrics["direction"], "endFrame": end},
            ))
    return issues, stats


def phase_window_for_frame(frame: int, windows: list[dict]) -> tuple[str, dict] | None:
    """프레임이 속한 정확한 route 단계와 해당 윈도우를 반환한다."""
    for window in windows:
        if float(window["drawStart"]) <= frame <= float(window["lastStrokeEnd"]):
            return "drawing", window
        if float(window["lastStrokeEnd"]) < frame <= float(window["colorSettleEnd"]):
            return "completion", window
        if float(window["outroStart"]) <= frame < float(window["sceneEnd"]):
            return "outro", window
    return None


def phase_for_frame(frame: int, windows: list[dict]) -> str | None:
    active = phase_window_for_frame(frame, windows)
    return active[0] if active else None


def estimate_boundaries(lums: np.ndarray) -> list[int]:
    """순백 근접(lum ≥ WHITE_LUM) 유지 구간의 끝 다음 프레임을 씬 경계로 추정."""
    white = lums >= WHITE_LUM
    bounds: list[int] = []
    n = len(white)
    i = 0
    while i < n:
        if white[i]:
            j = i
            while j + 1 < n and white[j + 1]:
                j += 1
            if (j - i + 1) >= WHITE_MIN_RUN and j + 1 < n:
                bounds.append(j + 1)
            i = j + 1
        else:
            i += 1
    return bounds


# ── 검출기 (순수 함수 — 단위 테스트 대상) ──

def classify_boundary(diff: float) -> str | None:
    if diff > BOUNDARY_FAIL:
        return "FAIL"
    if diff > BOUNDARY_WARN:
        return "WARN"
    return None


def classify_spike(fullres: float, lowres: float, med: float) -> str | None:
    """스파이크 판정: 절대치는 원본 해상도, 배수는 저해상 diff/롤링중앙값 (동일 도메인)."""
    ratio = lowres / max(med, ROLL_FLOOR)
    if fullres > SPIKE_FAIL and ratio > SPIKE_FAIL_RATIO:
        return "FAIL"
    if fullres > SPIKE_WARN and ratio > SPIKE_WARN_RATIO:
        return "WARN"
    return None


def pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    """픽셀 Pearson 상관 — 같은 구도의 워시/명암 변화는 ≈1, 내용이 바뀌는 컷은 낮다.
    분산 0(민무늬 프레임)은 구조 비교 불능 → 0.0 (내용 변화로 간주)."""
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    sa, sb = a.std(), b.std()
    if sa < 1e-6 or sb < 1e-6:
        return 0.0
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def judge_candidate(peak: float, lowres: float, med: float, *, transient: bool,
                    corr: float, near_scene_end: bool) -> tuple[str, str | None]:
    """씬 중간 후보 최종 분류 (순수 함수). 반환: (kind, severity|None).

    - transient(번쩍 후 복귀)      → spike 임계 (develop 플래시류 결함)
    - 지속 + 구도 유지(corr≥0.9)   → 워시 온셋: 씬 끝 직전이면 연출(INFO), 아니면 WARN
    - 지속 + 구도 변화(corr<0.9)   → 씬 중간 하드컷: 경계 임계(6/10%) 적용
    """
    if transient:
        return "spike", classify_spike(peak, lowres, med)
    if corr >= WASH_CORR:
        if near_scene_end:
            return "outro-wash", "INFO" if peak > SPIKE_FAIL else None
        return "wash-jump", "WARN" if peak > SPIKE_WARN else None
    return "hardcut", classify_boundary(peak)


def spike_candidates(diffs: np.ndarray, roll: np.ndarray, exclude: set[int],
                     min_diff: float = CANDIDATE_MIN, min_ratio: float = CANDIDATE_RATIO) -> list[int]:
    """저해상 1차 후보 (경계 ±1 제외) → 근접(5프레임) 클러스터의 피크만."""
    cand = [i for i in range(1, len(diffs))
            if i not in exclude
            and diffs[i] >= min_diff
            and diffs[i] >= min_ratio * max(roll[i], ROLL_FLOOR)]
    peaks: list[int] = []
    for i in cand:
        if peaks and i - peaks[-1] <= 5:
            if diffs[i] > diffs[peaks[-1]]:
                peaks[-1] = i
        else:
            peaks.append(i)
    return peaks


def prioritize_spike_candidates(candidates: list[int], diffs: np.ndarray,
                                roll: np.ndarray) -> list[int]:
    """원본 해상도 재측정 순서를 위험도 기준으로 정한다.

    전체 후보가 ``MAX_REMEASURE``보다 많으면 시간순 선두만 재측정하는 방식은
    뒤쪽의 큰 변화에 ``corr=0/transient=True`` 기본값을 부여해 거짓 FAIL을 만들 수
    있다. 절대 변화량을 우선하고, 동률이면 정상 변화 대비 배수를 사용한다. 반환값은
    측정용 순서일 뿐, 최종 보고서는 원래 시간순 ``candidates``를 유지한다.
    """
    return sorted(
        candidates,
        key=lambda frame: (
            -float(diffs[frame]),
            -float(diffs[frame]) / max(float(roll[frame]), ROLL_FLOOR),
            int(frame),
        ),
    )


def find_freeze_runs(diffs: np.ndarray, fps: float,
                     eps: float = FREEZE_EPS, min_sec: float = FREEZE_MIN_SEC) -> list[tuple[int, int]]:
    """diff≈0 이 min_sec 이상 지속되는 [시작, 끝] 프레임 구간."""
    min_len = int(round(min_sec * fps))
    runs: list[tuple[int, int]] = []
    s = None
    for i in range(1, len(diffs) + 1):
        if i < len(diffs) and diffs[i] < eps:
            if s is None:
                s = i
        else:
            if s is not None and (i - 1) - s + 1 >= min_len:
                runs.append((s, i - 1))
            s = None
    return runs


def freeze_severity(run: tuple[int, int], boundaries: list[int], n_frames: int, fps: float) -> str:
    """씬 끝/영상 끝 감상 구간이면 info, 그 외 정지는 WARN."""
    tail = int(round(FREEZE_TAIL_SEC * fps))
    ends = list(boundaries) + [n_frames - 1]
    for e in ends:
        if 0 <= e - run[1] <= tail:
            return "INFO"
    return "WARN"


def detect_letterbox(mean_frame: np.ndarray, dark: float = 26.0) -> list[Issue]:
    """시간 평균 프레임의 상하/좌우 연속 어두운 밴드 → 레터박스/필러박스."""
    issues: list[Issue] = []
    rows = mean_frame.mean(axis=1)
    cols = mean_frame.mean(axis=0)
    for name, arr in (("레터박스(상하)", rows), ("필러박스(좌우)", cols)):
        top = 0
        while top < len(arr) and arr[top] < dark:
            top += 1
        bot = 0
        while bot < len(arr) - top and arr[len(arr) - 1 - bot] < dark:
            bot += 1
        frac = (top + bot) / len(arr)
        if top > 0 and bot > 0 and frac >= 0.01:
            sev = "WARN" if frac >= 0.04 else "INFO"
            issues.append(Issue(sev, "letterbox",
                                f"{name} 밴드 {frac * 100:.1f}% (상/좌 {top}, 하/우 {bot} 셀)",
                                metrics={"fraction": round(frac, 4)}))
    return issues


# ── 오디오 검사 ──

_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
_MEAN_VOL = re.compile(r"mean_volume:\s*(-?[0-9.]+) dB")
_MAX_VOL = re.compile(r"max_volume:\s*(-?[0-9.]+) dB")
_LOUDNORM_JSON = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def parse_audio_stderr(text: str, duration: float) -> dict:
    """silencedetect+volumedetect stderr 파싱 → 무음 구간/볼륨."""
    starts = [float(m) for m in _SIL_START.findall(text)]
    ends = [float(m) for m in _SIL_END.findall(text)]
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration  # EOF 까지 무음이면 end 미출력
        silences.append((s, max(s, e)))
    mean_m = _MEAN_VOL.search(text)
    max_m = _MAX_VOL.search(text)
    return {"silences": silences,
            "meanVolume": float(mean_m.group(1)) if mean_m else None,
            "maxVolume": float(max_m.group(1)) if max_m else None}


def audio_issues(parsed: dict, duration: float, *, allow_full_silence: bool = False) -> list[Issue]:
    """무음>2s WARN / 전체 무음 FAIL / 클리핑 > -0.5dB WARN (순수 함수).

    ``allow_full_silence``는 mix report가 BGM/내레이션 없음과 AAC 무음 트랙을
    명시한 전달 건에만 사용한다. 해당 경우에도 감사 로그에는 INFO로 남긴다.
    """
    issues: list[Issue] = []
    silences = parsed["silences"]
    total_sil = sum(e - s for s, e in silences)
    fully_silent = (duration > 0 and total_sil >= FULL_SILENCE_FRAC * duration) or \
                   (parsed["meanVolume"] is not None and parsed["meanVolume"] < -70.0)
    if fully_silent:
        severity = "INFO" if allow_full_silence else "FAIL"
        label = "명시적 BGM 없음 AAC 무음 트랙" if allow_full_silence else "전체 무음"
        issues.append(Issue(severity, "audio-silence",
                            f"{label} (무음 합산 {total_sil:.1f}s / {duration:.1f}s, "
                            f"mean {parsed['meanVolume']} dB)"))
        return issues
    for s, e in silences[:5]:
        issues.append(Issue("WARN", "audio-silence",
                            f"무음 {e - s:.1f}s ({fmt_ts(s)} ~ {fmt_ts(e)})",
                            timeSec=s, metrics={"start": round(s, 2), "end": round(e, 2)}))
    if len(silences) > 5:
        issues.append(Issue("WARN", "audio-silence", f"무음 구간 총 {len(silences)}건 (상위 5건만 표기)"))
    if parsed["maxVolume"] is not None and parsed["maxVolume"] > CLIP_DB:
        issues.append(Issue("WARN", "audio-clipping",
                            f"클리핑 의심: max_volume {parsed['maxVolume']:.1f} dB > {CLIP_DB} dB"))
    return issues


def parse_loudnorm_stderr(text: str) -> dict:
    """loudnorm JSON의 Integrated LUFS/True Peak/LRA를 파싱한다."""
    matches = _LOUDNORM_JSON.findall(text)
    if not matches:
        return {"integratedLufs": None, "truePeakDbtp": None, "lra": None}
    raw = json.loads(matches[-1])

    def finite(key: str) -> float | None:
        try:
            value = float(raw.get(key))
            return value if np.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    return {"integratedLufs": finite("input_i"), "truePeakDbtp": finite("input_tp"),
            "lra": finite("input_lra")}


def loudness_issues(metrics: dict) -> list[Issue]:
    issues: list[Issue] = []
    integrated = metrics.get("integratedLufs")
    peak = metrics.get("truePeakDbtp")
    if integrated is not None and integrated < LOUDNESS_LOW_LUFS:
        issues.append(Issue("WARN", "audio-loudness",
                            f"평균 음량이 작음: {integrated:.1f} LUFS < {LOUDNESS_LOW_LUFS:.0f} LUFS",
                            metrics={"integratedLufs": integrated}))
    elif integrated is not None and integrated > LOUDNESS_HIGH_LUFS:
        issues.append(Issue("WARN", "audio-loudness",
                            f"평균 음량이 큼: {integrated:.1f} LUFS > {LOUDNESS_HIGH_LUFS:.0f} LUFS",
                            metrics={"integratedLufs": integrated}))
    if peak is not None and peak > TRUE_PEAK_WARN_DBTP:
        issues.append(Issue("WARN", "audio-true-peak",
                            f"True Peak 여유 부족: {peak:.1f} dBTP > {TRUE_PEAK_WARN_DBTP:.1f} dBTP",
                            metrics={"truePeakDbtp": peak}))
    return issues


def analyze_audio(video: str | Path, duration: float, *, allow_full_silence: bool = False) -> tuple[list[Issue], dict]:
    res = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(video),
         "-vn",
         "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SEC},volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True)
    volume = parse_audio_stderr(res.stderr, duration)
    loud = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
         "-vn",
         "-af", "loudnorm=I=-23:LRA=11:TP=-1.0:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    loudness = parse_loudnorm_stderr(loud.stderr)
    metrics = {**volume, **loudness}
    return [*audio_issues(volume, duration, allow_full_silence=allow_full_silence),
            *loudness_issues(loudness)], metrics


def run_audio_checks(video: str | Path, duration: float) -> list[Issue]:
    """하위 호환 wrapper."""
    return analyze_audio(video, duration)[0]


def check_license_manifest(path: str | Path | None, *,
                           workspace_root: str | Path = REPO_ROOT) -> tuple[list[Issue], dict | None]:
    """BGM 라이선스 매니페스트 기본 계약. auditor 독립성을 위해 bgm 모듈을 import하지 않는다."""
    if path is None:
        return [], None
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return [Issue("FAIL", "bgm-license", f"BGM 라이선스 매니페스트 없음: {manifest_path}")], None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue("FAIL", "bgm-license", f"BGM 라이선스 매니페스트 파싱 실패: {exc}")], None
    issues: list[Issue] = []
    if data.get("kind") == "generated-piano-bgm":
        status = data.get("status")
        if status not in {"PENDING_USER_LISTENING", "APPROVED"}:
            issues.append(Issue("FAIL", "bgm-generated-status", f"generated BGM 승인 상태 오류: {status}"))
        elif status == "PENDING_USER_LISTENING":
            issues.append(Issue("WARN", "bgm-generated-pending", "Stable Audio 생성 BGM은 사람 청취 승인 전 상태입니다"))
        if data.get("engine") != "stable-audio-3-mlx":
            issues.append(Issue("FAIL", "bgm-generated-engine", "generated BGM engine metadata 누락 또는 오류"))
        if not data.get("provenance") or not data.get("qa"):
            issues.append(Issue("FAIL", "bgm-generated-provenance", "generated BGM provenance/qa 경로 누락"))
        license_info = data.get("license") or {}
        if not license_info.get("modelUrl") or not license_info.get("termsUrl"):
            issues.append(Issue("FAIL", "bgm-generated-license", "Stable Audio model/terms URL 누락"))
        digest = data.get("deliverySha256") or data.get("sha256") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append(Issue("FAIL", "bgm-generated-sha256", "generated BGM SHA-256 형식 오류"))
        local_path = data.get("localPath")
        if not local_path:
            issues.append(Issue("FAIL", "bgm-generated-path", "generated BGM localPath 누락"))
        else:
            audio_path = Path(local_path)
            if not audio_path.is_absolute():
                audio_path = Path(workspace_root) / audio_path
            if not audio_path.is_file():
                issues.append(Issue("FAIL", "bgm-generated-path", f"generated BGM 로컬 파일 없음: {audio_path}"))
            elif re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                hasher = hashlib.sha256()
                with audio_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest().lower() != digest.lower():
                    issues.append(Issue("FAIL", "bgm-generated-sha256", "generated BGM SHA-256 불일치"))
        return issues, {
            "path": str(manifest_path), "kind": "generated-piano-bgm", "status": status,
            "engine": data.get("engine"), "assetId": data.get("assetId"),
        }
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        issues.append(Issue("FAIL", "bgm-license", "BGM 라이선스 매니페스트 assets가 비어 있음"))
        return issues, data
    today = date.today()
    distribution = data.get("distribution")
    for asset in assets:
        asset_id = asset.get("id") or "<unknown>"
        youtube_blocked = (
            asset.get("source") == "pixabay" or asset.get("youtubeAllowed") is False
        )
        if distribution in ("youtube", "shorts") and youtube_blocked:
            issues.append(Issue(
                "FAIL", "bgm-source-policy",
                f"{asset_id}: Pixabay 음원은 YouTube/Shorts 제작·교체·배포에 사용 금지",
            ))
        for key in ("sourcePage", "sha256", "artist"):
            if not asset.get(key):
                issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: {key} 누락"))
        lic = asset.get("license") or {}
        if not lic.get("url") or not lic.get("downloadedAt") or not lic.get("checkedAt"):
            issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: 라이선스 URL/날짜 증빙 누락"))
        if not lic.get("evidenceFiles"):
            issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: 라이선스 증빙 파일 목록 누락"))
        digest = asset.get("sha256") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: SHA-256 형식 오류"))
        resolved = asset.get("resolvedPath")
        if not resolved:
            issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: resolvedPath 누락"))
        else:
            audio_path = Path(resolved)
            if not audio_path.is_file():
                issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: 로컬 음원 없음: {audio_path}"))
            elif re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                hasher = hashlib.sha256()
                with audio_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest().lower() != digest.lower():
                    issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: 로컬 음원 SHA-256 불일치"))
            for evidence in lic.get("evidenceFiles") or []:
                evidence_path = (audio_path.parent / evidence).resolve()
                try:
                    evidence_path.relative_to(audio_path.parent.resolve())
                except ValueError:
                    issues.append(Issue("FAIL", "bgm-license",
                                        f"{asset_id}: 증빙 경로가 에셋 폴더 밖을 가리킴: {evidence}"))
                    continue
                if not evidence_path.is_file():
                    issues.append(Issue("FAIL", "bgm-license",
                                        f"{asset_id}: 라이선스 증빙 파일 없음: {evidence}"))
        status = lic.get("contentIdStatus", "unknown")
        if status in ("unknown", "registered"):
            issues.append(Issue("FAIL", "bgm-content-id", f"{asset_id}: Content ID 상태 {status}"))
        elif status == "not-displayed":
            issues.append(Issue("WARN", "bgm-content-id",
                                f"{asset_id}: 페이지 표시 없음은 Content ID 미등록을 보장하지 않음"))
        try:
            age = (today - date.fromisoformat(lic.get("checkedAt", ""))).days
            if age > 90:
                issues.append(Issue("WARN", "bgm-license",
                                    f"{asset_id}: 라이선스/Content ID 확인 후 {age}일 경과"))
        except ValueError:
            issues.append(Issue("FAIL", "bgm-license", f"{asset_id}: checkedAt 날짜 형식 오류"))
    summary = {"path": str(manifest_path), "assetIds": [a.get("id") for a in assets],
               "licensePolicy": data.get("licensePolicy"), "distribution": distribution}
    return issues, summary


def check_voice_manifest(path: str | Path | None, *,
                         engine_root: str | Path = REPO_ROOT) -> tuple[list[Issue], dict | None]:
    """TTS voice manifest의 재현성·AI 고지 계약. 미제공이면 독립 mp4 검사를 유지한다."""
    if path is None:
        return [], None
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return [Issue("FAIL", "tts-voice-manifest", f"TTS voice manifest 없음: {manifest_path}")], None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue("FAIL", "tts-voice-manifest", f"TTS voice manifest 파싱 실패: {exc}")], None

    if data.get("schemaVersion") == 2:
        return _check_engine_voice_manifest_v2(manifest_path, data, engine_root=engine_root)

    issues: list[Issue] = []
    required = (
        "schemaVersion", "projectId", "requestedVoice", "voicePresetId", "voicePackVersion",
        "engine", "packageVersion", "model", "language", "sampleRate", "speed",
        "components", "catalogSha256", "styleSourceSha256", "styleSha256",
        "aiDisclosure", "license", "pauseMs", "durationSec", "sentenceCount",
    )
    for key in required:
        if data.get(key) is None or data.get(key) == "":
            issues.append(Issue("FAIL", "tts-voice-manifest", f"voice manifest {key} 누락"))

    for key in ("catalogSha256", "styleSha256"):
        value = data.get(key)
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            issues.append(Issue("FAIL", "tts-voice-manifest", f"{key} SHA-256 형식 오류"))
    sources = data.get("styleSourceSha256")
    if not isinstance(sources, dict) or not sources:
        issues.append(Issue("FAIL", "tts-voice-manifest", "styleSourceSha256가 비어 있음"))
    else:
        for name, digest in sources.items():
            if not re.fullmatch(r"[0-9a-f]{64}", str(digest or "")):
                issues.append(Issue("FAIL", "tts-voice-manifest", f"{name} style SHA-256 형식 오류"))

    components = data.get("components")
    if not isinstance(components, dict) or not components:
        issues.append(Issue("FAIL", "tts-voice-manifest", "components가 비어 있음"))
    else:
        try:
            weights = [float(value) for value in components.values()]
            if any(value <= 0 for value in weights) or abs(sum(weights) - 1.0) > 1e-8:
                issues.append(Issue("FAIL", "tts-voice-manifest", "component 비율 합이 1.0이 아님"))
        except (TypeError, ValueError):
            issues.append(Issue("FAIL", "tts-voice-manifest", "component 비율이 숫자가 아님"))
        preset_id = str(data.get("voicePresetId") or "")
        if preset_id.startswith("female-") and any(not re.fullmatch(r"F[1-5]", name) for name in components):
            issues.append(Issue("FAIL", "tts-voice-manifest", "여성 preset에 F1~F5 외 component 포함"))

    try:
        speed = float(data.get("speed"))
        if not 0.70 <= speed <= 2.00:
            issues.append(Issue("FAIL", "tts-voice-manifest", f"speed 범위 밖: {speed}"))
    except (TypeError, ValueError):
        if data.get("speed") is not None:
            issues.append(Issue("FAIL", "tts-voice-manifest", "speed가 숫자가 아님"))

    license_info = data.get("license") or {}
    if not license_info.get("url") or not license_info.get("aiDisclosureRequired"):
        issues.append(Issue("FAIL", "tts-voice-manifest", "모델 라이선스 URL/AI 고지 의무 누락"))
    if not str(data.get("aiDisclosure") or "").strip():
        issues.append(Issue("FAIL", "tts-ai-disclosure", "AI 합성 음성 고지 문구 누락"))

    expected = {"voicePackVersion": "1.0.0", "packageVersion": "1.3.1", "model": "supertonic-3"}
    for key, value in expected.items():
        actual = data.get(key)
        if actual is not None and actual != value:
            issues.append(Issue("WARN", "tts-version-drift", f"{key} drift: expected={value}, actual={actual}"))

    summary = {
        "path": str(manifest_path),
        "voicePresetId": data.get("voicePresetId"),
        "requestedVoice": data.get("requestedVoice"),
        "voicePackVersion": data.get("voicePackVersion"),
        "packageVersion": data.get("packageVersion"),
        "model": data.get("model"),
        "components": data.get("components"),
        "speed": data.get("speed"),
        "aiDisclosure": data.get("aiDisclosure"),
    }
    return issues, summary


def _check_engine_voice_manifest_v2(manifest_path: Path, data: dict, *,
                                    engine_root: str | Path = REPO_ROOT) -> tuple[list[Issue], dict | None]:
    """신규 engine manifest는 저장소 단일 JSON Schema와 엔진별 의미 규칙으로 감사한다."""
    schema_path = Path(engine_root) / "schema" / "tts-voice-manifest.schema.json"
    issues: list[Issue] = []
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data),
            key=lambda error: list(error.absolute_path),
        )
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue("FAIL", "tts-voice-manifest", f"v2 schema 읽기 실패: {exc}")], None
    for error in errors[:8]:
        where = ".".join(str(part) for part in error.absolute_path) or "<root>"
        issues.append(Issue("FAIL", "tts-voice-manifest", f"v2 schema {where}: {error.message}"))
    license_info = data.get("license") or {}
    if not license_info.get("url") or license_info.get("aiDisclosureRequired") is not True:
        issues.append(Issue("FAIL", "tts-voice-manifest", "v2 모델 라이선스 URL/AI 고지 의무 누락"))
    if not str(data.get("aiDisclosure") or "").strip():
        issues.append(Issue("FAIL", "tts-ai-disclosure", "v2 AI 합성 음성 고지 문구 누락"))
    if data.get("engine") == "qwen3-base" and data.get("xVectorOnlyMode") is not False:
        issues.append(Issue("FAIL", "tts-voice-manifest", "Qwen x-vector-only 모드는 허용하지 않음"))
    summary = {
        "path": str(manifest_path), "engine": data.get("engine"), "voice": data.get("voice"),
        "requestedVoice": data.get("voice"), "voicePresetId": data.get("voice"),
        "voicePackVersion": None, "components": None,
        "model": data.get("model"), "modelRevision": data.get("modelRevision"),
        "packageVersion": data.get("packageVersion"), "durationSec": data.get("durationSec"),
        "sentenceCount": data.get("sentenceCount"), "speed": data.get("appliedSpeed"),
        "requestedSpeed": data.get("requestedSpeed"), "appliedSpeed": data.get("appliedSpeed"),
        "speedAppliedBy": data.get("speedAppliedBy"),
        "nativeSampleRate": data.get("nativeSampleRate"), "outputSampleRate": data.get("outputSampleRate"),
        "referenceVoiceId": data.get("referenceVoiceId"),
        "referenceAudioSha256": data.get("referenceAudioSha256"),
        "referenceTranscriptSha256": data.get("referenceTranscriptSha256"),
        "aiDisclosure": data.get("aiDisclosure"),
    }
    return issues, summary


def check_mix_report(path: str | Path | None, video_duration: float) -> tuple[list[Issue], dict | None]:
    """실제 mix 스테이지가 기록한 정규화·playlist·ducking 결과를 교차 검증한다."""
    if path is None:
        return [], None
    report_path = Path(path)
    if not report_path.is_file():
        return [Issue("FAIL", "audio-mix-report", f"mix report 없음: {report_path}")], None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue("FAIL", "audio-mix-report", f"mix report 파싱 실패: {exc}")], None
    issues: list[Issue] = []
    duration = float(data.get("durationSec") or 0)
    if abs(duration - video_duration) > 0.15:
        issues.append(Issue("FAIL", "audio-duration",
                            f"mix/video 길이 불일치: {duration:.3f}s vs {video_duration:.3f}s"))
    bgm = data.get("bgm") or {}
    tracks = bgm.get("tracks") or []
    if bgm.get("kind") == "playlist":
        if len(tracks) < 2 or float(bgm.get("crossfadeSec") or 0) <= 0:
            issues.append(Issue("FAIL", "audio-crossfade", "playlist track/crossfade 기록 누락"))
    bgm_out = bgm.get("output") or {}
    if bgm_out.get("truePeakDbtp") is not None and bgm_out["truePeakDbtp"] > TRUE_PEAK_WARN_DBTP:
        issues.append(Issue("WARN", "audio-true-peak",
                            f"BGM master True Peak {bgm_out['truePeakDbtp']:.1f} dBTP"))
    voice = data.get("voice")
    # 음성만 있는 bgm=off 결과에는 ducking 자체가 필요 없다.
    if voice and tracks:
        duck = voice.get("ducking") or {}
        if duck.get("enabled") and float(duck.get("ratio") or 0) <= 1:
            issues.append(Issue("FAIL", "audio-ducking", "ducking enabled지만 compressor ratio가 1 이하"))
        if duck.get("enabled") and duck.get("measuredAttenuationDb") is None:
            issues.append(Issue("FAIL", "audio-ducking", "ducking 실제 감쇄량 측정값 누락"))
        elif duck.get("enabled"):
            attenuation = float(duck["measuredAttenuationDb"])
            regions = duck.get("regionMetrics")
            if regions:
                active_attenuation = float(regions.get("activeAttenuationDb") or 0)
                inactive_attenuation = float(regions.get("inactiveAttenuationDb") or 0)
                requested = float(duck.get("requestedAmountDb") or 0)
                if active_attenuation < 1.0:
                    issues.append(Issue("WARN", "audio-ducking",
                                        f"음성 활성 구간 BGM 감쇄가 낮음: {active_attenuation:.1f}dB"))
                if inactive_attenuation > active_attenuation - 1.0:
                    issues.append(Issue("WARN", "audio-ducking",
                                        "음성 비활성 구간에서 BGM 복귀가 확인되지 않음"))
                if requested > 0 and active_attenuation > requested + 1.0:
                    issues.append(Issue("WARN", "audio-ducking",
                                        f"음성 활성 구간 감쇄가 요청값을 초과함: "
                                        f"{active_attenuation:.1f}dB > {requested:.1f}dB"))
            elif attenuation < 1.0:
                # 음성/무음 구간을 분리 측정하지 못한 구형 report에서만 전체 평균을 대체 지표로 쓴다.
                # 긴 감상 구간이 있는 영상은 전체 평균이 낮아도 활성 구간 덕킹은 정상일 수 있다.
                issues.append(Issue("WARN", "audio-ducking",
                                    f"ducking 실제 평균 감쇄량이 낮음: {attenuation:.1f}dB"))
            elif attenuation > 24.0:
                issues.append(Issue("WARN", "audio-ducking",
                                    f"ducking 실제 평균 감쇄량이 과도함: {attenuation:.1f}dB"))
        if not duck.get("enabled"):
            issues.append(Issue("WARN", "audio-ducking", "내레이션+BGM인데 ducking 비활성"))
    summary = {"path": str(report_path), "mode": data.get("mode"),
               "durationSec": duration, "trackCount": len(tracks),
               "crossfadeSec": bgm.get("crossfadeSec"),
               "ducking": (voice or {}).get("ducking") if voice and tracks else None}
    return issues, summary


def audio_envelope(video: str | Path, rate: int = 100) -> np.ndarray:
    """긴 영상도 가볍게 검사하도록 mono 100Hz float envelope 입력을 만든다."""
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn",
         "-af", f"aeval=abs(val(0)),aresample={rate}", "-ac", "1", "-ar", str(rate),
         "-f", "f32le", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(res.stdout, dtype="<f4")


def _rms_db(samples: np.ndarray, rate: int, start: float, end: float) -> float:
    lo = max(0, min(len(samples), int(start * rate)))
    hi = max(lo + 1, min(len(samples), int(end * rate)))
    if lo >= len(samples):
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples[lo:hi].astype(np.float64)))))
    return 20.0 * np.log10(max(rms, 1e-6))


def audio_shape_issues(video: str | Path, mix_report: str | Path | None) -> tuple[list[Issue], dict | None]:
    """앰비언트 fade와 playlist 전환의 무음 틈을 실제 master 파형에서 확인한다."""
    if mix_report is None or not Path(mix_report).is_file():
        return [], None
    data = json.loads(Path(mix_report).read_text(encoding="utf-8"))
    if data.get("mode") == "off" and (data.get("settings") or {}).get("silentAudioTrack"):
        return [], {"silentAudioTrack": True}
    # 음성이 있으면 master envelope로 BGM fade만 분리할 수 없으므로 playlist gap만 검사한다.
    has_voice = bool(data.get("voice"))
    duration = float(data.get("durationSec") or 0)
    settings = data.get("settings") or {}
    bgm = data.get("bgm") or {}
    rate = 100
    samples = audio_envelope(video, rate=rate)
    issues: list[Issue] = []
    metrics: dict = {"rate": rate, "fade": None, "transitions": []}

    if not has_voice and duration > 1:
        fi = min(float(settings.get("fadeInSec") or 0), duration * 0.45)
        fo = min(float(settings.get("fadeOutSec") or 0), duration * 0.45)
        start_db = _rms_db(samples, rate, 0, min(0.2, max(0.05, fi / 3)))
        body_start = min(duration - 0.1, max(0.25, fi + 0.1))
        body_db = _rms_db(samples, rate, body_start, min(duration, body_start + 0.5))
        tail_db = _rms_db(samples, rate, max(0, duration - min(0.2, max(0.05, fo / 3))), duration)
        before_tail = max(0, duration - fo - 0.6)
        before_tail_db = _rms_db(samples, rate, before_tail, min(duration, before_tail + 0.5))
        metrics["fade"] = {"startDb": round(start_db, 2), "bodyDb": round(body_db, 2),
                           "beforeTailDb": round(before_tail_db, 2), "tailDb": round(tail_db, 2)}
        if fi > 0.2 and start_db > body_db - 3.0:
            issues.append(Issue("WARN", "audio-fade", "BGM fade-in 감쇄가 3dB 미만",
                                metrics=metrics["fade"]))
        if fo > 0.2 and tail_db > before_tail_db - 3.0:
            issues.append(Issue("WARN", "audio-fade", "BGM fade-out 감쇄가 3dB 미만",
                                metrics=metrics["fade"]))

    tracks = bgm.get("tracks") or []
    crossfade = float(bgm.get("crossfadeSec") or 0)
    if len(tracks) > 1 and crossfade > 0:
        transition_times = bgm.get("transitionTimesSec")
        if not transition_times:
            seg = float(tracks[0].get("segmentSec") or 0)
            transition_times = [i * seg - (i - 0.5) * crossfade
                                for i in range(1, len(tracks))]
        for center_value in transition_times:
            center = float(center_value)
            if center >= duration:
                continue
            db = _rms_db(samples, rate, max(0, center - 0.2), min(duration, center + 0.2))
            item = {"timeSec": round(center, 3), "rmsDb": round(db, 2)}
            metrics["transitions"].append(item)
            if db < -50.0:
                issues.append(Issue("FAIL", "audio-crossfade", f"playlist 전환 무음 틈 {db:.1f}dB",
                                    timeSec=center, metrics=item))
    return issues, metrics


# ── 2-패스: 원본 해상도 재측정 / 증거 스틸 ──
# ffmpeg 입력 시킹의 시작 프레임은 컨테이너에 따라 ±1~2 프레임 흔들린다(실측) —
# 후보 중심 ±radius 윈도우를 한 번에 뽑아 "윈도우 내 최대 연속 diff"로 정렬 불요 측정.

PAIR_RADIUS = 4


def _grab_window(video: str | Path, center: int, fps: float, width: int, height: int,
                 radius: int = PAIR_RADIUS, rgb: bool = False) -> list[np.ndarray]:
    """center ±radius 프레임을 원본 해상도로 추출 (그레이 또는 RGB)."""
    n0 = max(0, center - radius)
    t = max(0.0, (n0 - 0.5) / fps)
    count = 2 * radius + 1
    fmt, ch = ("rgb24", 3) if rgb else ("gray", 1)
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.6f}", "-i", str(video),
         "-frames:v", str(count), "-vf", f"format={fmt}", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    fsz = width * height * ch
    data = res.stdout
    frames = []
    for i in range(len(data) // fsz):
        arr = np.frombuffer(data[i * fsz:(i + 1) * fsz], dtype=np.uint8)
        frames.append(arr.reshape(height, width, ch) if rgb else arr)
    return frames


def analyze_window(video: str | Path, frame: int, fps: float,
                   width: int, height: int) -> dict | None:
    """후보 주변 윈도우 원본 해상도 정밀 분석.

    반환: {peak(최대 연속 diff %), corr(피크 쌍 Pearson), transient(번쩍 후 복귀 여부)}
    """
    if frame < 1:
        return None
    frames = [f.astype(np.int16) for f in _grab_window(video, frame, fps, width, height)]
    if len(frames) < 2:
        return None
    diffs = [float(np.abs(frames[i] - frames[i - 1]).mean()) / 255.0 * 100.0
             for i in range(1, len(frames))]
    k = int(np.argmax(diffs))  # 피크 쌍 = frames[k] → frames[k+1]
    peak = diffs[k]
    corr = pearson_corr(frames[k], frames[k + 1])
    pre = frames[max(0, k - 2)]
    post = frames[min(len(frames) - 1, k + 4)]
    d_rev = float(np.abs(post - pre).mean()) / 255.0 * 100.0  # 복귀 잔차
    transient = d_rev < REVERT_FRAC * peak
    return {"peak": peak, "corr": corr, "transient": transient}


def fullres_pair_diff(video: str | Path, frame: int, fps: float,
                      width: int, height: int) -> float | None:
    """후보 프레임 주변 윈도우의 최대 연속 diff % (경계 재측정용 축약)."""
    res = analyze_window(video, frame, fps, width, height)
    return res["peak"] if res else None


def remeasure_boundaries(video: str | Path, boundaries: list[int], diffs: np.ndarray,
                         *, fps: float, width: int, height: int,
                         workers: int = REMEASURE_WORKERS) -> dict[int, float | None]:
    """고위험 씬 경계만 원본 해상도로 재측정한다.

    각 경계의 ffmpeg seek는 독립적이다. ``executor.map``으로 결과를 원래 경계 순서에
    다시 연결해 report 순서와 판정 수치를 그대로 보존한다. 59개 경계는 MAX_REMEASURE
    상한보다 작으므로 기존 순차 경로와 같은 후보 집합을 검사한다.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    jobs = [int(frame) for frame in boundaries if float(diffs[frame]) > BOUNDARY_REMEASURE]
    if not jobs:
        return {}
    jobs = jobs[:MAX_REMEASURE]

    def measure(frame: int) -> float | None:
        return fullres_pair_diff(video, frame, fps, width, height)

    worker_count = min(workers, len(jobs))
    if worker_count == 1:
        values = [measure(frame) for frame in jobs]
    else:
        with ThreadPoolExecutor(max_workers=worker_count,
                                thread_name_prefix="audit-boundary") as executor:
            values = list(executor.map(measure, jobs))
    return dict(zip(jobs, values))


def remeasure_spike_candidates(video: str | Path, candidates: list[int], *, limit: int,
                               fps: float, width: int, height: int,
                               workers: int = REMEASURE_WORKERS) -> tuple[dict[int, dict | None], int]:
    """스파이크 후보의 원본 해상도 재측정을 제한 병렬화한다.

    순차 경로는 ``analyze_window()``이 결과를 못 낸 후보를 재측정 상한에 포함하지
    않는다. 같은 동작을 유지하려고 남은 성공 한도만큼 작은 배치로 실행하고, None인
    후보가 있으면 다음 후보를 이어서 처리한다. 반환 dict는 후보 순서와 무관하게
    프레임 키로 조회하지만 호출·판정 대상은 순차 경로와 동일하다.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if limit <= 0 or not candidates:
        return {}, 0

    results: dict[int, dict | None] = {}
    successful = 0
    offset = 0

    def measure(frame: int) -> dict | None:
        return analyze_window(video, frame, fps, width, height)

    while offset < len(candidates) and successful < limit:
        batch_size = min(workers, limit - successful, len(candidates) - offset)
        batch = candidates[offset:offset + batch_size]
        offset += len(batch)
        if len(batch) == 1:
            values = [measure(batch[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(batch),
                                    thread_name_prefix="audit-spike") as executor:
                values = list(executor.map(measure, batch))
        for frame, value in zip(batch, values):
            results[frame] = value
            if value is not None:
                successful += 1
    return results, successful


def save_evidence_pair(video: str | Path, frame: int, fps: float, width: int, height: int,
                       out_prev: str | Path, out_at: str | Path) -> bool:
    """문제 지점 전후 프레임 PNG — 윈도우에서 diff 최대 쌍을 골라 저장 (정렬 불요)."""
    from PIL import Image
    frames = _grab_window(video, frame, fps, width, height, rgb=True)
    if len(frames) < 2:
        return False
    grays = [f.astype(np.int16).mean(axis=2) for f in frames]
    diffs = [float(np.abs(grays[i] - grays[i - 1]).mean()) for i in range(1, len(grays))]
    k = int(np.argmax(diffs)) + 1
    Path(out_prev).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(frames[k - 1], "RGB").save(out_prev)
    Image.fromarray(frames[k], "RGB").save(out_at)
    return True


# ── 오케스트레이션 ──

def run_audit(video: str | Path, props: str | Path | None = None,
              out_dir: str | Path | None = None, evidence: bool = True,
              license_manifest: str | Path | None = None,
              mix_report: str | Path | None = None,
              voice_manifest: str | Path | None = None,
              engine_root: str | Path = REPO_ROOT,
              workspace_root: str | Path = REPO_ROOT,
              sanitize_paths: bool = False) -> dict:
    """전체 검수 실행 → 결과 dict (issues/verdict/stats). out_dir 주면 리포트 파일 산출."""
    video = Path(video)
    t0 = time.perf_counter()
    spec = probe_summary(video)
    fps = spec["fps"] or 30.0
    issues: list[Issue] = check_spec(spec)

    # 1-패스
    scan = scan_lowres(video, spec["width"], spec["height"])
    diffs, lums = scan["diffs"], scan["lums"]
    n_frames = len(lums)
    t1 = time.perf_counter()

    # 씬 경계
    boundary_source = "none"
    boundaries: list[int] = []
    completion_windows: list[dict] = []
    if props is not None:
        boundaries, props_total = boundaries_from_props(props)
        boundary_source = "props"
        try:
            completion_windows = completion_windows_from_props(props)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(Issue("INFO", "completion-pulse",
                                f"완료 구간 정밀 분석 생략: {exc}"))
        if props_total != n_frames:
            issues.append(Issue("INFO", "spec",
                                f"props 총 프레임({props_total}) ≠ 영상 프레임({n_frames}) — 경계는 props 기준"))
        boundaries = [b for b in boundaries if 0 < b < n_frames]
    else:
        boundaries = estimate_boundaries(lums)
        boundary_source = "estimated" if boundaries else "none"

    roll = rolling_median(diffs)
    # 경계 재측정은 서로 독립적인 원본 해상도 seek이므로 제한적으로 병렬화한다.
    # props 기반 59개 경계는 240회 상한보다 작아 순차 판정과 같은 대상을 검사한다.
    boundary_fullres = remeasure_boundaries(
        video, boundaries, diffs, fps=fps, width=spec["width"], height=spec["height"])
    remeasured = len(boundary_fullres)

    # 경계 하드컷
    boundary_stats: list[dict] = []
    for b in boundaries:
        low = float(diffs[b])
        full = boundary_fullres.get(b)
        d = full if full is not None else low
        boundary_stats.append({"frame": b, "lowres": round(low, 3),
                               "fullres": round(full, 3) if full is not None else None})
        sev = classify_boundary(d)
        if sev:
            issues.append(Issue(sev, "boundary-hardcut",
                                f"씬 경계 하드컷 diff {d:.1f}% (경계 f{b})",
                                frame=b, timeSec=b / fps,
                                metrics={"diff": round(d, 2), "lowres": round(low, 2),
                                         "source": boundary_source}))

    completion_issues, completion_stats = completion_pulse_issues(lums, completion_windows, fps)
    issues.extend(completion_issues)

    # 씬 중간 후보: 원본 해상도 정밀 분석 → 번쩍 스파이크 / 워시 점프 / 씬 중간 하드컷 3분류
    labels = {"spike": "씬 중간 번쩍 스파이크", "outro-wash": "씬 끝 워시 온셋(연출)",
              "wash-jump": "씬 중간 급격한 워시 점프", "hardcut": "씬 중간 하드컷",
              "pen-brush-paint-stroke": "브러시 채색 스트로크(연출)"}
    exclude = {b + o for b in boundaries for o in (-1, 0, 1)}
    candidates = spike_candidates(diffs, roll, exclude)
    measurement_order = prioritize_spike_candidates(candidates, diffs, roll)
    spike_results, spike_remeasured = remeasure_spike_candidates(
        video, measurement_order, limit=max(0, MAX_REMEASURE - remeasured), fps=fps,
        width=spec["width"], height=spec["height"])
    remeasured += spike_remeasured
    for f in candidates:
        low = float(diffs[f])
        res = spike_results.get(f)
        if res is not None:
            peak, corr, transient = res["peak"], res["corr"], res["transient"]
        else:  # 재측정 상한 초과 — 저해상 값으로 보수 판정 (transient 가정)
            peak, corr, transient = low, 0.0, True
        near_end = any(0 <= b - f <= OUTRO_WINDOW for b in boundaries) or \
                   (n_frames - 1 - f) <= OUTRO_WINDOW
        kind, sev = judge_candidate(peak, low, float(roll[f]),
                                    transient=transient, corr=corr, near_scene_end=near_end)
        if sev:
            active_phase = phase_window_for_frame(f, completion_windows)
            phase = active_phase[0] if active_phase else None
            phase_window = active_phase[1] if active_phase else None
            # 큰 seal stroke는 그림을 실제로 확장하는 정상 draw 이벤트다. 빠른 transient
            # spike/hardcut은 그대로 두고, 구도 유지 wash-jump만 참고 정보로 낮춘다.
            if phase == "drawing" and kind == "wash-jump":
                if phase_window and phase_window.get("auditKind") == "pen-brush-paint":
                    kind = "pen-brush-paint-stroke"
                sev = "INFO"
            issues.append(Issue(sev, kind,
                                f"{labels[kind]} diff {peak:.2f}% "
                                f"(주변 중앙값 {roll[f]:.2f}%, corr {corr:.2f}, f{f})",
                                frame=f, timeSec=f / fps,
                                metrics={"diff": round(peak, 3), "lowres": round(low, 3),
                                         "rollingMedian": round(float(roll[f]), 3),
                                         "corr": round(corr, 3), "transient": transient,
                                         "phase": phase}))

    # 정지
    for run in find_freeze_runs(diffs, fps):
        sev = freeze_severity(run, boundaries, n_frames, fps)
        dur = (run[1] - run[0] + 1) / fps
        label = "씬 끝 감상 구간" if sev == "INFO" else "정지 화면"
        issues.append(Issue(sev, "freeze",
                            f"{label} {dur:.1f}s (f{run[0]}~f{run[1]})",
                            frame=run[0], timeSec=run[0] / fps,
                            metrics={"start": run[0], "end": run[1], "sec": round(dur, 2)}))

    # 레터박스
    issues.extend(detect_letterbox(scan["meanFrame"]))

    # 오디오 — BGM 없는 전달본은 mix report의 명시적 무음 AAC 선언이 있어야만
    # 전체 무음을 INFO로 처리한다. 임의의 무음/오디오 누락은 기존 FAIL 규칙을 유지한다.
    intentional_silent_audio = False
    if mix_report is not None and Path(mix_report).is_file():
        try:
            mix_data = json.loads(Path(mix_report).read_text(encoding="utf-8"))
            intentional_silent_audio = (
                mix_data.get("mode") == "off"
                and mix_data.get("bgm") is None
                and mix_data.get("voice") is None
                and bool((mix_data.get("settings") or {}).get("silentAudioTrack"))
            )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    audio_metrics = None
    if spec["hasAudio"]:
        audio_issues_found, audio_metrics = analyze_audio(
            video, spec["duration"], allow_full_silence=intentional_silent_audio)
        issues.extend(audio_issues_found)
    license_issues, license_summary = check_license_manifest(
        license_manifest, workspace_root=workspace_root)
    issues.extend(license_issues)
    voice_issues, voice_summary = check_voice_manifest(
        voice_manifest, engine_root=engine_root)
    issues.extend(voice_issues)
    mix_issues, mix_summary = check_mix_report(mix_report, spec["duration"])
    issues.extend(mix_issues)
    shape_issues, shape_summary = audio_shape_issues(video, mix_report)
    issues.extend(shape_issues)
    t2 = time.perf_counter()

    issues.sort(key=lambda i: (SEV_ORDER.get(i.severity, 9), -(i.metrics.get("diff") or 0)))
    verdict = "FAIL" if any(i.severity == "FAIL" for i in issues) else "PASS"
    result = {
        "video": str(video),
        "verdict": verdict,
        "summary": {s: sum(1 for i in issues if i.severity == s) for s in ("FAIL", "WARN", "INFO")},
        "spec": spec,
        "boundarySource": boundary_source,
        "boundaryCount": len(boundaries),
        "boundaryStats": boundary_stats,
        "completionStats": completion_stats,
        "audio": audio_metrics,
        "bgmLicense": license_summary,
        "ttsVoice": voice_summary,
        "mixReport": mix_summary,
        "audioShape": shape_summary,
        "issues": [i.to_dict() for i in issues],
        "stats": {"frames": n_frames, "remeasured": remeasured,
                  "scanSec": round(t1 - t0, 2), "detectSec": round(t2 - t1, 2),
                  "totalSec": round(t2 - t0, 2)},
    }
    report_result = sanitize_audit_paths(
        result, workspace_root=workspace_root, engine_root=engine_root,
    ) if sanitize_paths else result
    if out_dir is not None:
        write_reports(report_result, Path(out_dir), video, fps,
                      spec["width"], spec["height"], evidence=evidence)
    return report_result


def sanitize_audit_paths(value, *, workspace_root: str | Path, engine_root: str | Path):
    """Remove host roots from persisted audit JSON/Markdown while preserving diagnostics."""
    workspace = str(Path(workspace_root).resolve())
    engine = str(Path(engine_root).resolve())
    if isinstance(value, dict):
        return {
            key: sanitize_audit_paths(child, workspace_root=workspace, engine_root=engine)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_audit_paths(child, workspace_root=workspace, engine_root=engine)
            for child in value
        ]
    if isinstance(value, str):
        if value == workspace:
            return "."
        if value.startswith(workspace + "/"):
            return value[len(workspace) + 1:]
        if value == engine:
            return "$ENGINE"
        if value.startswith(engine + "/"):
            return "$ENGINE/" + value[len(engine) + 1:]
        return value.replace(workspace, "$WORKSPACE").replace(engine, "$ENGINE")
    return value


# ── 리포트 산출 ──

def _evidence_targets(result: dict) -> list[dict]:
    with_frame = [i for i in result["issues"] if i["frame"] is not None and i["kind"] != "freeze"]
    return with_frame[:EVIDENCE_MAX]  # 이미 severity·diff 순 정렬됨


def write_reports(result: dict, out_dir: Path, video: Path, fps: float,
                  width: int, height: int, evidence: bool = True) -> None:
    """audit-report.md / .json / evidence/*.png / FIELD-LOG 초안."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 증거 스틸 (문제 프레임 전후)
    ev_lines: list[str] = []
    if evidence:
        for it in _evidence_targets(result):
            f = it["frame"]
            ok = save_evidence_pair(video, f, fps, width, height,
                                    out_dir / "evidence" / f"f{f:06d}-prev.png",
                                    out_dir / "evidence" / f"f{f:06d}-at.png")
            if ok:
                ev_lines.append(f"- f{f} ({fmt_ts(it['timeSec'])}) {it['kind']}: "
                                f"`evidence/f{f:06d}-prev.png` → `evidence/f{f:06d}-at.png`")

    (out_dir / "audit-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["spec"]
    md = [f"# audit-report — {Path(result['video']).name}", "",
          f"- **판정: {result['verdict']}** (FAIL {result['summary']['FAIL']} / "
          f"WARN {result['summary']['WARN']} / INFO {result['summary']['INFO']})",
          f"- 규격: {s['width']}×{s['height']} {s['fps']:.6g}fps {s['vcodec']}"
          f"/{s['acodec'] or '오디오 없음'} · {s['duration']:.1f}s · {result['stats']['frames']}f",
          f"- 씬 경계: {result['boundarySource']} ({result['boundaryCount']}개) · "
          f"스캔 {result['stats']['scanSec']}s + 검출 {result['stats']['detectSec']}s "
          f"= 총 {result['stats']['totalSec']}s · 재측정 {result['stats']['remeasured']}건", ""]

    if result.get("audio"):
        a = result["audio"]
        md += ["## 오디오 요약",
               f"- Integrated: {a.get('integratedLufs')} LUFS · True Peak: {a.get('truePeakDbtp')} dBTP "
               f"· mean/max: {a.get('meanVolume')}/{a.get('maxVolume')} dB", ""]
    if result.get("completionStats"):
        stats = result["completionStats"]
        worst = max((float(x.get("reversal", 0)) for x in stats), default=0.0)
        md += ["## 완료 구간 요약",
               f"- integrated-develop {len(stats)}씬 · 최대 밝기 역전 {worst:.2f} luma "
               f"(WARN>{COMPLETION_REVERSAL_WARN:.1f}, FAIL>{COMPLETION_REVERSAL_FAIL:.1f})", ""]
    if result.get("bgmLicense"):
        lic = result["bgmLicense"]
        md += ["## BGM 라이선스",
               f"- assetId: {', '.join(str(x) for x in lic.get('assetIds', []))}",
               f"- 정책: {lic.get('licensePolicy')} · 매니페스트: `{lic.get('path')}`", ""]
    if result.get("mixReport"):
        mix = result["mixReport"]
        md += ["## 믹싱 계약",
               f"- mode: {mix.get('mode')} · tracks: {mix.get('trackCount')} · "
               f"crossfade: {mix.get('crossfadeSec')}s · ducking: {mix.get('ducking')}",
               f"- report: `{mix.get('path')}`", ""]
    if result.get("ttsVoice"):
        voice = result["ttsVoice"]
        if voice.get("engine") == "supertonic" or voice.get("voicePackVersion") is not None:
            md += ["## TTS 음성 재현성",
                   f"- voice: {voice.get('voicePresetId')} (요청 {voice.get('requestedVoice')}) · "
                   f"pack {voice.get('voicePackVersion')} · Supertonic {voice.get('packageVersion')} / {voice.get('model')}",
                   f"- components: {voice.get('components')} · speed: {voice.get('speed')}",
                   f"- AI 고지: {voice.get('aiDisclosure')} · manifest: `{voice.get('path')}`", ""]
        else:
            md += ["## TTS 음성 재현성",
                   f"- engine: {voice.get('engine')} · voice: {voice.get('voice')} · "
                   f"package {voice.get('packageVersion')} · model {voice.get('model')} @ {voice.get('modelRevision')}",
                   f"- sample rate: {voice.get('nativeSampleRate')}Hz → {voice.get('outputSampleRate')}Hz · "
                   f"speed: {voice.get('requestedSpeed')} → {voice.get('appliedSpeed')} ({voice.get('speedAppliedBy')})"]
            if voice.get("engine") == "qwen3-base":
                md.append(
                    f"- reference voice: {voice.get('referenceVoiceId')} · audio hash: {voice.get('referenceAudioSha256')} · "
                    f"transcript hash: {voice.get('referenceTranscriptSha256')}"
                )
            md += [f"- AI 고지: {voice.get('aiDisclosure')} · manifest: `{voice.get('path')}`", ""]

    md += ["## 검출 이슈", ""]
    if result["issues"]:
        md += ["| 심각도 | 종류 | 프레임 | 시각 | 내용 |", "| --- | --- | --- | --- | --- |"]
        for it in result["issues"]:
            fr = f"f{it['frame']}" if it["frame"] is not None else "-"
            md.append(f"| {it['severity']} | {it['kind']} | {fr} | {fmt_ts(it['timeSec'])} "
                      f"| {it['message']} |")
    else:
        md.append("검출된 이슈 없음.")
    md.append("")

    bs = [b for b in result["boundaryStats"]]
    if bs:
        vals = [(b["fullres"] if b["fullres"] is not None else b["lowres"]) for b in bs]
        md += ["## 씬 경계 diff 요약",
               f"- {len(bs)}개 경계 · 최대 {max(vals):.2f}% · 평균 {sum(vals) / len(vals):.2f}% "
               f"(WARN>{BOUNDARY_WARN}%, FAIL>{BOUNDARY_FAIL}%)", ""]

    if ev_lines:
        md += ["## 증거 스틸", *ev_lines, ""]

    top = [i for i in result["issues"] if i["severity"] in ("FAIL", "WARN")][:3]
    found = "; ".join(f"{i['message']}" for i in top) if top else "이슈 없음"
    md += ["## FIELD-LOG 초안", "", "```markdown",
           f"## {time.strftime('%Y-%m-%d')} · {Path(result['video']).stem} (video-auditor 자동 검수)",
           f"- **발견**: {found}",
           "- **원인**: (조사 후 기입)",
           "- **수정**: (수정 후 기입)",
           "- **환류** ★필수: (반영한 문서/검증기/프리셋 경로)",
           "```", ""]
    (out_dir / "audit-report.md").write_text("\n".join(md), encoding="utf-8")
