"""bin/build.py 스테이지 캐시/스킵/재개 로직 단위 테스트."""
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from brushvid.project import BgmConfig, MotionConfig, MotionSceneConfig

BUILD_PY = Path(__file__).resolve().parents[2] / "bin" / "build.py"

spec = importlib.util.spec_from_file_location("buildmod", BUILD_PY)
buildmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(buildmod)


@pytest.fixture()
def ledger(tmp_path):
    return buildmod.StageLedger(tmp_path / "stages")


def test_ledger_mark_and_payload(ledger):
    assert not ledger.is_done("cues")
    ledger.mark_done("cues", {"sceneCount": 2})
    assert ledger.is_done("cues")
    assert ledger.payload("cues")["sceneCount"] == 2


def test_ledger_invalidate_from(ledger):
    for s in buildmod.STAGES:
        ledger.mark_done(s)
    ledger.invalidate_from("render")
    assert ledger.is_done("props")          # 앞 스테이지 유지
    for s in ("render", "mux", "qa"):       # 해당+이후 무효화
        assert not ledger.is_done(s)


def test_ledger_invalid_stage_rejected(ledger):
    with pytest.raises(ValueError, match="--from"):
        ledger.invalidate_from("renderr")


def test_resolved_audit_artifacts_use_actual_mix_payload(tmp_path):
    data_dir = tmp_path / "data" / "auto-bgm"
    license_path = data_dir / "licenses" / "bgm-manifest.json"
    report_path = data_dir / "audio" / "mix-report.json"
    license_path.parent.mkdir(parents=True)
    report_path.parent.mkdir(parents=True)
    license_path.write_text("{}", encoding="utf-8")
    report_path.write_text("{}", encoding="utf-8")
    led = buildmod.StageLedger(data_dir / "stages")
    led.mark_done("mix", {"mode": "asset", "autoSelected": True,
                          "report": str(report_path), "assetIds": ["auto-id"]})
    pipe = SimpleNamespace(data_dir=data_dir, ledger=led)
    assert buildmod.resolved_audit_artifacts(pipe) == (license_path, report_path)


def test_resolved_audit_artifacts_ignore_stale_license_when_bgm_is_off(tmp_path):
    data_dir = tmp_path / "data" / "voice-only"
    license_path = data_dir / "licenses" / "bgm-manifest.json"
    report_path = data_dir / "audio" / "mix-report.json"
    license_path.parent.mkdir(parents=True)
    report_path.parent.mkdir(parents=True)
    license_path.write_text("{}", encoding="utf-8")
    report_path.write_text("{}", encoding="utf-8")
    led = buildmod.StageLedger(data_dir / "stages")
    led.mark_done("mix", {"mode": "off", "report": str(report_path)})
    pipe = SimpleNamespace(data_dir=data_dir, ledger=led)
    assert buildmod.resolved_audit_artifacts(pipe) == (None, report_path)


def test_generated_piano_manifest_final_gate_requires_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="final-piano", bgm=BgmConfig(mode="piano-auto"))
    manifest = tmp_path / "output" / "original-audio" / "piano-bgm" / cfg.project_id / "generated-bgm-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "kind": "generated-piano-bgm", "status": "PENDING_USER_LISTENING",
    }), encoding="utf-8")
    assert buildmod._generated_piano_manifest_approved(cfg) is False
    manifest.write_text(json.dumps({
        "kind": "generated-piano-bgm", "status": "APPROVED",
    }), encoding="utf-8")
    assert buildmod._generated_piano_manifest_approved(cfg) is True


def test_bgm_off_normalizes_voice_and_writes_voice_only_report(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    voice = tmp_path / "voice.wav"
    cfg = buildmod.ProjectConfig(
        project_id="voice-only", audio=voice, srt=tmp_path / "voice.srt",
        bgm=BgmConfig(mode="off"),
    )
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300}])
    captured = {}

    def normalize(src, out, *, duration_sec):
        captured["src"] = src
        captured["duration"] = duration_sec
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"voice-master")
        return Path(out), {"integratedLufs": -24.0}

    monkeypatch.setattr(buildmod.bv_mix, "normalize_voice", normalize)
    monkeypatch.setattr(buildmod.bv_mix, "measure_loudness",
                        lambda *_a, **_k: {"integratedLufs": -16.0})
    result = pipe.stage_mix()
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert result["mode"] == "off"
    assert Path(result["audio"]).read_bytes() == b"voice-master"
    assert captured == {"src": voice, "duration": 10.0}
    assert report["bgm"] is None
    assert report["voice"]["ducking"] == {"enabled": False, "reason": "bgm-off"}


