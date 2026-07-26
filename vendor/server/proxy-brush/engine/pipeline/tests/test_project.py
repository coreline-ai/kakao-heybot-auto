"""project.py 테스트 — 모드 판정 3종(TC-4.1/4.2/4.3 판정부) + TC-4.E1(format 오타)."""
import logging

import pytest

from brushvid.project import load_project


def _write_yaml(tmp_path, body: str):
    p = tmp_path / "project.yaml"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture()
def media(tmp_path):
    srt = tmp_path / "voice.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-mp3")
    return srt, audio


def test_tc_4_1_srt_and_audio_is_narration(tmp_path, media):
    """TC-4.1 판정부: srt+audio 제공 → narration (whisper 미호출 모드)."""
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
format: youtube
input:
  srt: voice.srt
  audio: voice.mp3
"""))
    assert cfg.mode == "narration"
    assert cfg.srt.name == "voice.srt"
    assert cfg.audio.name == "voice.mp3"


def test_tc_4_2_audio_only_is_whisper(tmp_path, media):
    """TC-4.2 판정부: audio 만 → whisper 모드."""
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  audio: voice.mp3
"""))
    assert cfg.mode == "whisper"


def test_tc_4_3_neither_is_ambient(tmp_path):
    """TC-4.3 판정부: 둘 다 없음 → ambient (기본 3씬)."""
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
ambient:
  scenes: 3
"""))
    assert cfg.mode == "ambient"
    assert cfg.ambient_scenes == 3


def test_imagegen_fallback_policy_is_loaded_and_validated(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
background:
  strategy: imagegen
fallbackPolicy:
  imagegen: fail
"""))
    assert cfg.bg_imagegen_fallback == "fail"
    with pytest.raises(ValueError, match="fallbackPolicy.imagegen"):
        load_project(_write_yaml(tmp_path, """
projectId: invalid-policy
fallbackPolicy:
  imagegen: mystery
"""))


def test_tc_4_e1_format_typo_rejected(tmp_path):
    """TC-4.E1: format 오타 → 즉시 ValueError (파이프라인 미진입)."""
    with pytest.raises(ValueError, match="format"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
format: youtub
"""))


def test_missing_files_and_bad_strategy_rejected(tmp_path):
    """존재하지 않는 입력 파일 / 잘못된 배경 전략도 검증 단계에서 거부."""
    with pytest.raises(ValueError, match="input.srt"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: no-such.srt
"""))
    with pytest.raises(ValueError, match="strategy"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
background:
  strategy: presett
"""))


def test_widgets_auto_deferred_to_none(tmp_path, caplog):
    """widgets: auto 는 보류 — 경고 후 none 으로 처리."""
    with caplog.at_level(logging.WARNING, logger="brushvid.project"):
        cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
widgets: auto
"""))
    assert cfg.widgets == "none"
    assert any("보류" in r.message for r in caplog.records)


def test_widgets_authored_inline(tmp_path):
    """widgets 에 목록을 주면 authored 모드."""
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
widgets:
  - type: stat
    label: 성장률
"""))
    assert cfg.widgets == "authored"
    assert cfg.authored_widgets[0]["type"] == "stat"


def test_pen_brush_profile_and_timing_options(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
background:
  fit: cover
drawing:
  profile: pen-brush
  fullBleed: true
  outlineRatio: 0.4
  handoffFrames: 10
  paintEndRatio: 0.9
"""))
    assert cfg.drawing_profile == "pen-brush"
    assert cfg.drawing_full_bleed is True
    assert cfg.drawing_outline_ratio == 0.4
    assert cfg.drawing_handoff_frames == 10
    assert cfg.drawing_paint_end_ratio == 0.9


def test_youtube_uhd_and_full_bleed_profiles_are_accepted(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"png-placeholder")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: progressive-demo
format: youtube
render:
  resolution: uhd
background:
  strategy: user-images
  images: [source.png]
  fit: cover
drawing:
  profile: progressive-frame-sequence
ambient:
  scenes: 1
  cues: ["frame one"]
"""))
    assert cfg.render_resolution == "uhd"
    assert cfg.drawing_profile == "progressive-frame-sequence"


def test_overlays_none_is_loaded_for_prompt_driven_art_film(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: art-film
overlays: none
"""))
    assert cfg.overlays == "none"


def test_invalid_overlays_policy_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="overlays"):
        load_project(_write_yaml(tmp_path, """
projectId: art-film
overlays: captions-only
"""))


def test_uhd_shorts_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="format: youtube"):
        load_project(_write_yaml(tmp_path, """
projectId: uhd-shorts
format: shorts
render:
  resolution: uhd
"""))


