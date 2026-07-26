"""background.py — 배경 이미지 준비.

- clean(): 원본의 종이/격자/여백(near-paper) 픽셀을 종이색으로 치환 (붓이 지나가도 배경만 드러나게)
- generate(): 전략 3종
    imagegen    — codex exec 내장 image_gen 으로 손그림 배경 생성 (codex 부재 시 preset 폴백 + 경고)
    preset      — PIL 절차 합성 (잉크 스트로크 + 수채 워시, 시드 고정 결정적)
    user-images — 사용자 이미지를 종이 캔버스(기본 1920×1080, shorts 1080×1920)에 contain-fit 배치
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)

DEFAULT_SIZE = (1920, 1080)  # 기본 캔버스 (youtube 가로) — shorts 는 (1080, 1920) 전달
PAPER = (251, 250, 246)  # #fbfaf6
CODEX_APP_BIN = "/Applications/Codex.app/Contents/Resources/codex"

_PROMPT_TEMPLATE = (
    "A hand-drawn ink line-art sketch combined with soft, light pastel watercolor washes, "
    "on a clean warm-white paper background. Subject: {subject}. Loose confident pen strokes, "
    "delicate watercolor in violet / teal / amber tones, airy editorial composition with GENEROUS "
    "EMPTY WHITE SPACE in the upper-left third (reserved for title and widgets). Absolutely NO text, "
    "no letters, no numbers, no labels. Minimal, elegant, high detail, 16:9 landscape."
)

# 세로(9:16) 전용 — 상단 1/4 하늘·여백(타이틀 세이프존), 중앙 주 소재, 하단 근경
_PROMPT_TEMPLATE_PORTRAIT = (
    "A hand-drawn ink line-art sketch combined with soft, light pastel watercolor washes, "
    "on a clean warm-white paper background. Subject: {subject}. "
    "9:16 VERTICAL full-bleed composition: the top quarter is open sky / airy negative space "
    "(reserved as a safe zone for a title), the main subject sits in the middle band, and the "
    "bottom shows a closer foreground detail. Loose confident pen strokes, delicate watercolor "
    "in violet / teal / amber tones. Absolutely NO text, no letters, no numbers, no labels. "
    "Minimal, elegant, high detail, 9:16 portrait."
)


def _pick_prompt(size: tuple[int, int]) -> str:
    """캔버스 방향에 따라 프롬프트 템플릿 선택 (세로 = height > width)."""
    return _PROMPT_TEMPLATE_PORTRAIT if size[1] > size[0] else _PROMPT_TEMPLATE


def clean(image_path: str | Path, out_path: str | Path,
          lum_thresh: float = 237.0, sat_thresh: float = 12.0,
          paper: tuple[int, int, int] = PAPER) -> Path:
    """near-paper 픽셀(밝고 채도 낮음)을 종이색으로 치환한 정제 이미지를 저장."""
    im = Image.open(image_path).convert("RGB")
    arr = np.asarray(im).astype(np.int16)
    mx = arr.max(2)
    mn = arr.min(2)
    lum = arr.mean(2)
    content = (lum < lum_thresh) | ((mx - mn) > sat_thresh)
    out = arr.copy()
    for c, v in enumerate(paper):
        out[:, :, c][~content] = v
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.astype(np.uint8)).save(out_path)
    log.info("clean: content %.1f%% -> %s", content.mean() * 100, out_path)
    return out_path


PEN_PAPER = (242, 238, 227)  # pen 프로파일 종이 톤 (#f2eee3)


def contain_fit(image: Image.Image, size: tuple[int, int] = DEFAULT_SIZE,
                paper: tuple[int, int, int] = PEN_PAPER) -> Image.Image:
    """이미지를 잘림 없이 contain-fit — 남는 영역은 종이색 패딩 (pen 배경 규칙)."""
    tw, th = size
    canvas = Image.new("RGB", (tw, th), paper)
    im = image.convert("RGB")
    scale = min(tw / im.width, th / im.height)
    nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    canvas.paste(im.resize((nw, nh), Image.LANCZOS), ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def compose_dark_rgba(image_path: str | Path, out_path: str | Path, *,
                      size: tuple[int, int] = DEFAULT_SIZE, fit: str = "cover") -> Path:
    """우주 프로파일용 RGBA 합성 — 투명 화소의 숨은 RGB 노출을 막고 alpha를 보존한다."""
    if fit not in {"contain", "cover"}:
        raise ValueError(f"dark RGBA fit은 contain|cover: {fit!r}")
    tw, th = size
    im = Image.open(image_path).convert("RGBA")
    scale = max(tw / im.width, th / im.height) if fit == "cover" else min(tw / im.width, th / im.height)
    nw, nh = max(1, int(round(im.width * scale))), max(1, int(round(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    if fit == "cover":
        left, top = max(0, (nw - tw) // 2), max(0, (nh - th) // 2)
        im = im.crop((left, top, left + tw, top + th))
        nw, nh = im.size
    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((tw - nw) // 2, (th - nh) // 2))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def separate_ink(bg_path: str | Path, out_alpha_path: str | Path, out_flat_path: str | Path,
                 paper: tuple[int, int, int] = PEN_PAPER,
                 size: tuple[int, int] = DEFAULT_SIZE) -> dict:
    """잉크-알파 분리 (pen 프로파일 핵심): 종이는 항상 보이고 잉크만 리빌되도록.

    contain-fit(잘림 금지) 후 lum/sat 기반 잉크 알파를 계산한다:
      a_dark = (205 - lum) / 40, a_color = (sat - 45) / 50, alpha = max(둘) 0..1 클램프(경계 그라데이션)
    산출: out_alpha_path = 잉크 RGBA, out_flat_path = 잉크만 흰 배경(routes 입력용).
    """
    src = contain_fit(Image.open(bg_path), size=size, paper=paper)
    arr = np.asarray(src).astype(np.float32)
    lum = arr.mean(axis=2)
    sat = arr.max(axis=2) - arr.min(axis=2)
    a_dark = (205.0 - lum) / 40.0
    a_color = (sat - 45.0) / 50.0
    alpha = np.clip(np.maximum(a_dark, a_color), 0.0, 1.0)

    out_alpha_path, out_flat_path = Path(out_alpha_path), Path(out_flat_path)
    out_alpha_path.parent.mkdir(parents=True, exist_ok=True)
    out_flat_path.parent.mkdir(parents=True, exist_ok=True)

    rgba = np.dstack([arr.astype(np.uint8), (alpha * 255).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(out_alpha_path)

    # flat: 잉크를 흰 배경 위에 알파 합성 (콘텐츠 마스크/routes 생성용)
    white = np.full_like(arr, 255.0)
    flat = arr * alpha[..., None] + white * (1.0 - alpha[..., None])
    Image.fromarray(flat.astype(np.uint8)).save(out_flat_path)

    ink_frac = float((alpha > 0.5).mean())
    log.info("separate_ink: 잉크 %.1f%% -> %s / %s", ink_frac * 100, out_alpha_path.name, out_flat_path.name)
    return {"alpha": out_alpha_path, "flat": out_flat_path, "inkFraction": ink_frac}


def find_codex() -> str | None:
    """작동하는 codex 바이너리 경로 (앱 우선, 없으면 PATH)."""
    configured = os.environ.get("DRAW_PROXY_CODEX_BIN")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return str(candidate) if candidate.is_file() else None
    if Path(CODEX_APP_BIN).is_file():
        return CODEX_APP_BIN
    return shutil.which("codex")


def generate(strategy: str, out_path: str | Path, *, subject: str | None = None,
             images: list[str | Path] | None = None, seed: int = 1,
             codex_bin: str | None = None, size: tuple[int, int] = DEFAULT_SIZE,
             fit: str = "contain", imagegen_fallback: str = "preset") -> dict:
    """전략에 따라 배경 PNG 생성. 반환: {"strategy": 실제 사용 전략, "path": 경로}."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if imagegen_fallback not in {"fail", "preset"}:
        raise ValueError(f"imagegen_fallback은 fail|preset 중 하나여야 함: {imagegen_fallback!r}")

    if strategy == "imagegen":
        cx = codex_bin if codex_bin is not None else find_codex()
        if cx and Path(cx).is_file():
            try:
                _gen_imagegen(cx, subject or "a calm natural landscape", out_path, size=size)
                return {"strategy": "imagegen", "path": out_path}
            except Exception as exc:
                if imagegen_fallback == "fail":
                    raise RuntimeError("DRAW_IMAGEGEN_REQUIRED_FAILED") from exc
                log.warning("imagegen 생성 실패 — preset 배경으로 폴백", exc_info=True)
        elif imagegen_fallback == "fail":
            raise RuntimeError("DRAW_IMAGEGEN_REQUIRED_UNAVAILABLE")
        else:
            log.warning("codex exec 부재 — imagegen 불가, preset 배경으로 폴백")
        strategy = "preset"

    if strategy == "preset":
        _gen_preset(out_path, subject=subject, seed=seed, size=size)
        return {"strategy": "preset", "path": out_path}

    if strategy == "user-images":
        if not images:
            raise ValueError("user-images 전략에는 images 가 최소 1장 필요")
        if fit not in {"contain", "cover"}:
            raise ValueError(f"user-images fit은 contain|cover 중 하나여야 함: {fit!r}")
        _compose_user_images([Path(p) for p in images], out_path, size=size, fit=fit)
        return {"strategy": "user-images", "path": out_path, "fit": fit}

    raise ValueError(f"알 수 없는 배경 전략: {strategy}")