def test_bgm_off_without_voice_creates_intentional_silent_audio_report(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="silent", bgm=BgmConfig(mode="off"))
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300}])

    def create_silence(out, duration_sec):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"silent")
        assert duration_sec == 10.0
        return Path(out)

    monkeypatch.setattr(buildmod.bv_audio, "create_silent_audio", create_silence)
    result = pipe.stage_mix()
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert result["mode"] == "off" and result["silentAudioTrack"] is True
    assert Path(result["audio"]).read_bytes() == b"silent"
    assert report["settings"] == {"bgmEnabled": False, "silentAudioTrack": True}


def test_standard_youtube_brush_props_use_fitted_no_pulse_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="brush-no-pulse", drawing_profile="brush",
                                 fmt="youtube", ambient_scenes=1)
    pipe = buildmod.Pipeline(cfg)
    pipe.data_dir.mkdir(parents=True, exist_ok=True)
    (pipe.public_dir / "routes").mkdir(parents=True)
    pipe.scenes_json.write_text(json.dumps([{"durationInFrames": 300, "cues": []}]), encoding="utf-8")
    (pipe.public_dir / "routes" / "scene-01.routes.json").write_text(json.dumps({
        "meta": {"drawStart": 8, "drawEnd": 219, "penInvisibleAfter": 228},
        "strokes": [{"end": 219}],
    }), encoding="utf-8")
    payload = pipe.stage_props()
    scene = json.loads(pipe.props_json.read_text(encoding="utf-8"))["scenes"][0]
    assert scene["completionMode"] == "integrated-develop"
    assert scene["prewashImage"] == "brush-no-pulse/bg/scene-01.png"
    assert scene["prewashOpacity"] == 0.5
    assert scene["prewashFrames"] == 12
    assert scene["prewashFadeOutFrames"] == 12
    assert scene["prewashBlur"] == 12
    assert scene["outroWashOpacity"] == 1.0
    assert scene["developFrames"] == 30
    assert scene["colorSettleFrames"] == 15
    assert scene["naturalEffects"] == {
        "kind": "mist", "opacity": 0, "parallaxScale": 1.012, "seed": 1,
    }
    assert payload["completionTimings"][0]["colorSettleEnd"] + 12 <= 276


def test_overlay_free_prompt_film_suppresses_title_and_captions(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="overlay-free", drawing_profile="brush",
                                 fmt="youtube", ambient_scenes=1,
                                 title="원본을 가리면 안 되는 제목", overlays="none")
    pipe = buildmod.Pipeline(cfg)
    pipe.data_dir.mkdir(parents=True, exist_ok=True)
    (pipe.public_dir / "routes").mkdir(parents=True)
    pipe.scenes_json.write_text(json.dumps([{
        "durationInFrames": 300,
        "cues": [{"from": 0, "to": 10, "text": "QA cue only"}],
    }]), encoding="utf-8")
    (pipe.public_dir / "routes" / "scene-01.routes.json").write_text(json.dumps({
        "meta": {"drawStart": 8, "drawEnd": 219, "penInvisibleAfter": 228},
        "strokes": [{"end": 219}],
    }), encoding="utf-8")
    pipe.stage_props()
    scene = json.loads(pipe.props_json.read_text(encoding="utf-8"))["scenes"][0]
    assert scene["captionsVisible"] is False
    assert "topTitle" not in scene


def test_uhd_canvas_and_composition_are_selected_for_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="uhd-demo", fmt="youtube", render_resolution="uhd")
    pipe = buildmod.Pipeline(cfg)
    assert pipe.canvas == (3840, 2160)
    assert buildmod.resolve_composition(cfg) == "BrushLandscape4K"