def test_full_bleed_rejects_contain_fit(tmp_path):
    """풀블리드인데 contain을 선택하면 4면 종이 여백이 생기므로 즉시 거부한다."""
    with pytest.raises(ValueError, match="background.fit: cover"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
background:
  fit: contain
drawing:
  profile: pen-brush
  fullBleed: true
"""))


def test_pen_brush_bad_advanced_option_rejected(tmp_path):
    with pytest.raises(ValueError, match="지원하지 않는 옵션"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
drawing:
  profile: pen-brush
  magicZones: true
"""))


def test_cosmic_random_brush_profile_contract(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"png-placeholder")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: cosmic-demo
format: youtube
background:
  strategy: user-images
  images: [source.png]
drawing:
  profile: cosmic-random-brush
  seed: 260712
ambient:
  scenes: 1
"""))
    assert cfg.drawing_profile == "cosmic-random-brush"
    assert cfg.seed == 260712
    assert cfg.ambient_scenes == 1


def test_dark_random_brush_public_profile_alias_normalizes_to_runtime_key(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"png-placeholder")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: dark-demo
format: youtube
background:
  strategy: user-images
  images: [source.png]
  fit: cover
drawing:
  profile: dark-random-brush
  seed: 260712
ambient:
  scenes: 1
"""))
    # public profile is content-agnostic; runtime keeps the historical golden key.
    assert cfg.drawing_profile == "cosmic-random-brush"
    assert cfg.bg_fit == "cover"


def test_cosmic_random_brush_v02_six_scene_contract(tmp_path):
    images = []
    for i in range(6):
        image = tmp_path / f"scene-{i + 1:02d}.png"
        image.write_bytes(b"png-placeholder")
        images.append(f"    - {image.name}")
    cfg = load_project(_write_yaml(tmp_path, f"""
projectId: cosmic-v02
format: youtube
background:
  strategy: user-images
  images:
{chr(10).join(images)}
drawing:
  profile: cosmic-random-brush
  seed: 260712
ambient:
  scenes: 6
"""))
    assert cfg.ambient_scenes == 6
    assert len(cfg.bg_images) == 6


def test_cosmic_random_brush_v03_sixty_scene_contract(tmp_path):
    images = []
    cues = []
    for i in range(60):
        image = tmp_path / f"scene-{i + 1:02d}.png"
        image.write_bytes(b"png-placeholder")
        images.append(f"    - {image.name}")
        cues.append(f'    - "{i + 1:02d}. scene"')
    cfg = load_project(_write_yaml(tmp_path, f"""
projectId: cosmic-v03
format: youtube
background:
  strategy: user-images
  images:
{chr(10).join(images)}
drawing:
  profile: cosmic-random-brush
ambient:
  scenes: 60
  cues:
{chr(10).join(cues)}
"""))
    assert cfg.ambient_scenes == 60
    assert len(cfg.bg_images) == len(cfg.ambient_cues) == 60


@pytest.mark.parametrize("extra,match", [
    ("format: shorts", "format: youtube"),
    ("ambient:\n  scenes: 2", r"1\(골든\), 6\(v0.2\), 60"),
    ("ambient:\n  scenes: 59", r"1\(골든\), 6\(v0.2\), 60"),
    ("ambient:\n  scenes: 6", "user-images 필수"),
])
def test_cosmic_random_brush_v01_scope_rejected(tmp_path, extra, match):
    body = f"""
projectId: cosmic-demo
{extra}
drawing:
  profile: cosmic-random-brush
"""
    with pytest.raises(ValueError, match=match):
        load_project(_write_yaml(tmp_path, body))


def test_cosmic_random_brush_rejects_image_count_mismatch(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"png-placeholder")
    with pytest.raises(ValueError, match="images 수가 ambient.scenes와 같아야"):
        load_project(_write_yaml(tmp_path, """
projectId: cosmic-v02-bad-count
background:
  strategy: user-images
  images: [source.png]
drawing:
  profile: cosmic-random-brush
ambient:
  scenes: 6
"""))


def test_cosmic_random_brush_sixty_scene_rejects_missing_cues(tmp_path):
    images = []
    for i in range(60):
        image = tmp_path / f"scene-{i + 1:02d}.png"
        image.write_bytes(b"png-placeholder")
        images.append(f"    - {image.name}")
    with pytest.raises(ValueError, match="ambient.cues 60개"):
        load_project(_write_yaml(tmp_path, f"""
projectId: cosmic-v03-bad-cues
background:
  strategy: user-images
  images:
{chr(10).join(images)}
drawing:
  profile: cosmic-random-brush
ambient:
  scenes: 60
"""))


# ── 로컬 BGM 계약 ──

