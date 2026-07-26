"""cues.py — SRT → frame cue 변환, 긴 문장 분할, 씬 그룹핑, 타이틀 색 추출.

- parse_srt(): SRT 텍스트 → (start초, end초, text) 목록. 타임코드 역전은 명시적 에러.
- srt_to_cues(): 초 → 프레임 환산(fps 30) + 한 줄 초과 문장은 글리프 비례로 분할.
- group_scenes(): cue 를 씬 경계로 그룹핑 (씬당 길이 상·하한, cue 프레임은 씬-로컬로 변환).
- title_color(): 이미지 도미넌트 채도색 추출 → 가독성 위해 어둡게 보정한 hex.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

FPS = 30

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


@dataclass
class SrtEntry:
    """SRT 항목 1개 (초 단위)."""

    start: float
    end: float
    text: str


def _parse_time(s: str, context: str) -> float:
    m = _TIME_RE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"SRT 타임코드 형식 오류: '{s}' ({context})")
    hh, mm, ss, ms = m.groups()
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[SrtEntry]:
    """SRT 텍스트 파싱. 타임코드 역전(시작≥끝, 이전 항목보다 과거로 점프)은 ValueError."""
    entries: list[SrtEntry] = []
    blocks = re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n"))
    prev_end = -1.0
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        # 첫 줄이 인덱스 번호면 건너뜀
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            raise ValueError(f"SRT 블록에 타임라인 없음: {block[:60]!r}")
        t0_s, t1_s = [part.strip() for part in lines[0].split("-->")[:2]]
        t0, t1 = _parse_time(t0_s, block[:40]), _parse_time(t1_s, block[:40])
        if t1 <= t0:
            raise ValueError(f"SRT 타임코드 역전: {t0_s} --> {t1_s}")
        if t0 < prev_end - 1e-6:
            raise ValueError(f"SRT 타임코드 역행: 항목 시작 {t0_s} 이 이전 항목 끝보다 빠름")
        prev_end = t1
        body = " ".join(lines[1:]).strip()
        if body:
            entries.append(SrtEntry(t0, t1, body))
    return entries


def visual_len(s: str) -> float:
    """한글/전각 ~1.0, 라틴/공백 ~0.55 근사 (한 줄 폭 판단용)."""
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in s)


def split_words(text: str, max_units: float) -> list[str]:
    """단어 경계로 max_units(글리프 폭) 이하 줄들로 분할."""
    lines: list[str] = []
    cur = ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if cur and visual_len(cand) > max_units:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def split_cue(text: str, f0: int, f1: int, max_chars: float = 30.0, min_hold: int = 36) -> list[dict]:
    """긴 문장 1개를 한 줄 cue 여러 개로 분할. 프레임은 각 조각 폭에 비례 배분."""
    lines = split_words(text, max_chars)
    total = sum(visual_len(ln) for ln in lines) or 1.0
    span = max(1, f1 - f0)
    cues, t = [], f0
    for i, ln in enumerate(lines):
        dur = max(min_hold, round(span * visual_len(ln) / total))
        end = f1 if i == len(lines) - 1 else min(f1, t + dur)
        cues.append({"text": ln, "from": int(t), "to": int(end)})
        t = end
    return cues


def srt_to_cues(srt_text: str, fps: int = FPS, max_chars: float = 30.0, min_hold: int = 36) -> list[dict]:
    """SRT → [{text, from, to}] (프레임 단위). 한 줄 초과 문장은 자동 분할."""
    cues: list[dict] = []
    for e in parse_srt(srt_text):
        f0, f1 = round(e.start * fps), round(e.end * fps)
        if visual_len(e.text) > max_chars:
            cues.extend(split_cue(e.text, f0, f1, max_chars, min_hold))
        else:
            cues.append({"text": e.text, "from": f0, "to": f1})
    return cues


def group_scenes(cues: list[dict], *, min_frames: int = 240, max_frames: int = 450,
                 tail_pad: int = 30) -> list[dict]:
    """cue 를 씬 경계로 그룹핑. 반환: [{durationInFrames, cues(씬-로컬 프레임)}].

    씬 길이가 max_frames 를 넘기 직전(그리고 min_frames 이상)에 경계를 자른다.
    """
    if not cues:
        return []
    scenes: list[dict] = []
    start = cues[0]["from"]
    group: list[dict] = []
    for cue in cues:
        if group and cue["to"] - start > max_frames and group[-1]["to"] - start >= min_frames:
            scenes.append(_close_scene(group, start, tail_pad))
            start = cue["from"]
            group = []
        group.append(cue)
    scenes.append(_close_scene(group, start, tail_pad))
    return scenes


def _close_scene(group: list[dict], start: int, tail_pad: int) -> dict:
    local = [{"text": c["text"], "from": c["from"] - start, "to": c["to"] - start} for c in group]
    return {"durationInFrames": local[-1]["to"] + tail_pad, "cues": local}


def title_color(image_path: str | Path, min_sat: int = 30, darken: float = 0.62) -> str:
    """이미지 도미넌트 채도색(hex). 채도 있는 픽셀의 주요 hue 군집 평균색을 어둡게 보정."""
    img = Image.open(image_path).convert("RGB").resize((480, 270))
    arr = np.asarray(img).astype(int)
    mx, mn = arr.max(2), arr.min(2)
    sat = mx - mn
    mask = sat > min_sat
    if not mask.any():
        return "#5a544c"  # 채도 없음 — 잉크 톤 폴백
    px = arr[mask].astype(np.float32)
    hsv = np.asarray(Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8)).convert("HSV")).reshape(-1, 3)
    hist = np.bincount(hsv[:, 0] // 16, minlength=16)  # hue 16버킷
    bucket = int(hist.argmax())
    sel = (hsv[:, 0] // 16) == bucket
    r, g, b = px[sel].mean(axis=0)
    r, g, b = (int(max(0, min(255, v * darken))) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"