def test_full_color_motion_selects_its_own_fhd_uhd_and_portrait_compositions(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    motion = MotionConfig()
    assert buildmod.resolve_composition(buildmod.ProjectConfig(
        project_id="motion-fhd", drawing_profile="full-color-motion", motion=motion,
    )) == "FullColorMotionLandscape"
    assert buildmod.resolve_composition(buildmod.ProjectConfig(
        project_id="motion-uhd", drawing_profile="full-color-motion", motion=motion,
        render_resolution="uhd",
    )) == "FullColorMotionLandscape4K"
    assert buildmod.resolve_composition(buildmod.ProjectConfig(
        project_id="motion-shorts", drawing_profile="full-color-motion", motion=motion,
        fmt="shorts",
    )) == "FullColorMotionPortrait"


def test_uhd_long_render_uses_internal_four_worker_limit(tmp_path, monkeypatch):
    """프로젝트 간 병렬 렌더 없이 UHD 100초 청크를 4개 워커로 처리한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="uhd-workers", fmt="youtube", render_resolution="uhd")
    pipe = buildmod.Pipeline(cfg)
    pipe.data_dir.mkdir(parents=True, exist_ok=True)
    pipe._write_scenes([{"durationInFrames": 300, "cues": []} for _ in range(60)])
    pipe.props_json.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pipe, "_render_input_signature", lambda: "unit-signature")
    captured = {}

    def fake_render_segments(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(buildmod.bv_render, "render_segments", fake_render_segments)
    result = pipe.stage_render()
    assert captured["concurrency"] == 4
    assert result["segmentCount"] == 6


def test_full_bleed_profile_skips_route_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="full-bleed-demo", drawing_profile="storybook-full-bleed")
    pipe = buildmod.Pipeline(cfg)
    assert pipe.stage_routes()["skippedReason"] == "storybook-full-bleed"


def test_full_color_motion_generates_only_brush_routes_at_staticfile_path(tmp_path, monkeypatch):
    """projectId를 public/<id> 아래에 두 번 붙이면 renderer가 routes 404를 낸다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(
        project_id="motion-demo", drawing_profile="full-color-motion",
        motion=MotionConfig(scenes=(
            MotionSceneConfig(reveal="brush", reveal_frames=90),
            MotionSceneConfig(reveal="none"),
        )),
    )
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([
        {"durationInFrames": 300, "cues": []},
        {"durationInFrames": 300, "cues": []},
    ])
    captured = []

    def fake_routes(_source, params, *, image_rel):
        captured.append((params.draw_start, params.draw_end, image_rel))
        return {"meta": {"coverage": 0.98}, "strokes": [
            {"id": "s1", "kind": "contour", "width": 44, "start": 0, "end": 90,
             "points": [[0, 0], [100, 100]]},
        ]}

    monkeypatch.setattr(buildmod, "generate_routes", fake_routes)
    result = pipe.stage_routes()
    route = tmp_path / "public" / "motion-demo" / "routes" / "scene-01.motion-reveal.routes.json"
    assert route.is_file()
    assert not (tmp_path / "public" / "motion-demo" / "motion-demo").exists()
    assert captured == [(0, 90, "motion-demo/bg/scene-01.png")]
    assert result["brushRevealScenes"][0]["routes"] == "routes/scene-01.motion-reveal.routes.json"
def test_pen_first_scene_props_use_a_blurred_source_poster(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="pen-opening", drawing_profile="pen",
                                 fmt="youtube", ambient_scenes=1)
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300, "cues": []}])
    pipe.stage_props()
    scene = json.loads(pipe.props_json.read_text(encoding="utf-8"))["scenes"][0]
    assert scene["prewashImage"] == "pen-opening/bg/scene-01.png"
    assert scene["prewashOpacity"] == 0.35
    assert scene["prewashFrames"] == 12
    assert scene["prewashFadeOutFrames"] == 12
    assert scene["prewashBlur"] == 10


def test_blurred_opening_poster_scope_excludes_shorts_hook_and_preserve_source(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    brush = buildmod.Pipeline(buildmod.ProjectConfig(project_id="brush", drawing_profile="brush"))
    assert brush._uses_first_scene_blurred_poster(0) is True
    assert brush._uses_first_scene_blurred_poster(1) is False

    shorts = buildmod.Pipeline(buildmod.ProjectConfig(
        project_id="shorts", drawing_profile="brush", fmt="shorts", ambient_scenes=3,
    ))
    assert shorts._uses_first_scene_blurred_poster(0) is False

    preserve = buildmod.Pipeline(buildmod.ProjectConfig(
        project_id="pen-preserve", drawing_profile="pen", drawing_preserve_source=True,
    ))
    assert preserve._uses_first_scene_blurred_poster(0) is False


def test_first_scene_brush_route_starts_when_the_blurred_poster_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="brush-opening", drawing_profile="brush",
                                 fmt="youtube", ambient_scenes=1)
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300, "cues": []}])
    captured = {}

    def fake_routes(_image, params, *, image_rel):
        captured["drawStart"] = params.draw_start
        captured["image"] = image_rel
        return {"meta": {"coverage": 1.0}}

    monkeypatch.setattr(buildmod, "generate_routes", fake_routes)
    monkeypatch.setattr(buildmod, "write_routes", lambda *_args, **_kwargs: None)
    pipe.stage_routes()
    assert captured == {
        "drawStart": 0,
        "image": "brush-opening/bg/scene-01-content.png",
    }