def test_bgm_asset_contract(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
bgm:
  mode: asset
  assetId: pixabay-gentle-piano-meditation
  gainDb: 5
  sourceStartSec: 3.4
  fadeInSec: 1.8
  fadeOutSec: 2
  ducking:
    enabled: true
    amountDb: 8
    attackMs: 120
    releaseMs: 600
  licensePolicy: strict
"""))
    assert cfg.bgm.mode == "asset"
    assert cfg.bgm.asset_id == "pixabay-gentle-piano-meditation"
    assert cfg.bgm.gain_db == 5
    assert cfg.bgm.source_start_sec == 3.4
    assert cfg.bgm.ducking_enabled is True


def test_bgm_playlist_contract(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
bgm:
  mode: playlist
  playlist:
    assetIds: [one-track, two-track, three-track]
    crossfadeSec: 3
"""))
    assert cfg.bgm.asset_ids == ("one-track", "two-track", "three-track")
    assert cfg.bgm.crossfade_sec == 3


def test_bgm_piano_auto_contract(tmp_path):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: piano-auto-demo
bgm:
  mode: piano-auto
  prompt: "warm cinematic solo piano"
  negativePrompt: "vocals, speech"
  cfg: 2.5
  steps: 8
"""))
    assert cfg.bgm.mode == "piano-auto"
    assert cfg.bgm.prompt == "warm cinematic solo piano"
    assert cfg.bgm.negative_prompt == "vocals, speech"
    assert cfg.bgm.cfg == 2.5 and cfg.bgm.steps == 8


@pytest.mark.parametrize("body", [
    "mode: asset\n  prompt: invalid",
    "mode: piano-auto\n  cfg: 10.1",
    "mode: piano-auto\n  steps: 17",
])
def test_bgm_piano_auto_sampling_contract_rejected(tmp_path, body):
    with pytest.raises(ValueError, match="bgm"):
        load_project(_write_yaml(tmp_path, f"projectId: piano-auto-invalid\nbgm:\n  {body}\n"))


@pytest.mark.parametrize("body,match", [
    ("mode: asset", "assetId"),
    ("mode: playlist\n  playlist:\n    assetIds: [only-one]", "2~3"),
    ("mode: synth\n  assetId: not-allowed", "지정할 수 없음"),
    ("mode: asset\n  assetId: okay\n  gainDb: 13", "gainDb"),
    ("mode: asset\n  assetId: okay\n  sourceStartSec: -1", "sourceStartSec"),
    ("mode: asset\n  assetId: okay\n  magic: true", "지원하지 않는 옵션"),
])
def test_bgm_invalid_contract_rejected(tmp_path, body, match):
    with pytest.raises(ValueError, match=match):
        load_project(_write_yaml(tmp_path, f"projectId: demo\nbgm:\n  {body}\n"))


# ── TTS 모드 매트릭스 ──

TTS_BLOCK = """
  tts:
    engine: supertonic
    voice: F1
    pauseMs: 250
"""


def test_tts_mode_srt_plus_tts(tmp_path, media):
    """srt + tts → tts 모드 (SRT 는 텍스트 소스)."""
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: voice.srt
""" + TTS_BLOCK))
    assert cfg.mode == "tts"
    assert cfg.tts == {
        "engine": "supertonic", "voice": "F1", "speed": 1.05,
        "pauseMs": 250, "timing": "tts",
    }
    assert "안녕" in cfg.tts_text()


def test_supertonic_legacy_srt_timing_is_accepted_for_text_source(tmp_path, media):
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: voice.srt
  tts:
    engine: supertonic
    voice: F1
    timing: srt
"""))
    assert cfg.mode == "tts"
    assert cfg.tts["timing"] == "srt"
    assert "안녕" in cfg.tts_text()


def test_tts_mode_script_plus_tts(tmp_path):
    """script + tts → tts 모드."""
    (tmp_path / "script.txt").write_text("첫 문장. 둘째 문장.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
""" + TTS_BLOCK))
    assert cfg.mode == "tts"
    assert cfg.tts_text() == "첫 문장. 둘째 문장."


def test_tts_ignored_when_real_audio(tmp_path, media, caplog):
    """srt + audio + tts → narration (실더빙 우선, TTS 무시 경고)."""
    with caplog.at_level(logging.WARNING, logger="brushvid.project"):
        cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: voice.srt
  audio: voice.mp3