def _gen_imagegen(codex_bin: str, subject: str, out_path: Path,
                  size: tuple[int, int] = DEFAULT_SIZE) -> None:
    """codex exec 내장 image_gen 으로 배경 생성 (API 키 불필요). 세로 캔버스면 세로 템플릿."""
    prompt = _pick_prompt(size).format(subject=subject)
    instruction = (
        "너의 내장 image_gen 툴(imagegen 스킬)만 사용해 이미지 1장을 생성해. "
        "OPENAI_API_KEY 기반 CLI는 절대 쓰지 마. "
        f"생성 후 결과 PNG를 정확히 '{out_path}' 로 복사해. 프롬프트: {prompt}"
    )
    subprocess.run(
        [codex_bin, "exec", "--dangerously-bypass-approvals-and-sandbox",
         "--skip-git-repo-check", "-C", str(out_path.parent), instruction],
        check=True,
    )
    if not out_path.is_file():
        raise RuntimeError(f"imagegen 결과 파일 생성 실패: {out_path}")


def _gen_preset(out_path: Path, subject: str | None = None, seed: int = 1,
                size: tuple[int, int] = DEFAULT_SIZE) -> None:
    """PIL 절차 합성 폴백 배경 — 종이 위 수채 워시 + 잉크 능선 스케치 (시드 고정 결정적)."""
    w, h = size
    rng = np.random.default_rng(seed)
    canvas = Image.new("RGB", (w, h), PAPER)

    # 수채 워시 (반투명 타원 → 블러)
    wash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wash)
    palette = [(148, 122, 190), (94, 158, 160), (214, 168, 96)]  # violet / teal / amber
    alpha = 46
    if h > w:
        # 세로(쇼츠) 전용: 시드로 도미넌트 색을 회전시켜 씬마다 팔레트가 다르게 —
        # 자막 강조색(title_color) 씬 동조용. 가로 경로는 위 기본값 그대로(바이트 불변).
        k = seed % len(palette)
        palette = palette[k:] + palette[:k]
        palette = [palette[0]] * 3 + palette  # 도미넌트 워시 비중 확대
        alpha = 92
    for i in range(7):
        col = palette[i % len(palette)]
        cx = int(rng.uniform(w * 0.42, w * 0.92))
        cy = int(rng.uniform(h * 0.3, h * 0.86))
        rx = int(rng.uniform(120, 300))
        ry = int(rng.uniform(70, 170))
        wd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=col + (alpha,))
    wash = wash.filter(ImageFilter.GaussianBlur(26))
    canvas.paste(wash, (0, 0), wash)

    # 잉크 능선/지평선 스케치 (우측 하단 위주 — 좌상단은 타이틀/위젯용 여백)
    d = ImageDraw.Draw(canvas)
    ink = (52, 48, 44)
    for k in range(3):
        base_y = h * (0.52 + 0.14 * k)
        amp = 36 + 22 * k
        pts = []
        for x in range(int(w * 0.34), w - 90, 24):
            t = x / w * math.tau
            y = base_y + amp * math.sin(t * (1.4 + 0.5 * k) + k) + float(rng.normal(0, 4))
            pts.append((x, min(h - 80.0, max(h * 0.3, y))))
        d.line(pts, fill=ink, width=4 - k if k < 3 else 2, joint="curve")
    # 소나무 실루엣 획
    for k in range(4):
        tx = int(rng.uniform(w * 0.5, w * 0.9))
        ty = int(rng.uniform(h * 0.42, h * 0.6))
        th = int(rng.uniform(90, 190))
        d.line([(tx, ty), (tx, ty + th)], fill=ink, width=5)
        for j in range(4):
            yy = ty + th * (j + 1) / 5
            span = th * 0.32 * (j + 2) / 5
            d.line([(tx - span, yy), (tx + span, yy - th * 0.1)], fill=ink, width=3)

    canvas.save(out_path)
    log.info("preset 배경 생성 (seed=%d, subject=%s, %dx%d) -> %s", seed, subject, w, h, out_path)


def _compose_user_images(images: list[Path], out_path: Path,
                         size: tuple[int, int] = DEFAULT_SIZE,
                         fit: str = "contain") -> None:
    """사용자 이미지들을 가로 배치. cover는 슬롯을 빈틈없이 채우고 중앙 크롭한다."""
    w, h = size
    canvas = Image.new("RGB", (w, h), PAPER)
    n = len(images)
    margin = 0 if fit == "cover" else 90
    slot_w = (w - margin * 2) // n
    slot_h = h - margin * 2
    for i, p in enumerate(images):
        im = Image.open(p).convert("RGB")
        scale = max(slot_w / im.width, slot_h / im.height) if fit == "cover" \
            else min(slot_w / im.width, slot_h / im.height)
        nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        im = im.resize((nw, nh), Image.LANCZOS)
        if fit == "cover":
            left = max(0, (nw - slot_w) // 2)
            top = max(0, (nh - slot_h) // 2)
            im = im.crop((left, top, left + slot_w, top + slot_h))
            nw, nh = im.size
        x = margin + i * slot_w + (slot_w - nw) // 2
        y = margin + (slot_h - nh) // 2
        canvas.paste(im, (x, y))
    canvas.save(out_path)