def _stub_pipeline(tmp_path, monkeypatch, calls):
    """무거운 스테이지를 기록용 스텁으로 바꾼 Pipeline."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="unit-demo")
    pipe = buildmod.Pipeline(cfg)
    for s in buildmod.STAGES:
        monkeypatch.setattr(pipe, f"stage_{s}", lambda s=s: calls.append(s) or {})
    return pipe


def test_pipeline_runs_then_skips(tmp_path, monkeypatch, caplog):
    """1회차 전 스테이지 실행 → 2회차 전부 캐시 스킵([skip] 로그)."""
    calls: list[str] = []
    pipe = _stub_pipeline(tmp_path, monkeypatch, calls)
    with caplog.at_level(logging.INFO, logger="build"):
        pipe.run()
        assert calls == buildmod.STAGES
        calls.clear()
        pipe.run()
    assert calls == []
    skips = [r.message for r in caplog.records if r.message.startswith("[skip]")]
    assert len(skips) == len(buildmod.STAGES)


def test_pipeline_from_stage_resumes(tmp_path, monkeypatch, caplog):
    """--from render: 앞 스테이지 스킵, render 이후만 재실행."""
    calls: list[str] = []
    pipe = _stub_pipeline(tmp_path, monkeypatch, calls)
    pipe.run()
    calls.clear()
    with caplog.at_level(logging.INFO, logger="build"):
        pipe.run(from_stage="render")
    assert calls == ["render", "mix", "mux", "qa"]
    skipped = [r.message.split()[1] for r in caplog.records if r.message.startswith("[skip]")]
    assert skipped == buildmod.STAGES[:buildmod.STAGES.index("render")]


def test_pipeline_from_mix_never_rerenders_video(tmp_path, monkeypatch):
    calls: list[str] = []
    pipe = _stub_pipeline(tmp_path, monkeypatch, calls)
    pipe.run()
    calls.clear()
    pipe.run(from_stage="mix")
    assert calls == ["mix", "mux", "qa"]


def test_user_image_content_change_invalidates_background_cache(tmp_path, monkeypatch):
    """같은 경로의 원본 이미지가 교체되면 background부터 자동 재실행한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(b"image-v1")
    cfg = buildmod.ProjectConfig(
        project_id="user-image-cache", bg_strategy="user-images", bg_images=[source]
    )
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 30, "cues": []}])
    bg = pipe.public_dir / "bg" / "scene-01.png"
    bg.parent.mkdir(parents=True)
    bg.write_bytes(b"rendered-v1")
    for stage in buildmod.STAGES:
        payload = {"signature": pipe._background_signature()} if stage == "background" else {}
        pipe.ledger.mark_done(stage, payload)

    calls: list[str] = []
    for stage in buildmod.STAGES:
        monkeypatch.setattr(pipe, f"stage_{stage}", lambda stage=stage: calls.append(stage) or {})

    source.write_bytes(b"image-v2")
    pipe.run()
    assert calls == buildmod.STAGES[buildmod.STAGES.index("background"):]


def test_user_images_are_mapped_one_per_scene_in_order(tmp_path, monkeypatch):
    """background.images N장은 매 씬에 N장 몽타주가 아니라 순서대로 한 장씩 연결된다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    red, blue = tmp_path / "red.png", tmp_path / "blue.png"
    Image.new("RGB", (32, 18), (240, 20, 20)).save(red)
    Image.new("RGB", (32, 18), (20, 40, 240)).save(blue)
    cfg = buildmod.ProjectConfig(
        project_id="scene-image-order", bg_strategy="user-images",
        bg_images=[red, blue], bg_fit="cover",
    )
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 30}, {"durationInFrames": 30}])
    pipe.stage_background()
    assert Image.open(pipe.public_dir / "bg" / "scene-01.png").getpixel((960, 540)) == (240, 20, 20)
    assert Image.open(pipe.public_dir / "bg" / "scene-02.png").getpixel((960, 540)) == (20, 40, 240)


def test_render_input_signature_changes_with_routes(tmp_path, monkeypatch):
    """길이가 같은 과거 청크도 routes 입력이 바뀌면 재사용 디렉터리를 분리한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    pipe = buildmod.Pipeline(buildmod.ProjectConfig(project_id="render-signature"))
    pipe.data_dir.mkdir(parents=True, exist_ok=True)
    pipe.props_json.write_text('{"projectId":"render-signature"}', encoding="utf-8")
    routes = pipe.public_dir / "routes"
    routes.mkdir(parents=True)
    route = routes / "scene-01.routes.json"
    pipe.ledger.mark_done("background", {"signature": "background-a"})
    route.write_text('{"strokes":[1]}', encoding="utf-8")
    first = pipe._render_input_signature()
    route.write_text('{"strokes":[2]}', encoding="utf-8")
    assert pipe._render_input_signature() != first