""" + TTS_BLOCK))
    assert cfg.mode == "narration"
    assert any("실더빙 우선" in r.message for r in caplog.records)


def test_audio_script_tts_is_whisper_and_keeps_tts_out_of_execution_branch(tmp_path):
    (tmp_path / "voice.mp3").write_bytes(b"fake-mp3")
    (tmp_path / "script.txt").write_text("실제 음성 우선.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  audio: voice.mp3
  script: script.txt
  tts:
    engine: melo-ko
"""))
    assert cfg.mode == "whisper"
    assert cfg.tts["engine"] == "melo-ko"


def test_script_and_srt_tts_is_rejected_as_ambiguous_source(tmp_path, media):
    (tmp_path / "script.txt").write_text("대본 source.", encoding="utf-8")
    with pytest.raises(ValueError, match="source가 모호"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  srt: voice.srt
  tts:
    engine: supertonic
"""))


def test_melo_contract_defaults_to_kr_default_and_rejects_reference(tmp_path):
    (tmp_path / "script.txt").write_text("한국어 문장.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  tts:
    engine: melo-ko
    language: ko
"""))
    assert cfg.mode == "tts"
    assert cfg.tts["voice"] == "kr-default"
    with pytest.raises(ValueError, match="지원하지 않는 옵션"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  tts:
    engine: melo-ko
    reference: {}
"""))


def test_qwen_contract_requires_local_reference_pair(tmp_path):
    (tmp_path / "script.txt").write_text("복제 음성 문장.", encoding="utf-8")
    (tmp_path / "ref.wav").write_bytes(b"fake-wav")
    (tmp_path / "ref.txt").write_text("참조 음성 문장.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  tts:
    engine: qwen3-base
    voice: f1-reference
    language: ko
    reference:
      audio: ref.wav
      transcript: ref.txt
"""))
    assert cfg.tts["reference"]["audio"] == (tmp_path / "ref.wav").resolve()
    with pytest.raises(ValueError, match="reference"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  tts:
    engine: qwen3-base
    voice: f1-reference
"""))


def test_script_without_tts_rejected(tmp_path):
    """script 만 있고 tts 없음 → 검증 실패 (더빙/타이밍 소스 없음)."""
    (tmp_path / "script.txt").write_text("문장.", encoding="utf-8")
    with pytest.raises(ValueError, match="input.tts"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
"""))


def test_tts_bad_engine_and_timing_rejected(tmp_path, media):
    with pytest.raises(ValueError, match="engine"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: voice.srt
  tts:
    engine: eleven
"""))
    with pytest.raises(ValueError, match="timing"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  srt: voice.srt
  tts:
    timing: srtt
"""))


def test_tts_female_preset_and_speed_are_parsed(tmp_path):
    (tmp_path / "script.txt").write_text("전문 해설입니다.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
input:
  script: script.txt
  tts:
    engine: supertonic
    voice: female-09
    speed: 1.10
    pauseMs: 350
    timing: tts
"""))
    assert cfg.tts["voice"] == "female-09"
    assert cfg.tts["speed"] == pytest.approx(1.10)


@pytest.mark.parametrize("voice", ["F1", "F5", "M1", "M5", "female-01", "female-10"])
def test_tts_legacy_and_voice_pack_ids_are_supported(tmp_path, voice):
    (tmp_path / "script.txt").write_text("한 문장.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, f"""
projectId: demo
input:
  script: script.txt
  tts:
    voice: {voice}
"""))
    assert cfg.tts["voice"] == voice


@pytest.mark.parametrize("voice", ["F6", "female-11", "female-1", "m1"])
def test_tts_unknown_voice_is_rejected_without_fallback(tmp_path, voice):
    (tmp_path / "script.txt").write_text("한 문장.", encoding="utf-8")
    with pytest.raises(ValueError, match="지원하지 않는 TTS voice"):
        load_project(_write_yaml(tmp_path, f"""
projectId: demo
input:
  script: script.txt
  tts:
    voice: {voice}
"""))


@pytest.mark.parametrize("speed", [0.70, 2.00])
def test_tts_speed_boundaries_are_allowed(tmp_path, speed):
    (tmp_path / "script.txt").write_text("한 문장.", encoding="utf-8")
    cfg = load_project(_write_yaml(tmp_path, f"""
projectId: demo
input:
  script: script.txt
  tts:
    speed: {speed}
"""))
    assert cfg.tts["speed"] == pytest.approx(speed)


@pytest.mark.parametrize("speed", ["0.69", "2.01", ".nan", ".inf", "true", "fast"])
def test_tts_invalid_speed_is_rejected(tmp_path, speed):
    (tmp_path / "script.txt").write_text("한 문장.", encoding="utf-8")
    with pytest.raises((ValueError, TypeError), match="speed"):
        load_project(_write_yaml(tmp_path, f"""
projectId: demo
input:
  script: script.txt
  tts:
    speed: {speed}
"""))
