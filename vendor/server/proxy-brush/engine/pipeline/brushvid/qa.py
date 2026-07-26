"""qa.py — QA 산출물: 프레임 캡처 → capture-manifest.json → 콘택트시트 PNG.

캡처는 `npx remotion still`(props 기준)으로, 콘택트시트는 PIL 그리드로 만든다.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

from .public_assets import public_roots_for_props, scoped_public_dir
from .render import ENTRY, REPO_ROOT, remotion_browser_executable

log = logging.getLogger(__name__)
QA_MAX_WIDTH = 960


def completion_timing(scene: dict, routes: dict) -> dict:
    """integrated-develop 씬의 실제 완료 구간을 props+routes에서 계산한다."""
    strokes = routes.get("strokes") or []
    meta = routes.get("meta") or {}
    last_end = max((float(s.get("end", 0)) for s in strokes),
                   default=float(meta.get("drawEnd", 0)))
    develop = int(scene.get("developFrames", 0))
    settle = int(scene.get("colorSettleFrames", 0))
    duration = int(scene["durationInFrames"])
    outro = int(scene.get("outroFadeFrames", 0))
    return {
        "sceneId": scene.get("id", "<scene>"),
        "drawStart": round(float(meta.get("drawStart", 0)), 2),
        "lastStrokeEnd": round(last_end, 2),
        "developEnd": round(last_end + develop, 2),
        "colorSettleEnd": round(last_end + develop + settle, 2),
        "outroStart": duration - outro,
        "durationInFrames": duration,
    }


def completion_sample_frames(timing: dict) -> list[tuple[str, int]]:
    """완료 전후를 대표하는 로컬 프레임. outro/빈 종이는 선택하지 않는다."""
    last = float(timing["lastStrokeEnd"])
    develop_end = float(timing["developEnd"])
    settle_end = float(timing["colorSettleEnd"])
    outro_start = int(timing["outroStart"])
    hold = min(outro_start - 2, max(round(settle_end) + 2, round(settle_end)))
    raw = [
        ("draw-end", round(last)),
        ("develop-mid", round((last + develop_end) / 2)),
        ("develop-end", round(develop_end)),
        ("settle-mid", round((develop_end + settle_end) / 2)),
        ("settle-end", round(settle_end)),
        ("hold", hold),
    ]
    # 0프레임 구간이나 반올림 중복은 첫 레이블을 유지한다.
    out: list[tuple[str, int]] = []
    seen: set[int] = set()
    for label, frame in raw:
        frame = max(0, min(int(timing["durationInFrames"]) - 1, frame))
        if frame not in seen:
            out.append((label, frame))
            seen.add(frame)
    return out


def _frame_color_metrics(path: Path) -> tuple[float, float]:
    im = Image.open(path).convert("RGB")
    lum = ImageStat.Stat(im.convert("L")).mean[0] / 255.0
    sat = ImageStat.Stat(im.convert("HSV").getchannel("S")).mean[0] / 255.0
    return float(lum), float(sat)


def write_completion_report(out_path: str | Path, *, timings: list[dict],
                            entries: list[dict], qa_dir: str | Path,
                            luma_tolerance: float = 0.008,
                            saturation_tolerance: float = 0.01) -> dict:
    """캡처된 완료 프레임의 밝기 역전·채도 역전을 판정한다."""
    qa_dir = Path(qa_dir)
    by_frame = {int(e["frame"]): e for e in entries}
    scenes = []
    for timing in timings:
        samples = []
        for item in timing.get("samples", []):
            entry = by_frame.get(int(item["frame"]))
            if entry is None:
                continue
            lum, sat = _frame_color_metrics(qa_dir / entry["file"])
            samples.append({"label": item["label"], "frame": int(item["frame"]),
                            "luma": round(lum, 6), "saturation": round(sat, 6)})
        lumas = [s["luma"] for s in samples]
        sats = [s["saturation"] for s in samples]
        overall = (lumas[-1] - lumas[0]) if len(lumas) > 1 else 0.0
        direction = -1 if overall < -luma_tolerance else (1 if overall > luma_tolerance else 0)
        if direction < 0:
            luma_ok = all(b <= a + luma_tolerance for a, b in zip(lumas, lumas[1:]))
        elif direction > 0:
            luma_ok = all(b >= a - luma_tolerance for a, b in zip(lumas, lumas[1:]))
        else:
            luma_ok = (max(lumas, default=0) - min(lumas, default=0)) <= luma_tolerance * 2
        saturation_ok = all(b >= a - saturation_tolerance for a, b in zip(sats, sats[1:]))
        scenes.append({**{k: v for k, v in timing.items() if k != "samples"},
                       "samples": samples, "lumaMonotonic": luma_ok,
                       "saturationMonotonic": saturation_ok,
                       "pass": bool(len(samples) >= 3 and luma_ok and saturation_ok)})
    payload = {"schemaVersion": 1, "scenes": scenes,
               "summary": {"passed": sum(s["pass"] for s in scenes),
                           "total": len(scenes),
                           "pass": bool(scenes and all(s["pass"] for s in scenes))}}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def write_pen_brush_report(out_path: str | Path, *, project_id: str, scenes: list[dict]) -> dict:
    """pen-brush 수치 계약을 기계 판정해 JSON으로 저장."""
    checks = []
    for scene in scenes:
        outline = scene["outline"]
        paint = scene["paint"]
        timing_ok = (
            float(outline["drawStart"]) < float(outline["drawEnd"])
            <= float(paint["drawStart"]) < float(paint["drawEnd"])
            < float(paint["durationInFrames"])
        )
        overlap = max(0.0, float(outline["penInvisibleAfter"]) - float(paint["drawStart"]))
        checks.append({
            "sceneId": scene["sceneId"],
            "outlineCoverage": outline.get("coverage"),
            "paintCoverage": paint.get("coverage"),
            "paintMissingPixels": paint.get("missingPixels"),
            # paint가 outline pen-off 뒤 시작하는 계약이므로 outline 완료 시 색상 레이어 노출은 0.
            "colorLeakAtOutlineEnd": 0.0 if float(paint["drawStart"]) > float(outline["drawEnd"]) else 1.0,
            "cursorOverlapFrames": overlap,
            "phaseTimingValid": timing_ok,
            "lineThickness": scene.get("lineThickness"),
            "finalLineThickness": scene.get("finalLineThickness"),
            "finalLineThicknessDeltaPct": scene.get("finalLineThicknessDeltaPct"),
            "pass": bool(float(outline.get("coverage", 0)) >= 0.99
                         and float(paint.get("coverage", 0)) >= 0.9999
                         and int(paint.get("missingPixels", 1)) == 0
                         and overlap == 0 and timing_ok
                         and abs(float(scene.get("finalLineThicknessDeltaPct", 999))) <= 5.0),
        })
    result = {"projectId": project_id, "profile": "pen-brush", "pass": all(c["pass"] for c in checks),
              "scenes": checks}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_cosmic_random_brush_report(out_path: str | Path, *, project_id: str,
                                     scenes: list[dict]) -> dict:
    """cosmic-random-brush의 브러싱 방식 불변 조건을 판정한다."""
    checks = []
    require_content_coverage = len(scenes) in (6, 60)
    for scene in scenes:
        meta = scene["meta"]
        widths = meta.get("brushWidthRange") or [0, 9999]
        coordinate_scale = float(meta.get("width", 1920)) / 1920.0
        timing_ok = (
            36 <= float(meta.get("drawStart", -1)) <= 40
            and float(meta.get("drawEnd", 999)) <= 210
            and float(meta.get("drawEnd", 999)) <= float(meta.get("brushInvisibleAfter", -1))
            < float(meta.get("settleStart", -1)) < float(meta.get("settleEnd", -1)) < 300
        )
        item = {
            "sceneId": scene["sceneId"],
            "family": meta.get("family"),
            "strokeCount": meta.get("strokeCount"),
            "baseStrokeCount": meta.get("baseStrokeCount"),
            "coverageStrokeCount": meta.get("coverageStrokeCount"),
            "brushWidthRange": widths,
            "maskCoverage": meta.get("maskCoverage"),
            "visibleContentFraction": meta.get("visibleContentFraction"),
            "visibleContentCoverage": meta.get("visibleContentCoverage"),
            "contentCoverageRequired": require_content_coverage,
            "meanCenterJump": meta.get("meanCenterJump"),
            "maxCenterJump": meta.get("maxCenterJump"),
            "timingValid": timing_ok,
            "deterministic": meta.get("deterministic"),
        }
        item["pass"] = bool(
            item["family"] == "free-random-touch"
            and item["baseStrokeCount"] == 36
            and 1 <= int(item["coverageStrokeCount"] or 0) <= 20
            and int(item["strokeCount"] or 0) == 36 + int(item["coverageStrokeCount"] or 0)
            and float(widths[0]) >= 230 * coordinate_scale
            and float(widths[1]) <= 365 * coordinate_scale
            and float(item["maskCoverage"] or 0) >= 0.991
            and float(item["meanCenterJump"] or 0) >= 650 * coordinate_scale
            and float(item["maxCenterJump"] or 0) >= 1200 * coordinate_scale
            and (not require_content_coverage
                 or (0.001 <= float(item["visibleContentFraction"] or 0) <= 1
                     and float(item["visibleContentCoverage"] or 0) >= 0.985))
            and item["deterministic"] is True and timing_ok
        )
        checks.append(item)
    result = {"projectId": project_id, "profile": "cosmic-random-brush",
              "pass": all(c["pass"] for c in checks), "scenes": checks}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_full_bleed_report(out_path: str | Path, *, project_id: str, profile: str,
                            scenes: list[dict], expected_scene_count: int) -> dict:
    """누적 프레임/풀블리드 서사 프로필의 순서·전환·중복 오버레이 계약을 검증한다."""
    transition_frames = 30 if profile == "progressive-frame-sequence" else 24
    checks = []
    seen_images: set[str] = set()
    for index, scene in enumerate(scenes):
        image = scene.get("image")
        expected_previous = None if index == 0 else scenes[index - 1].get("image")
        uses_no_routes = not scene.get("routes") and not scene.get("drawingPhases")
        source_overlay_free = (scene.get("captionsVisible") is False
                               and not scene.get("topTitle")
                               and not scene.get("widgets"))
        duplicate_image = not isinstance(image, str) or image in seen_images
        if isinstance(image, str):
            seen_images.add(image)
        item = {
            "sceneId": scene.get("id", f"scene-{index + 1:02d}"),
            "presentation": scene.get("presentation"),
            "image": image,
            "previousImage": scene.get("previousImage"),
            "expectedPreviousImage": expected_previous,
            "transitionFrames": transition_frames,
            "orderValid": scene.get("id") == f"scene-{index + 1:02d}",
            "previousFrameLinked": scene.get("previousImage") == expected_previous,
            "routeOrPenOverlayAbsent": uses_no_routes,
            "sourceFrameOverlayFree": source_overlay_free,
            "duplicateSourceFrame": duplicate_image,
        }
        item["pass"] = bool(
            scene.get("presentation") == profile
            and isinstance(image, str) and bool(image)
            and item["orderValid"]
            and item["previousFrameLinked"]
            and item["routeOrPenOverlayAbsent"]
            and item["sourceFrameOverlayFree"]
            and not item["duplicateSourceFrame"]
        )
        checks.append(item)
    result = {
        "projectId": project_id,
        "profile": profile,
        "expectedSceneCount": expected_scene_count,
        "transitionFrames": transition_frames,
        "pass": bool(len(checks) == expected_scene_count and checks and all(c["pass"] for c in checks)),
        "scenes": checks,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def capture_frames(props_path: str | Path, frames: list[int], out_dir: str | Path, *,
                   composition: str = "BrushLandscape", labels: dict[int, str] | None = None,
                   repo_root: str | Path = REPO_ROOT,
                   public_root: str | Path | None = None) -> list[dict]:
    """지정 프레임들을 PNG 스틸로 캡처. 반환: manifest 항목 목록."""
    out_dir = Path(out_dir).resolve()  # 서브프로세스 cwd(repo_root)와 무관하게 절대 경로 사용
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    resolved_public_root = Path(public_root).resolve() if public_root else Path(repo_root) / "public"
    with scoped_public_dir(props_path, public_root=resolved_public_root) as public_dir:
        roots = ", ".join(p.name for p in public_roots_for_props(props_path, resolved_public_root))
        log.info("QA scoped public: %s", roots or "(empty)")
        for f in frames:
            png = out_dir / f"frame-{f:05d}.png"
            cmd = ["npx", "remotion", "still", ENTRY, composition, str(png),
                   f"--props={Path(props_path).resolve()}", f"--frame={f}",
                   f"--public-dir={public_dir}", "--bundle-cache=false"]
            browser = remotion_browser_executable(repo_root)
            if browser:
                cmd.append(f"--browser-executable={browser}")
            log.info("$ %s", " ".join(cmd))
            subprocess.run(cmd, cwd=str(repo_root), check=True)
            entries.append({
                "frame": f,
                "file": png.name,
                "label": (labels or {}).get(f, ""),
            })
    return entries


def capture_video_frames(video_path: str | Path, frames: list[int], out_dir: str | Path, *,
                         labels: dict[int, str] | None = None, fps: float = 30.0,
                         workers: int = 4) -> list[dict]:
    """완성 MP4에서 프레임을 추출한다. 60씬 QA에서 Remotion still 360회를 피한다.

    전 구간을 한 번만 디코드하고 ``select`` 필터로 필요한 프레임만 PNG로 기록한다.
    이전의 프레임별 seek는 360개 QA 지점에서 같은 GOP를 반복 디코드해 UHD 검사에
    큰 I/O 비용을 만들었다. 반환 manifest는 반드시 요청한 ``frames`` 순서를 유지한다.
    QA PNG는 contact sheet와 색·완료도 판정 전용의 최대 960px 프록시다. 최종본의
    UHD 규격은 별도 ffprobe/audit 검사로 검증한다. 임시 디렉터리에서 전부 성공한
    뒤에만 기존 QA 프레임을 교체한다.
    """
    video = Path(video_path).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if workers < 1:
        raise ValueError("workers must be at least 1")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not frames:
        return []
    unique_frames = sorted(set(frames))
    if unique_frames[0] < 0:
        raise ValueError("frame numbers must be non-negative")
    # 이전 프로세스가 강제 중단되어 남긴 임시 캡처는 납품 QA에 포함되면 안 된다.
    for stale in out_dir.glob(".qa-select-*"):
        shutil.rmtree(stale, ignore_errors=True)
    # 쉘을 통과하지 않는 subprocess 인수지만 ffmpeg filter parser의 쉼표는 이스케이프한다.
    # 60씬 × 3점처럼 선택 프레임이 많으면 하나의 select 식이 지나치게 길어져
    # 일부 ffmpeg 빌드에서 filter parser 메모리 오류가 난다. 고정 크기 배치로
    # 나눠도 완성 MP4만 읽고, 성공한 모든 배치가 모인 뒤에만 QA 프레임을 교체한다.
    select_batch_size = 48
    with tempfile.TemporaryDirectory(prefix=".qa-select-", dir=out_dir) as temp_name:
        temp_dir = Path(temp_name)
        captures: list[tuple[int, Path]] = []
        for batch_index, start in enumerate(range(0, len(unique_frames), select_batch_size)):
            batch = unique_frames[start:start + select_batch_size]
            select_expr = "select=" + "+".join(f"eq(n\\,{frame})" for frame in batch)
            video_filter = f"{select_expr},scale=w='min({QA_MAX_WIDTH}\\,iw)':h=-2"
            pattern = temp_dir / f"batch-{batch_index:03d}-%04d.png"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", str(workers),
                   "-i", str(video), "-vf", video_filter, "-vsync", "0", str(pattern)]
            log.info("$ %s", " ".join(cmd))
            subprocess.run(cmd, check=True)
            captured = sorted(temp_dir.glob(f"batch-{batch_index:03d}-*.png"))
            if len(captured) != len(batch):
                raise RuntimeError(
                    f"QA frame extraction mismatch: requested {len(batch)}, got {len(captured)}")
            captures.extend(zip(batch, captured))
        for frame, source in captures:
            destination = out_dir / f"frame-{frame:05d}.png"
            if destination.exists():
                destination.unlink()
            shutil.move(str(source), destination)

    return [{"frame": frame, "file": f"frame-{frame:05d}.png",
             "label": (labels or {}).get(frame, "")}
            for frame in frames]


def write_manifest(entries: list[dict], out_dir: str | Path, *, project_id: str = "",
                   props: str = "") -> Path:
    """capture-manifest.json 저장."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"projectId": project_id, "props": str(props), "captures": entries}
    path = out_dir / "capture-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def contact_sheet(out_dir: str | Path, out_path: str | Path | None = None, *,
                  cols: int = 4, thumb_w: int = 460) -> Path:
    """캡처 디렉토리의 manifest 기반 콘택트시트 PNG 생성."""
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / "capture-manifest.json").read_text(encoding="utf-8"))
    captures = manifest["captures"]
    if not captures:
        raise ValueError("캡처 항목이 없어 콘택트시트를 만들 수 없음")

    label_h = 34
    thumbs: list[tuple[Image.Image, str]] = []
    for c in captures:
        im = Image.open(out_dir / c["file"]).convert("RGB")
        th = max(1, int(im.height * thumb_w / im.width))
        text = f"f{c['frame']}" + (f" — {c['label']}" if c.get("label") else "")
        thumbs.append((im.resize((thumb_w, th), Image.LANCZOS), text))

    thumb_h = max(t.height for t, _ in thumbs)
    rows = (len(thumbs) + cols - 1) // cols
    pad = 12
    sheet = Image.new("RGB", (cols * (thumb_w + pad) + pad,
                              rows * (thumb_h + label_h + pad) + pad), (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    for i, (im, text) in enumerate(thumbs):
        x = pad + (i % cols) * (thumb_w + pad)
        y = pad + (i // cols) * (thumb_h + label_h + pad)
        sheet.paste(im, (x, y))
        d.text((x + 4, y + thumb_h + 8), text, fill=(230, 228, 220))

    out = Path(out_path) if out_path else out_dir / "contact-sheet.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


# ── 씬 갤러리 (QA 카드뷰) ──────────────────────────────────────────────

_GALLERY_KINDS = [("drawing", "드로잉"), ("subtitle", "자막"), ("title", "타이틀"),
                  ("widget", "위젯"), ("audio", "오디오")]


def _scene_cards(props: dict, captures: list[dict]) -> tuple[list[dict], list[str]]:
    """props 씬 + manifest 캡처(프레임 오프셋 매핑) → 카드 데이터 / 경고 목록."""
    warnings: list[str] = []
    scenes = props.get("scenes") or []
    if not scenes:
        warnings.append("props.json 에 씬이 없습니다 — 빌드/props 스테이지를 먼저 확인하세요.")
    cards = []
    offset = 0
    for i, s in enumerate(scenes):
        d = int(s.get("durationInFrames", 0))
        caps = [c for c in captures if offset <= int(c.get("frame", -1)) < offset + d]
        if not caps:
            warnings.append(f"{s.get('id', f'scene-{i + 1:02d}')}: 캡처 없음 — bin/qa.py 재실행 필요")
        cards.append({
            "sceneId": s.get("id", f"scene-{i + 1:02d}"),
            "offset": offset, "duration": d,
            "cues": s.get("cues") or [],
            "topTitle": s.get("topTitle"),
            "naturalEffects": s.get("naturalEffects"),
            "widgets": s.get("widgets") or [],
            "captures": caps,
        })
        offset += d
    return cards, warnings


def build_gallery(props_path: str | Path, qa_dir: str | Path,
                  out_path: str | Path | None = None) -> Path:
    """씬 갤러리 HTML 생성 — 카드(캡처·메타·수정 체크박스) + fix-request JSON 초안 복사.

    이미지 경로는 qa_dir 기준 상대경로만 사용한다 (폴더째 옮겨도 열림).
    """
    import html as html_mod

    qa_dir = Path(qa_dir)
    props = json.loads(Path(props_path).read_text(encoding="utf-8"))
    manifest_path = qa_dir / "capture-manifest.json"
    captures: list[dict] = []
    warnings: list[str] = []
    if manifest_path.is_file():
        captures = json.loads(manifest_path.read_text(encoding="utf-8")).get("captures", [])
    else:
        warnings.append("capture-manifest.json 이 없습니다 — bin/qa.py 로 캡처를 먼저 생성하세요.")

    cards, card_warnings = _scene_cards(props, captures)
    warnings += card_warnings
    for card in cards:
        for c in card["captures"]:
            if not (qa_dir / c["file"]).is_file():
                warnings.append(f"{card['sceneId']}: 캡처 파일 누락 — {c['file']}")

    project_id = props.get("projectId", "?")
    # Full Color Motion은 brush cursor를 선택적으로 쓰더라도 붓 리빌 제품으로
    # 오인하면 안 된다. 별도 props의 movement 필드로 gallery profile을 구분한다.
    profile = (
        "full-color-motion" if any("movement" in scene for scene in (props.get("scenes") or []))
        else ("pen" if (props.get("brush") or {}).get("kind") == "pen" else "brush")
    )

    def esc(s):
        return html_mod.escape(str(s), quote=True)

    kind_boxes = "".join(
        f'<label class="kind"><input type="checkbox" data-kind="{k}"> {label}</label>'
        for k, label in _GALLERY_KINDS)

    card_html = []
    for card in cards:
        cues = card["cues"]
        cue_sum = f"cue {len(cues)}개"
        if cues:
            cue_sum += f' — “{esc(cues[0]["text"])}”' + (f' … “{esc(cues[-1]["text"])}”' if len(cues) > 1 else "")
        badges = [f'<span class="badge profile">{profile}</span>']
        if card["topTitle"]:
            badges.append(f'<span class="badge">title: {esc(" / ".join(card["topTitle"].get("lines", [])))}</span>')
        if card["naturalEffects"]:
            badges.append(f'<span class="badge">fx: {esc(card["naturalEffects"].get("kind", "?"))}</span>')
        if card["widgets"]:
            types = {}
            for w in card["widgets"]:
                types[w.get("type", "?")] = types.get(w.get("type", "?"), 0) + 1
            badges.append('<span class="badge">widgets: '
                          + esc(", ".join(f"{t}×{n}" for t, n in types.items())) + "</span>")
        else:
            badges.append('<span class="badge dim">widgets 0</span>')
        thumbs = "".join(
            f'<figure><img src="{esc(c["file"])}" loading="lazy" alt="f{c["frame"]}" '
            f'onclick="openLightbox(this.src, \'{esc(c.get("label") or "")} (f{c["frame"]})\')">'
            f'<figcaption>f{c["frame"]} {esc(c.get("label") or "")}</figcaption></figure>'
            for c in card["captures"]) or '<p class="warn">캡처 없음</p>'
        rep_frame = card["captures"][0]["frame"] if card["captures"] else card["offset"] + 12
        card_html.append(f"""
  <section class="card" data-scene="{esc(card['sceneId'])}" data-frame="{rep_frame}">
    <header><h2>{esc(card['sceneId'])}</h2>
      <span class="meta">{card['duration']}f (f{card['offset']}~f{card['offset'] + card['duration'] - 1})</span></header>
    <div class="badges">{''.join(badges)}</div>
    <p class="cues">{cue_sum}</p>
    <div class="thumbs">{thumbs}</div>
    <div class="fix">{kind_boxes}
      <input type="text" class="memo" placeholder="문제/수정 메모 (fix-request problem 필드)">
    </div>
  </section>""")

    warn_html = "".join(f'<div class="warncard">⚠ {esc(w)}</div>' for w in warnings)
    html_text = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(project_id)} — 씬 갤러리 QA</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; padding: 24px; background: #17181b; color: #e8e6e1;
         font: 14px/1.5 -apple-system, "Apple SD Gothic Neo", sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #9a968e; margin-bottom: 16px; }}
  .toolbar {{ position: sticky; top: 0; background: #17181bee; padding: 10px 0; z-index: 5; }}
  button {{ background: #4f6df5; color: #fff; border: 0; border-radius: 8px;
            padding: 8px 14px; font-size: 14px; cursor: pointer; }}
  .warncard {{ background: #3a2d18; border: 1px solid #8a6d2f; border-radius: 8px;
               padding: 10px 14px; margin: 8px 0; }}
  .card {{ background: #202127; border: 1px solid #33343c; border-radius: 12px;
           padding: 16px; margin: 14px 0; }}
  .card header {{ display: flex; align-items: baseline; gap: 10px; }}
  .card h2 {{ font-size: 16px; margin: 0; }}
  .meta {{ color: #9a968e; font-size: 12px; }}
  .badge {{ display: inline-block; background: #2c2e36; border-radius: 999px;
            padding: 2px 10px; margin: 2px 4px 2px 0; font-size: 12px; }}
  .badge.profile {{ background: #4f6df5; }}
  .badge.dim {{ color: #77747d; }}
  .cues {{ color: #c9c5bd; margin: 6px 0; }}
  .thumbs {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .thumbs figure {{ margin: 0; }}
  .thumbs img {{ width: 300px; max-width: 100%; border-radius: 8px; cursor: zoom-in;
                 border: 1px solid #33343c; }}
  figcaption {{ font-size: 11px; color: #9a968e; margin-top: 2px; }}
  .fix {{ margin-top: 10px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
  .kind {{ user-select: none; }}
  .memo {{ flex: 1 1 260px; background: #17181b; color: #e8e6e1; border: 1px solid #33343c;
           border-radius: 8px; padding: 6px 10px; }}
  .warn {{ color: #e2b34c; }}
  dialog {{ border: 0; background: #000c; padding: 16px; border-radius: 12px; max-width: 92vw; }}
  dialog img {{ max-width: 88vw; max-height: 82vh; }}
  dialog p {{ color: #ddd; text-align: center; margin: 8px 0 0; }}
  pre {{ background: #101114; border: 1px solid #33343c; border-radius: 8px;
        padding: 12px; overflow-x: auto; white-space: pre-wrap; }}
</style></head><body>
<h1>{esc(project_id)} — 씬 갤러리 QA <span class="badge profile">{profile}</span></h1>
<p class="sub">씬 {len(cards)}개 · 캡처 {len(captures)}장 · 체크 후 아래 버튼으로 scene-fix-request JSON 초안을 복사하세요.</p>
<div class="toolbar"><button onclick="copyFixRequest()">선택 요약 → scene-fix-request JSON 초안 복사</button></div>
{warn_html}
{''.join(card_html)}
<h2>fix-request 미리보기</h2>
<pre id="preview">(체크박스 선택 후 버튼을 누르면 여기에 표시됩니다)</pre>
<dialog id="lightbox" onclick="this.close()"><img id="lightbox-img" src="" alt=""><p id="lightbox-cap"></p></dialog>
<script>
function openLightbox(src, cap) {{
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox-cap').textContent = cap;
  document.getElementById('lightbox').showModal();
}}
function buildFixRequest() {{
  const scenes = [];
  document.querySelectorAll('.card').forEach(card => {{
    const memo = card.querySelector('.memo').value.trim();
    const issues = [];
    card.querySelectorAll('input[data-kind]:checked').forEach(cb => {{
      issues.push({{ kind: cb.dataset.kind, severity: 'mid',
                    frame: parseInt(card.dataset.frame, 10),
                    problem: memo || '(메모 미작성)', fix: '' }});
    }});
    if (issues.length) scenes.push({{ sceneId: card.dataset.scene, issues }});
  }});
  return {{ projectId: {json.dumps(project_id, ensure_ascii=False)},
           reviewedAt: new Date().toISOString().slice(0, 10), scenes }};
}}
function copyFixRequest() {{
  const json = JSON.stringify(buildFixRequest(), null, 2);
  document.getElementById('preview').textContent = json;
  if (navigator.clipboard) navigator.clipboard.writeText(json);
}}
</script>
</body></html>
"""
    out = Path(out_path) if out_path else qa_dir / "gallery.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    log.info("gallery: 씬 %d개, 캡처 %d장, 경고 %d건 -> %s", len(cards), len(captures), len(warnings), out)
    return out