@pytest.mark.parametrize("kind", ["ambient", "narration", "whisper", "tts"])
def test_synth_bgm_keeps_voice_bus_separate_in_every_input_mode(tmp_path, monkeypatch, kind):
    """BGM은 어떤 입력 모드에서도 input.audio/TTS 경로를 대체하지 않는다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    voice = tmp_path / "voice.wav"
    srt = tmp_path / "voice.srt"
    script = tmp_path / "script.txt"
    common = {"project_id": f"mix-{kind}", "bgm": BgmConfig(mode="synth")}
    if kind == "narration":
        cfg = buildmod.ProjectConfig(**common, audio=voice, srt=srt)
    elif kind == "whisper":
        cfg = buildmod.ProjectConfig(**common, audio=voice)
    elif kind == "tts":
        cfg = buildmod.ProjectConfig(
            **common, script=script,
            tts={"engine": "supertonic", "voice": "F1", "pauseMs": 300, "timing": "tts"},
        )
    else:
        cfg = buildmod.ProjectConfig(**common)
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 30}])

    captured = {"voice": None, "bgm": None}

    def synth(path, *_args, **_kwargs):
        Path(path).write_bytes(b"synth")
        return Path(path)

    def prepare(paths, out, **_kwargs):
        captured["bgm"] = str(paths[0])
        Path(out).write_bytes(b"bgm")
        return Path(out), {"kind": "asset", "tracks": [{}], "output": {}}

    def mix(voice_path, _bgm_path, out, **_kwargs):
        captured["voice"] = str(voice_path)
        Path(out).write_bytes(b"master")
        return Path(out), {"ducking": {"enabled": True}}

    def copy(_src, out):
        Path(out).write_bytes(b"master")
        return Path(out)

    monkeypatch.setattr(buildmod.bv_audio, "synth_ambient_bgm", synth)
    monkeypatch.setattr(buildmod.bv_mix, "prepare_bgm", prepare)
    monkeypatch.setattr(buildmod.bv_mix, "mix_voice_and_bgm", mix)
    monkeypatch.setattr(buildmod.bv_mix, "copy_master", copy)
    monkeypatch.setattr(buildmod.bv_mix, "write_mix_report",
                        lambda out, _report: Path(out))

    result = pipe.stage_mix()
    assert result["mode"] == "synth"
    assert captured["bgm"].endswith("synth-raw.wav")
    if kind == "ambient":
        assert captured["voice"] is None
    elif kind == "tts":
        assert captured["voice"].endswith("data/mix-tts/tts/narration.wav")
    else:
        assert captured["voice"] == str(voice)


@pytest.mark.parametrize("kind", ["narration", "whisper", "tts"])
def test_legacy_projects_without_bgm_return_original_voice_path_unchanged(tmp_path, monkeypatch, kind):
    """bgm 블록이 없는 기존 음성 프로젝트는 복사·정규화 없이 원본 bus를 그대로 mux한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    voice, srt, script = tmp_path / "voice.wav", tmp_path / "voice.srt", tmp_path / "script.txt"
    if kind == "narration":
        cfg = buildmod.ProjectConfig(project_id="legacy-narration", audio=voice, srt=srt)
        expected = voice
    elif kind == "whisper":
        cfg = buildmod.ProjectConfig(project_id="legacy-whisper", audio=voice)
        expected = voice
    else:
        cfg = buildmod.ProjectConfig(
            project_id="legacy-tts", script=script,
            tts={"engine": "supertonic", "voice": "F1", "pauseMs": 300, "timing": "tts"},
        )
        expected = tmp_path / "data" / "legacy-tts" / "tts" / "narration.wav"
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 30}])
    result = pipe.stage_mix()
    assert result["mode"] == "legacy-voice"
    assert result["audio"] == str(expected)


