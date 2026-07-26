#!/usr/bin/env python3
"""Harmony-aware, original solo piano BGM skill CLI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Run the repository source directly so a newly added independent skill does not
# depend on a stale installed wheel. The project venv remains preferred by users.
PIPELINE_SOURCE = str(REPO_ROOT / "pipeline")
if PIPELINE_SOURCE not in sys.path:
    sys.path.insert(0, PIPELINE_SOURCE)
from brushvid.piano_bgm import (OUTPUT_ROOT, PROJECTS_ROOT, PianoBgmError, PRESETS, approve_listening, media_tool,
                                compose, lint_score, load_request, load_score_bundle, normalize_request,
                                performance_from_score, qa_project, write_listening_review,
                                qa_generated_project, resolve_engine, write_request, write_score_bundle)
from brushvid.piano_render import render_to_wav, write_render_report
from brushvid.stable_audio import StableAudioError, generate as stable_audio_generate, preflight as stable_audio_preflight

DEFAULT_INSTRUMENT = REPO_ROOT / "local-assets" / "instruments" / "noct-salamander-grand-v6-1a"
DEFAULT_SFZ = DEFAULT_INSTRUMENT / "sfz_minimum" / "Noct-SalamanderGrandPiano_treble2.0db.Recommended.sfz"


def _output_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    return Path(args.projects_root).resolve(), Path(args.output_root).resolve()


def _request_for(args: argparse.Namespace) -> dict:
    request = load_request(args.request)
    engine_override = getattr(args, "engine", None)
    if engine_override:
        request = dict(request)
        request["engine"] = engine_override
        request = normalize_request(request)
    return request


def _runtime_engine(args: argparse.Namespace, request: dict) -> dict[str, str]:
    decision = resolve_engine(request)
    if decision["engine"] != "stable-audio-3-mlx":
        return decision
    try:
        stable_audio_preflight(getattr(args, "stable_audio_root", None))
    except StableAudioError as exc:
        if request.get("engine") == "auto":
            return {"engine": "sample-score", "reason": f"auto-fallback-stable-audio-unavailable: {exc}"}
        raise PianoBgmError(str(exc)) from exc
    return decision


def _ensure_stable_output_is_safe(output: Path, project_id: str, *, force: bool) -> None:
    if force:
        return
    existing = [path.name for path in (
        output / "source.wav", output / "raw-48k24.wav", output / "master-48k24.wav",
        output / f"{project_id}-44k16.wav", output / "generation.json",
    ) if path.exists()]
    if existing:
        raise PianoBgmError("Stable Audio 출력이 이미 있습니다. 덮어쓰려면 --force를 사용하세요: " + ", ".join(existing))


def _stable_generate(args: argparse.Namespace, request: dict) -> dict:
    projects_root, output_root = _output_roots(args)
    project_id = request["projectId"]
    project = projects_root / project_id
    output = output_root / project_id
    force = bool(getattr(args, "force", False))
    _ensure_stable_output_is_safe(output, project_id, force=force)
    write_request(project / "request.yaml", request)
    source = output / "source.wav"
    try:
        generation = stable_audio_generate(
            request,
            source,
            root=getattr(args, "stable_audio_root", None),
            force=force,
            timeout_sec=getattr(args, "timeout_sec", None),
        )
    except StableAudioError as exc:
        raise PianoBgmError(str(exc)) from exc
    generation_path = output / "generation.json"
    generation_path.write_text(json.dumps(generation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"projectId": project_id, "engine": "stable-audio-3-mlx", "source": str(source),
            "generation": str(generation_path), "sha256": generation["sha256"]}


def _compose(args: argparse.Namespace) -> dict:
    request = _request_for(args)
    if request.get("engine") == "stable-audio-3-mlx":
        raise PianoBgmError("compose는 sample-score 전용입니다. Stable Audio는 generate 또는 build를 사용하세요")
    projects_root, _ = _output_roots(args)
    score = compose(request)
    lint = lint_score(score)
    if lint["status"] != "PASS":
        raise PianoBgmError("music lint FAIL: " + json.dumps(lint["errors"], ensure_ascii=False))
    performance = performance_from_score(score)
    paths = write_score_bundle(request, score, performance, lint, projects_root=projects_root)
    return {"request": request, "paths": {key: str(value) for key, value in paths.items()}, "lint": lint}


def _render(args: argparse.Namespace) -> dict:
    projects_root, output_root = _output_roots(args)
    request, score, performance = load_score_bundle(args.project_id, projects_root=projects_root)
    lint = lint_score(score)
    if lint["status"] != "PASS":
        raise PianoBgmError("music lint FAIL: " + json.dumps(lint["errors"], ensure_ascii=False))
    output = output_root / args.project_id
    output.mkdir(parents=True, exist_ok=True)
    instrument = Path(args.instrument_root).resolve()
    sfz = Path(args.sfz).resolve()
    raw = output / "raw-48k24.wav"
    report = render_to_wav(performance, instrument_root=instrument, sfz=sfz, output=raw)
    write_render_report(output / "render-report.json", report)
    master = output / "master-48k24.wav"
    delivery = output / f"{args.project_id}-44k16.wav"
    ffmpeg = media_tool("ffmpeg")
    try:
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-af",
                        "loudnorm=I=-20:LRA=7:TP=-1.5", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(master)], check=True)
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(master), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(delivery)], check=True)
    except subprocess.CalledProcessError as exc:
        raise PianoBgmError("master WAV export 실패") from exc
    preview = output / "preview-30s-44k16.wav"
    if request["output"].get("preview", True):
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(delivery), "-t", "30", "-c:a", "pcm_s16le", str(preview)], check=True)
    return {"projectId": args.project_id, "raw": str(raw), "master": str(master), "delivery": str(delivery), "report": report}


def _build(args: argparse.Namespace) -> dict:
    request = _request_for(args)
    decision = _runtime_engine(args, request)
    if decision["engine"] == "stable-audio-3-mlx":
        return _stable_build(args, request, decision)
    return _sample_build(args, decision)


def _sample_build(args: argparse.Namespace, engine_selection: dict[str, str] | None = None) -> dict:
    composed = _compose(args)
    request = composed["request"]
    args.project_id = request["projectId"]
    rendered = _render(args)
    projects_root, output_root = _output_roots(args)
    qa = qa_project(args.project_id, projects_root=projects_root, output_root=output_root,
                    engine_selection=engine_selection)
    return {"compose": composed, "render": rendered, "qa": qa}


def _stable_build(args: argparse.Namespace, request: dict, decision: dict[str, str]) -> dict:
    generated = _stable_generate(args, request)
    _, output_root = _output_roots(args)
    output = output_root / request["projectId"]
    ffmpeg = media_tool("ffmpeg")
    source = output / "source.wav"
    raw = output / "raw-48k24.wav"
    master = output / "master-48k24.wav"
    delivery = output / f"{request['projectId']}-44k16.wav"
    try:
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(raw)], check=True)
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-af",
                        "loudnorm=I=-23:LRA=7:TP=-1.0", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(master)], check=True)
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(master),
                        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(delivery)], check=True)
        preview = output / "preview-30s-44k16.wav"
        if request["output"].get("preview", True):
            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(delivery),
                            "-t", "30", "-c:a", "pcm_s16le", str(preview)], check=True)
    except subprocess.CalledProcessError as exc:
        raise PianoBgmError("Stable Audio master WAV export 실패") from exc
    projects_root, output_root = _output_roots(args)
    qa = qa_generated_project(request["projectId"], projects_root=projects_root, output_root=output_root)
    return {"request": request, "engine": decision, "generate": generated,
            "raw": str(raw), "master": str(master), "delivery": str(delivery), "qa": qa}


def _generate(args: argparse.Namespace) -> dict:
    request = _request_for(args)
    decision = _runtime_engine(args, request)
    if decision["engine"] == "stable-audio-3-mlx":
        result = _stable_generate(args, request)
        result["engine"] = decision
        return result
    composed = _compose(args)
    args.project_id = request["projectId"]
    rendered = _render(args)
    return {"engine": decision, "compose": composed, "render": rendered}


def _qa(args: argparse.Namespace) -> dict:
    projects_root, output_root = _output_roots(args)
    stable_marker = output_root / args.project_id / "generation.json"
    if stable_marker.is_file():
        return qa_generated_project(args.project_id, projects_root=projects_root, output_root=output_root)
    return qa_project(args.project_id, projects_root=projects_root, output_root=output_root)


def _lint(args: argparse.Namespace) -> dict:
    request = load_request(Path(args.projects_root) / args.project_id / "request.yaml")
    stable_marker = _output_roots(args)[1] / args.project_id / "generation.json"
    if request.get("engine") == "stable-audio-3-mlx" or stable_marker.is_file():
        raise PianoBgmError("Stable Audio 산출물에는 symbolic score lint를 적용할 수 없습니다")
    return lint_score(load_score_bundle(args.project_id, projects_root=_output_roots(args)[0])[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", default=str(PROJECTS_ROOT))
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT))
    parser.add_argument("--stable-audio-root", default=None,
                        help="Stable Audio MLX 설치 루트; 미지정 시 STABLE_AUDIO_3_MLX_ROOT 사용")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="request schema/key/preset 검증")
    validate.add_argument("--request", required=True)
    validate.add_argument("--engine", choices=("auto", "stable-audio-3-mlx", "sample-score"))
    validate.set_defaults(func=lambda args: {"request": _request_for(args), "presets": sorted(PRESETS)})
    compose_cmd = sub.add_parser("compose", help="request -> harmony-aware score/performance/lint")
    compose_cmd.add_argument("--request", required=True)
    compose_cmd.add_argument("--engine", choices=("auto", "stable-audio-3-mlx", "sample-score"))
    compose_cmd.set_defaults(func=_compose)
    render = sub.add_parser("render", help="PASS score -> local SFZ sample raw/master/delivery WAV")
    render.add_argument("--project-id", required=True)
    render.add_argument("--instrument-root", default=str(DEFAULT_INSTRUMENT))
    render.add_argument("--sfz", default=str(DEFAULT_SFZ))
    render.set_defaults(func=_render)
    build = sub.add_parser("build", help="validate + compose + render + technical QA")
    build.add_argument("--request", required=True)
    build.add_argument("--project-id", default="")
    build.add_argument("--instrument-root", default=str(DEFAULT_INSTRUMENT))
    build.add_argument("--sfz", default=str(DEFAULT_SFZ))
    build.add_argument("--engine", choices=("auto", "stable-audio-3-mlx", "sample-score"))
    build.add_argument("--force", action="store_true", help="Stable Audio 후보 산출물을 덮어씀")
    build.add_argument("--timeout-sec", type=float, default=None)
    build.set_defaults(func=_build)
    generate = sub.add_parser("generate", help="request -> Stable Audio source 또는 sample-score candidate")
    generate.add_argument("--request", required=True)
    generate.add_argument("--engine", choices=("auto", "stable-audio-3-mlx", "sample-score"))
    generate.add_argument("--force", action="store_true", help="Stable Audio source를 덮어씀")
    generate.add_argument("--timeout-sec", type=float, default=None)
    generate.add_argument("--instrument-root", default=str(DEFAULT_INSTRUMENT))
    generate.add_argument("--sfz", default=str(DEFAULT_SFZ))
    generate.set_defaults(func=_generate)
    lint = sub.add_parser("lint", help="saved score의 음악 lint")
    lint.add_argument("--project-id", required=True)
    lint.set_defaults(func=_lint)
    qa = sub.add_parser("qa", help="format/loudness/provenance QA; human listening remains pending")
    qa.add_argument("--project-id", required=True)
    qa.set_defaults(func=_qa)
    review = sub.add_parser("review", help="HTML + JSON people-listening review template")
    review.add_argument("--project-id", required=True)
    review.set_defaults(func=lambda args: {"review": str(write_listening_review(args.project_id, output_root=_output_roots(args)[1]))})
    approve = sub.add_parser("approve", help="two-environment listening approval JSON import")
    approve.add_argument("--project-id", required=True)
    approve.add_argument("--review-result", required=True)
    approve.set_defaults(func=lambda args: approve_listening(args.project_id, args.review_result, output_root=_output_roots(args)[1]))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(args.func(args), ensure_ascii=False, indent=2))
        return 0
    except PianoBgmError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