def test_select_auto_bgm_policy_is_deterministic():
    """YouTube/Shorts 자동 BGM은 Pixabay를 제외하고 결정적으로 고른다."""
    from brushvid import bgm as bv_bgm
    assert bv_bgm.select_auto_bgm(profile="brush", fmt="youtube", duration_sec=300).asset_id \
        == "youtube-chris-zabriskie-fight-for-your-honor"
    assert bv_bgm.select_auto_bgm(profile="pen-brush", fmt="youtube", duration_sec=300).asset_id \
        == "youtube-jesse-gallagher-satya-yuga"
    assert bv_bgm.select_auto_bgm(profile="brush", fmt="shorts", duration_sec=30).asset_id \
        == "youtube-jesse-gallagher-satya-yuga"
    long_cfg = bv_bgm.select_auto_bgm(profile="brush", fmt="youtube", duration_sec=900)
    assert long_cfg.mode == "playlist" and 2 <= len(long_cfg.asset_ids) <= 3
    assert all(not asset_id.startswith("pixabay-") for asset_id in long_cfg.asset_ids)
    # 미지정 프로파일은 brush 기본으로 폴백
    assert bv_bgm.select_auto_bgm(profile="unknown", fmt="youtube", duration_sec=10).asset_id \
        == "youtube-chris-zabriskie-fight-for-your-honor"
    # Pixabay 기본값은 비공개 내부 preview 호환에서만 남긴다.
    assert bv_bgm.select_auto_bgm(profile="brush", fmt="preview", duration_sec=10).asset_id \
        == "pixabay-gentle-piano-meditation"


def _tts_project(tmp_path, *, voice="female-09", speed=1.10, timing="tts"):
    script = tmp_path / "narration.txt"
    script.write_text("전문 해설입니다.", encoding="utf-8")
    return buildmod.ProjectConfig(
        project_id="voice-pack-build",
        script=script,
        tts={
            "engine": "supertonic", "voice": voice, "speed": speed,
            "pauseMs": 350, "timing": timing,
        },
    )


def test_tts_signature_changes_with_voice_speed_and_text(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = _tts_project(tmp_path)
    pipe = buildmod.Pipeline(cfg)
    base = pipe._tts_signature()
    cfg.tts["speed"] = 1.20
    assert pipe._tts_signature() != base
    cfg.tts["speed"] = 1.10
    cfg.tts["voice"] = "female-08"
    assert pipe._tts_signature() != base
    cfg.tts["voice"] = "female-09"
    cfg.script.write_text("대본이 바뀌었습니다.", encoding="utf-8")
    assert pipe._tts_signature() != base


def test_changed_tts_signature_invalidates_stt_and_downstream(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = _tts_project(tmp_path)
    pipe = buildmod.Pipeline(cfg)
    tts_dir = pipe.data_dir / "tts"
    tts_dir.mkdir(parents=True)
    for name in ("narration.wav", "narration.srt", "voice-manifest.json"):
        (tts_dir / name).write_bytes(b"baseline")
    for stage in buildmod.STAGES:
        payload = {"signature": pipe._tts_signature()} if stage == "stt" else {}
        pipe.ledger.mark_done(stage, payload)
    cfg.tts["speed"] = 1.20
    calls = []
    for stage in buildmod.STAGES:
        monkeypatch.setattr(pipe, f"stage_{stage}", lambda stage=stage: calls.append(stage) or {})
    pipe.run()
    assert calls == buildmod.STAGES


def test_stage_stt_writes_resolved_voice_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = _tts_project(tmp_path)
    pipe = buildmod.Pipeline(cfg)

    def fake_synthesize(_text, out_wav, out_srt, **kwargs):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        Path(out_wav).write_bytes(b"wav")
        Path(out_srt).write_text("1\n00:00:00,000 --> 00:00:01,000\n문장\n", encoding="utf-8")
        assert kwargs["voice"] == "female-09"
        assert kwargs["speed"] == pytest.approx(1.10)
        return {
            "wav": str(out_wav), "srt": str(out_srt), "durationSec": 1.0,
            "entries": [{"text": "문장"}],
            "voice": {
                "requestedVoice": "female-09", "voicePresetId": "female-09",
                "voicePackVersion": "1.0.0", "engine": "supertonic",
                "packageVersion": "1.3.1", "model": "supertonic-3",
                "language": "ko", "sampleRate": 44100, "speed": 1.10,
                "components": {"F4": 0.65, "F1": 0.35},
                "catalogSha256": "a" * 64, "styleSourceSha256": {"F4": "b" * 64, "F1": "c" * 64},
                "styleSourceKind": "style-json-sha256", "styleSha256": "d" * 64,
                "aiDisclosure": "AI 합성 음성", "license": {"model": "OpenRAIL-M"},
            },
        }

    monkeypatch.setattr(buildmod.bv_tts, "synthesize_narration", fake_synthesize)
    payload = pipe.stage_stt()
    manifest_path = pipe.data_dir / "tts" / "voice-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["voiceManifest"] == str(manifest_path)
    assert payload["signature"] == pipe._tts_signature()
    assert manifest["voicePresetId"] == "female-09"
    assert manifest["components"] == {"F4": 0.65, "F1": 0.35}
    assert manifest["pauseMs"] == 350
    assert manifest["sentenceCount"] == 1


def test_stage_stt_writes_schema_valid_melo_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    script = tmp_path / "narration.txt"
    script.write_text("한국어 문장입니다.", encoding="utf-8")
    cfg = buildmod.ProjectConfig(
        project_id="melo-manifest", script=script,
        tts={"engine": "melo-ko", "voice": "kr-default", "language": "ko",
             "speed": 1.0, "pauseMs": 300, "timing": "tts"},
    )
    pipe = buildmod.Pipeline(cfg)

    def fake_synthesize(_text, out_wav, out_srt, **_kwargs):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        Path(out_wav).write_bytes(b"synthetic-melo-wav")
        Path(out_srt).write_text("1\n00:00:00,000 --> 00:00:01,000\n문장\n", encoding="utf-8")
        return {
            "wav": str(out_wav), "srt": str(out_srt), "durationSec": 1.0,
            "entries": [{"text": "문장"}],
            "voice": {
                "engine": "melo-ko", "model": "myshell-ai/MeloTTS-Korean",
                "modelRevision": "0207e5adfc90129a51b6b03d89be6d84360ed323",
                "packageVersion": "0.1.2", "language": "ko", "speaker": "KR",
                "nativeSampleRate": 44100, "speed": 1.0,
                "speedAppliedBy": "melo-native-length-scale",
                "license": {"model": "MIT", "url": "https://example.com", "aiDisclosureRequired": True},
            },
        }

    monkeypatch.setattr(buildmod.bv_tts, "synthesize_narration", fake_synthesize)
    payload = pipe.stage_stt()
    manifest = json.loads(Path(payload["voiceManifest"]).read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 2
    assert manifest["speaker"] == "KR"
    assert manifest["speedAppliedBy"] == "melo-native-length-scale"
    assert not Path(payload["voiceManifest"]).with_name(".voice-manifest.json.tmp").exists()


def test_legacy_supertonic_srt_timing_manifest_records_reapplied_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = _tts_project(tmp_path, timing="srt")
    pipe = buildmod.Pipeline(cfg)

    def fake_synthesize(_text, out_wav, out_srt, **_kwargs):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        Path(out_wav).write_bytes(b"wav")
        Path(out_srt).write_text("1\n00:00:00,000 --> 00:00:01,000\n문장\n", encoding="utf-8")
        return {
            "wav": str(out_wav), "srt": str(out_srt), "durationSec": 1.0,
            "entries": [{"text": "문장"}],
            "voice": {"engine": "supertonic", "voicePresetId": "female-09"},
        }

    monkeypatch.setattr(buildmod.bv_tts, "synthesize_narration", fake_synthesize)
    payload = pipe.stage_stt()
    manifest = json.loads(Path(payload["voiceManifest"]).read_text(encoding="utf-8"))
    assert manifest["requestedTiming"] == "srt"
    assert manifest["appliedTiming"] == "tts"


def test_auto_bgm_attaches_local_music_when_no_voice(tmp_path, monkeypatch):
    """대사 없는(ambient) 영상은 bgm 블록 없이도 로컬 BGM이 자동으로 붙는다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="auto-ambient")  # bgm=None, voice 없음 → ambient
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300}])

    captured = {}

    def preflight(config, **_kwargs):
        captured["assetIds"] = buildmod.bv_bgm.selected_asset_ids(config)
        return [{"id": config.asset_id, "sha256": "deadbeef",
                 "resolvedPath": str(tmp_path / "song.mp3")}]

    monkeypatch.setattr(buildmod.bv_bgm, "preflight_assets", preflight)
    monkeypatch.setattr(buildmod.bv_bgm, "write_license_manifest", lambda *a, **k: tmp_path / "m.json")
    monkeypatch.setattr(buildmod.bv_mix, "prepare_bgm",
                        lambda paths, out, **k: (Path(out).write_bytes(b"b") or Path(out), {"tracks": []}))
    monkeypatch.setattr(buildmod.bv_mix, "copy_master",
                        lambda _src, out: (Path(out).write_bytes(b"m") or Path(out)))
    monkeypatch.setattr(buildmod.bv_mix, "write_mix_report", lambda out, _r: Path(out))

    result = pipe.stage_mix()
    assert result["autoSelected"] is True
    assert result["mode"] == "asset"
    assert captured["assetIds"] == ("youtube-chris-zabriskie-fight-for-your-honor",)


def test_auto_bgm_prefers_piano_candidate_when_stable_runtime_is_available(tmp_path, monkeypatch):
    """무음 ambient 자동 BGM은 기존 catalog보다 Stable Audio piano-auto를 먼저 시도한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="auto-piano", title="별빛 호수", drawing_profile="brush")
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 900}])
    source = tmp_path / "piano-auto.wav"
    source.write_bytes(b"generated")
    captured = {}

    def generate(candidate_cfg, duration, bgm_cfg, **_kwargs):
        captured["duration"] = duration
        captured["mode"] = bgm_cfg.mode
        return {"mode": "piano-auto", "engine": "stable-audio-3-mlx", "source": str(source),
                "manifest": "generated-bgm-manifest.json", "status": "PENDING_USER_LISTENING"}

    def prepare(paths, out, **_kwargs):
        captured["source"] = str(paths[0])
        Path(out).write_bytes(b"bgm")
        return Path(out), {"tracks": []}

    monkeypatch.setattr(buildmod.bv_piano_auto, "build_candidate", generate)
    monkeypatch.setattr(buildmod.bv_mix, "prepare_bgm", prepare)
    monkeypatch.setattr(buildmod.bv_mix, "copy_master",
                        lambda _src, out: (Path(out).write_bytes(b"master") or Path(out)))
    monkeypatch.setattr(buildmod.bv_mix, "write_mix_report", lambda out, _r: Path(out))
    result = pipe.stage_mix()
    assert result["mode"] == "piano-auto"
    assert result["generatedCandidate"]["status"] == "PENDING_USER_LISTENING"
    assert captured == {"duration": 30.0, "mode": "piano-auto", "source": str(source)}


def test_explicit_piano_auto_is_available_with_voice_and_uses_ducking(tmp_path, monkeypatch):
    """내레이션 영상은 명시적인 piano-auto일 때만 생성 BGM을 붙인다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    voice = tmp_path / "voice.wav"
    cfg = buildmod.ProjectConfig(project_id="voice-piano-auto", audio=voice,
                                 srt=tmp_path / "voice.srt", bgm=BgmConfig(mode="piano-auto"))
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 900}])
    source = tmp_path / "piano-auto.wav"
    source.write_bytes(b"generated")
    captured = {}

    monkeypatch.setattr(buildmod.bv_piano_auto, "build_candidate",
                        lambda *_a, **_k: {"mode": "piano-auto", "engine": "stable-audio-3-mlx",
                                          "source": str(source), "status": "PENDING_USER_LISTENING"})
    monkeypatch.setattr(buildmod.bv_mix, "prepare_bgm",
                        lambda paths, out, **_k: (captured.setdefault("bgm", str(paths[0])) or Path(out), {"tracks": []}))
    monkeypatch.setattr(buildmod.bv_mix, "mix_voice_and_bgm",
                        lambda voice_path, _bgm, out, **_k: (captured.setdefault("voice", str(voice_path)) or Path(out),
                                                            {"ducking": {"enabled": True}}))
    monkeypatch.setattr(buildmod.bv_mix, "write_mix_report", lambda out, _r: Path(out))
    result = pipe.stage_mix()
    assert result["mode"] == "piano-auto"
    assert captured == {"bgm": str(source), "voice": str(voice)}


def test_explicit_piano_auto_surfaces_runtime_failure(tmp_path, monkeypatch):
    """명시 opt-in은 Stable Audio 미설치를 조용히 synth로 바꾸지 않는다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="explicit-piano-error", bgm=BgmConfig(mode="piano-auto"))
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 900}])

    def fail(*_args, **_kwargs):
        raise buildmod.bv_piano_auto.PianoAutoBgmError("Stable Audio 경로 없음")

    monkeypatch.setattr(buildmod.bv_piano_auto, "build_candidate", fail)
    with pytest.raises(ValueError, match="명시적 bgm.mode=piano-auto"):
        pipe.stage_mix()


def test_auto_bgm_falls_back_to_synth_when_assets_missing(tmp_path, monkeypatch):
    """로컬 자산이 준비 안 됐으면 기존 synth BGM으로 폴백하고 빌드는 실패하지 않는다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="auto-fallback")
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300}])

    def boom(config, **_kwargs):
        raise buildmod.bv_bgm.BgmAssetError("로컬 음원 없음")

    monkeypatch.setattr(buildmod.bv_bgm, "preflight_assets", boom)
    monkeypatch.setattr(buildmod.bv_audio, "synth_ambient_bgm",
                        lambda path, *a, **k: (Path(path).write_bytes(b"s") or Path(path)))

    result = pipe.stage_mix()
    assert result["mode"] == "legacy-synth"
