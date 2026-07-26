"""audit.py 테스트 — 임계 로직(순수) + 합성 클립(ffmpeg) 검출 + 독립성 가드."""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from brushvid import audit as A

FPS = 30


# ── 독립성 (1급 요구) ──

def test_audit_standalone_imports():
    """audit.py 는 brushvid 타 모듈을 import 하지 않는다."""
    src = Path(A.__file__).read_text(encoding="utf-8")
    assert "from ." not in src
    assert "from brushvid" not in src
    assert "import brushvid" not in src


# ── 임계 로직 (순수 함수) ──

def test_classify_boundary_thresholds():
    assert A.classify_boundary(12.0) == "FAIL"   # city 수정 전 실측 12~23%
    assert A.classify_boundary(7.0) == "WARN"
    assert A.classify_boundary(5.3) is None      # city 수정 후 실측 상한


def test_classify_spike_thresholds():
    assert A.classify_spike(2.97, 2.97, 0.7) == "FAIL"   # develop 스파이크 실측
    assert A.classify_spike(1.8, 1.8, 0.5) == "WARN"
    assert A.classify_spike(0.94, 0.94, 0.7) is None     # 수정 후 정상 페이드 수준
    assert A.classify_spike(3.0, 3.0, 2.0) is None       # 배수 미달(주변도 높음)


def test_completion_reversal_metrics_accepts_monotonic_and_rejects_pulse():
    ok = A.completion_reversal_metrics(np.linspace(210, 150, 55))
    assert ok["direction"] == "darker"
    assert ok["reversal"] == pytest.approx(0, abs=0.01)
    pulse = A.completion_reversal_metrics(np.array([150, 205, 130], dtype=float))
    assert pulse["direction"] == "darker"
    assert pulse["reversal"] > A.COMPLETION_REVERSAL_FAIL


def test_completion_pulse_issues_and_phase_classification():
    lums = np.full(300, 180.0)
    lums[206:261] = np.concatenate([np.linspace(180, 210, 20), np.linspace(210, 140, 35)])
    windows = [{"sceneId": "s1", "offset": 0, "drawStart": 6,
                "lastStrokeEnd": 206, "developEnd": 242, "colorSettleEnd": 260,
                "outroStart": 276, "sceneEnd": 300}]
    issues, stats = A.completion_pulse_issues(lums, windows, 30)
    assert issues and issues[0].kind == "completion-pulse" and issues[0].severity == "FAIL"
    assert stats[0]["reversal"] > A.COMPLETION_REVERSAL_FAIL
    assert A.phase_for_frame(144, windows) == "drawing"
    assert A.phase_for_frame(230, windows) == "completion"
    assert A.phase_for_frame(288, windows) == "outro"


def test_completion_windows_are_loaded_from_standard_props(tmp_path):
    repo = tmp_path / "repo"
    props_dir = repo / "data" / "demo"
    route_dir = repo / "public" / "demo" / "routes"
    props_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    (route_dir / "scene-01.routes.json").write_text(json.dumps({
        "meta": {"drawStart": 6, "drawEnd": 206},
        "strokes": [{"end": 206}],
    }), encoding="utf-8")
    props = props_dir / "props.json"
    props.write_text(json.dumps({"scenes": [{
        "id": "scene-01", "routes": "demo/routes/scene-01.routes.json",
        "durationInFrames": 300, "completionMode": "integrated-develop",
        "developFrames": 36, "colorSettleFrames": 18, "outroFadeFrames": 24,
    }]}), encoding="utf-8")
    windows = A.completion_windows_from_props(props)
    assert windows[0]["lastStrokeEnd"] == 206
    assert windows[0]["colorSettleEnd"] == 260
    assert windows[0]["sceneEnd"] == 300


def test_pen_brush_paint_windows_are_exempt_from_completion_pulse(tmp_path):
    repo = tmp_path / "repo"
    props_dir = repo / "data" / "demo"
    route_dir = repo / "public" / "demo" / "routes"
    props_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    (route_dir / "scene-01-paint.routes.json").write_text(json.dumps({
        "meta": {"drawStart": 114, "drawEnd": 264},
        "strokes": [{"start": 114, "end": 264}],
    }), encoding="utf-8")
    props = props_dir / "props.json"
    props.write_text(json.dumps({"scenes": [{
        "id": "scene-01", "durationInFrames": 300, "outroFadeFrames": 36,
        "drawingPhases": [{"kind": "paint", "routes": "demo/routes/scene-01-paint.routes.json"}],
    }]}), encoding="utf-8")
    windows = A.completion_windows_from_props(props)
    assert len(windows) == 1
    assert windows[0]["auditKind"] == "pen-brush-paint"
    assert A.phase_for_frame(180, windows) == "drawing"
    issues, stats = A.completion_pulse_issues(np.full(300, 180.0), windows, FPS)
    assert issues == [] and stats == []


def test_judge_candidate_three_way():
    """transient=스파이크 / 지속+구도유지=워시 / 지속+구도변화=하드컷."""
    kind, sev = A.judge_candidate(3.0, 3.0, 0.5, transient=True, corr=1.0, near_scene_end=False)
    assert (kind, sev) == ("spike", "FAIL")
    kind, sev = A.judge_candidate(8.0, 8.0, 0.5, transient=False, corr=0.98, near_scene_end=True)
    assert (kind, sev) == ("outro-wash", "INFO")     # 씬 끝 워시 온셋은 연출
    kind, sev = A.judge_candidate(8.0, 8.0, 0.5, transient=False, corr=0.98, near_scene_end=False)
    assert (kind, sev) == ("wash-jump", "WARN")
    kind, sev = A.judge_candidate(12.0, 12.0, 0.5, transient=False, corr=0.2, near_scene_end=True)
    assert (kind, sev) == ("hardcut", "FAIL")


def test_pearson_corr():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, 1000).astype(np.int16)
    washed = (a * 0.2 + 200).astype(np.int16)   # 같은 구도의 워시
    assert A.pearson_corr(a, washed) > 0.99
    other = rng.integers(0, 255, 1000).astype(np.int16)
    assert A.pearson_corr(a, other) < 0.5
    flat = np.full(1000, 128, dtype=np.int16)
    assert A.pearson_corr(flat, flat) == 0.0     # 분산 0 → 구조 비교 불능


def test_spike_candidates_cluster_and_exclude():
    d = np.full(200, 0.3)
    d[100], d[102] = 5.0, 6.0   # 근접 클러스터 → 피크(102)만
    d[150] = 9.0                # 경계 제외 대상
    roll = np.full(200, 0.3)
    peaks = A.spike_candidates(d, roll, exclude={149, 150, 151})
    assert peaks == [102]


def test_prioritize_spike_candidates_measures_late_high_risk_frames_first():
    """재측정 상한이 있어도 시간상 뒤쪽의 큰 후보를 먼저 측정한다."""
    diffs = np.asarray([0.0, 1.2, 6.4, 3.1, 5.8])
    roll = np.asarray([0.3, 0.2, 0.8, 0.4, 0.6])
    assert A.prioritize_spike_candidates([1, 2, 3, 4], diffs, roll) == [2, 4, 3, 1]


def test_find_freeze_runs_and_severity():
    d = np.full(400, 1.0)
    d[50:145] = 0.0    # 95프레임(3.2s) 정지
    d[200:260] = 0.0   # 60프레임(2s) — 미달
    runs = A.find_freeze_runs(d, FPS)
    assert runs == [(50, 144)]
    # 씬 끝(경계 160 직전) → info / 경계에서 먼 정지 → WARN
    assert A.freeze_severity((50, 144), [160], 400, FPS) == "INFO"
    assert A.freeze_severity((50, 144), [350], 400, FPS) == "WARN"
    assert A.freeze_severity((50, 144), [], 165, FPS) == "INFO"  # 영상 끝 감상


def test_estimate_boundaries_white_runs():
    lums = np.full(300, 180.0)
    lums[95:101] = 250.0   # 6프레임 순백 유지 → 경계 101
    lums[200] = 250.0      # 1프레임 화이트 플래시 → 경계 아님
    assert A.estimate_boundaries(lums) == [101]


def test_check_spec_rules():
    base = {"width": 1920, "height": 1080, "fps": 30.0, "vcodec": "h264",
            "acodec": "aac", "hasAudio": True, "duration": 600.0}
    assert A.check_spec(base) == []
    shorts_long = {**base, "width": 1080, "height": 1920, "duration": 181.0}
    assert any(i.severity == "FAIL" for i in A.check_spec(shorts_long))  # 쇼츠 180s 초과
    assert any(i.severity == "FAIL" and i.kind == "audio-missing"
               for i in A.check_spec({**base, "hasAudio": False, "acodec": None}))
    warns = A.check_spec({**base, "width": 320, "height": 180, "fps": 25.0, "vcodec": "vp9"})
    assert {i.severity for i in warns} == {"WARN"} and len(warns) == 3
    uhd = {**base, "width": 3840, "height": 2160, "nbFrames": 18000}
    assert A.check_spec(uhd) == []
    assert any(i.severity == "FAIL" and "18000" in i.message
               for i in A.check_spec({**uhd, "nbFrames": 17998}))


def test_full_bleed_qa_contract_checks_sequence_and_overlay_absence(tmp_path):
    from brushvid.qa import write_full_bleed_report

    scenes = [
        {"id": "scene-01", "presentation": "progressive-frame-sequence",
         "image": "demo/bg/scene-01-content.png", "captionsVisible": False, "widgets": []},
        {"id": "scene-02", "presentation": "progressive-frame-sequence",
         "image": "demo/bg/scene-02-content.png", "previousImage": "demo/bg/scene-01-content.png",
         "captionsVisible": False, "widgets": []},
    ]
    report = write_full_bleed_report(tmp_path / "full-bleed-report.json", project_id="demo",
                                     profile="progressive-frame-sequence", scenes=scenes,
                                     expected_scene_count=2)
    assert report["pass"]
    scenes[1]["previousImage"] = "wrong.png"
    assert not write_full_bleed_report(tmp_path / "bad-report.json", project_id="demo",
                                       profile="progressive-frame-sequence", scenes=scenes,
                                       expected_scene_count=2)["pass"]


def test_audio_issue_rules():
    dur = 30.0
    # 무음 2s 초과 → WARN
    parsed = {"silences": [(5.0, 8.5)], "meanVolume": -25.0, "maxVolume": -3.0}
    issues = A.audio_issues(parsed, dur)
    assert [i.severity for i in issues] == ["WARN"] and issues[0].kind == "audio-silence"
    # 전체 무음 → FAIL 하나로 종결
    parsed = {"silences": [(0.0, 30.0)], "meanVolume": -80.0, "maxVolume": -60.0}
    issues = A.audio_issues(parsed, dur)
    assert [(i.severity, i.kind) for i in issues] == [("FAIL", "audio-silence")]
    issues = A.audio_issues(parsed, dur, allow_full_silence=True)
    assert [(i.severity, i.kind) for i in issues] == [("INFO", "audio-silence")]
    # 클리핑 > -0.5dB → WARN
    parsed = {"silences": [], "meanVolume": -12.0, "maxVolume": -0.1}
    issues = A.audio_issues(parsed, dur)
    assert [(i.severity, i.kind) for i in issues] == [("WARN", "audio-clipping")]


def test_parse_audio_stderr():
    text = ("[silencedetect @ 0x0] silence_start: 3.2\n"
            "[silencedetect @ 0x0] silence_end: 6.4 | silence_duration: 3.2\n"
            "[silencedetect @ 0x0] silence_start: 25.0\n"   # EOF 까지 무음 (end 없음)
            "[Parsed_volumedetect @ 0x0] mean_volume: -21.3 dB\n"
            "[Parsed_volumedetect @ 0x0] max_volume: -0.2 dB\n")
    p = A.parse_audio_stderr(text, 30.0)
    assert p["silences"] == [(3.2, 6.4), (25.0, 30.0)]
    assert p["meanVolume"] == -21.3 and p["maxVolume"] == -0.2


def test_parse_loudness_and_issue_ranges():
    text = '''
    {
      "input_i" : "-27.20",
      "input_tp" : "-0.40",
      "input_lra" : "2.10",
      "input_thresh" : "-38.00",
      "target_offset" : "0.00"
    }
    '''
    metrics = A.parse_loudnorm_stderr(text)
    assert metrics == {"integratedLufs": -27.2, "truePeakDbtp": -0.4, "lra": 2.1}
    kinds = {i.kind for i in A.loudness_issues(metrics)}
    assert kinds == {"audio-loudness", "audio-true-peak"}
    high = A.loudness_issues({"integratedLufs": -10.0, "truePeakDbtp": -2.0})
    assert [(i.severity, i.kind) for i in high] == [("WARN", "audio-loudness")]


def test_analyze_audio_skips_video_decode(monkeypatch):
    """오디오 감사는 UHD 비디오 프레임을 디코드하지 않아야 한다."""
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return type("Result", (), {"stderr": (
                "silence_start: 0\\n"
                "silence_end: 10 | silence_duration: 10\\n"
                "mean_volume: -91.0 dB\\n"
                "max_volume: -91.0 dB\\n"
            )})()
        return type("Result", (), {"stderr": (
            '{"input_i":"-99.0","input_tp":"-99.0","input_lra":"0.0"}'
        )})()

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    A.analyze_audio("fixture.mp4", 10.0, allow_full_silence=True)
    assert len(calls) == 2
    assert all("-vn" in argv for argv in calls)


def test_license_manifest_rules(tmp_path):
    manifest = tmp_path / "bgm-manifest.json"
    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"fixture-audio")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "source-page.png").write_bytes(b"source")
    (evidence / "license.pdf").write_bytes(b"license")
    import hashlib
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({
        "licensePolicy": "strict",
        "assets": [{
            "id": "track", "artist": "Artist", "sourcePage": "https://pixabay.com/music/x/",
            "sha256": digest, "resolvedPath": str(audio),
            "license": {"url": "https://pixabay.com/service/license-summary/",
                        "downloadedAt": "2026-07-12", "checkedAt": "2026-07-12",
                        "contentIdStatus": "not-displayed",
                        "evidenceFiles": ["evidence/source-page.png", "evidence/license.pdf"]},
        }],
    }), encoding="utf-8")
    issues, summary = A.check_license_manifest(manifest)
    assert summary["assetIds"] == ["track"]
    assert [(i.severity, i.kind) for i in issues] == [("WARN", "bgm-content-id")]

    audio.write_bytes(b"changed")
    issues, _ = A.check_license_manifest(manifest)
    assert any(i.severity == "FAIL" and "SHA-256 불일치" in i.message for i in issues)

    data = json.loads(manifest.read_text())
    data["assets"][0]["sha256"] = hashlib.sha256(audio.read_bytes()).hexdigest()
    data["assets"][0]["license"]["checkedAt"] = "2020-01-01"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_license_manifest(manifest)
    assert any(i.severity == "WARN" and "일 경과" in i.message for i in issues)

    data = json.loads(manifest.read_text())
    data["distribution"] = "youtube"
    data["assets"][0]["source"] = "pixabay"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    issues, summary = A.check_license_manifest(manifest)
    assert summary["distribution"] == "youtube"
    assert any(i.severity == "FAIL" and i.kind == "bgm-source-policy" for i in issues)

    data["assets"][0]["source"] = "youtube-audio-library"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_license_manifest(manifest)
    assert not any(i.kind == "bgm-source-policy" for i in issues)

    manifest.write_text('{"assets": []}', encoding="utf-8")
    issues, _ = A.check_license_manifest(manifest)
    assert issues[0].severity == "FAIL" and issues[0].kind == "bgm-license"


def test_generated_piano_manifest_is_pending_warning_not_legacy_asset_failure(tmp_path):
    manifest = tmp_path / "generated-bgm-manifest.json"
    audio = tmp_path / "delivery.wav"
    audio.write_bytes(b"generated-audio")
    import hashlib
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({
        "kind": "generated-piano-bgm", "assetId": "demo", "engine": "stable-audio-3-mlx",
        "status": "PENDING_USER_LISTENING", "provenance": "provenance.json", "qa": "qa.json",
        "localPath": str(audio), "deliverySha256": digest,
        "license": {"modelUrl": "https://huggingface.co/stabilityai/stable-audio-3-optimized",
                     "termsUrl": "https://stability.ai/license"},
    }), encoding="utf-8")
    issues, summary = A.check_license_manifest(manifest)
    assert summary["kind"] == "generated-piano-bgm"
    assert [(issue.severity, issue.kind) for issue in issues] == [("WARN", "bgm-generated-pending")]


def _valid_voice_manifest():
    return {
        "schemaVersion": 1,
        "projectId": "voice-demo",
        "requestedVoice": "female-09",
        "voicePresetId": "female-09",
        "voicePackVersion": "1.0.0",
        "engine": "supertonic",
        "packageVersion": "1.3.1",
        "model": "supertonic-3",
        "language": "ko",
        "sampleRate": 44100,
        "speed": 1.10,
        "components": {"F4": 0.65, "F1": 0.35},
        "catalogSha256": "a" * 64,
        "styleSourceSha256": {"F4": "b" * 64, "F1": "c" * 64},
        "styleSha256": "d" * 64,
        "aiDisclosure": "이 콘텐츠의 내레이션은 Supertonic AI 합성 음성으로 제작되었습니다.",
        "license": {
            "model": "OpenRAIL-M", "url": "https://example.com/license",
            "aiDisclosureRequired": True,
        },
        "pauseMs": 350,
        "timing": "tts",
        "durationSec": 10.0,
        "sentenceCount": 2,
    }


def test_voice_manifest_reproducibility_and_disclosure_contract(tmp_path):
    path = tmp_path / "voice-manifest.json"
    path.write_text(json.dumps(_valid_voice_manifest()), encoding="utf-8")
    issues, summary = A.check_voice_manifest(path)
    assert issues == []
    assert summary["voicePresetId"] == "female-09"
    assert summary["components"] == {"F4": 0.65, "F1": 0.35}


def test_voice_manifest_missing_hash_and_disclosure_fail(tmp_path):
    data = _valid_voice_manifest()
    data["styleSha256"] = None
    data["aiDisclosure"] = ""
    path = tmp_path / "voice-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_voice_manifest(path)
    kinds = {(issue.severity, issue.kind) for issue in issues}
    assert ("FAIL", "tts-voice-manifest") in kinds
    assert ("FAIL", "tts-ai-disclosure") in kinds


def test_voice_manifest_version_drift_warns_without_hiding_contract_errors(tmp_path):
    data = _valid_voice_manifest()
    data["packageVersion"] = "1.4.0"
    path = tmp_path / "voice-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_voice_manifest(path)
    assert [(issue.severity, issue.kind) for issue in issues] == [("WARN", "tts-version-drift")]


def test_engine_v2_voice_manifest_uses_shared_schema_and_semantic_rules(tmp_path):
    data = {
        "schemaVersion": 2, "projectId": "melo", "engine": "melo-ko", "voice": "kr-default",
        "model": "myshell-ai/MeloTTS-Korean", "modelRevision": "a" * 40,
        "packageVersion": "0.1.2", "language": "ko", "nativeSampleRate": 44100,
        "outputSampleRate": 44100, "requestedSpeed": 1.0, "appliedSpeed": 1.0,
        "speedAppliedBy": "melo-native-length-scale",
        "requestedTiming": "tts", "appliedTiming": "tts", "pauseMs": 300,
        "durationSec": 1.0, "sentenceCount": 1, "audioSha256": "b" * 64,
        "license": {"model": "MIT", "url": "https://example.com", "aiDisclosureRequired": True},
        "aiDisclosure": "AI 합성 음성", "speaker": "KR",
    }
    path = tmp_path / "voice-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    issues, summary = A.check_voice_manifest(path)
    assert issues == []
    assert summary["engine"] == "melo-ko"
    assert summary["speedAppliedBy"] == "melo-native-length-scale"
    data["speaker"] = "fallback"
    path.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_voice_manifest(path)
    assert any(issue.severity == "FAIL" for issue in issues)


def test_mix_report_contract(tmp_path):
    report = tmp_path / "mix-report.json"
    report.write_text(json.dumps({
        "durationSec": 10.0, "mode": "playlist",
        "bgm": {"kind": "playlist", "tracks": [{}, {}], "crossfadeSec": 3.0,
                "output": {"truePeakDbtp": -2.0}},
        "voice": {"ducking": {"enabled": True, "ratio": 8.2,
                               "measuredAttenuationDb": 5.4}},
    }), encoding="utf-8")
    issues, summary = A.check_mix_report(report, 10.05)
    assert not issues
    assert summary["trackCount"] == 2 and summary["ducking"]["enabled"]

    data = json.loads(report.read_text())
    data["voice"]["ducking"]["ratio"] = 1
    report.write_text(json.dumps(data), encoding="utf-8")
    issues, _ = A.check_mix_report(report, 11.0)
    assert {i.kind for i in issues} == {"audio-duration", "audio-ducking"}


def test_voice_only_mix_report_does_not_require_ducking(tmp_path):
    report = tmp_path / "mix-report.json"
    report.write_text(json.dumps({
        "durationSec": 10.0, "mode": "off", "bgm": None,
        "voice": {"ducking": {"enabled": False, "reason": "bgm-off"}},
    }), encoding="utf-8")
    issues, summary = A.check_mix_report(report, 10.0)
    assert not issues
    assert summary["trackCount"] == 0
    assert summary["ducking"] is None


def test_mix_report_prefers_active_region_over_low_whole_timeline_duck_average(tmp_path):
    report = tmp_path / "mix-report.json"
    report.write_text(json.dumps({
        "durationSec": 60.0, "mode": "asset",
        "bgm": {"kind": "asset", "tracks": [{}], "crossfadeSec": 0,
                "output": {"truePeakDbtp": -2.8}},
        "voice": {"ducking": {
            "enabled": True, "ratio": 8.2, "requestedAmountDb": 8,
            "measuredAttenuationDb": 0.79,
            "regionMetrics": {"activeAttenuationDb": 5.8, "inactiveAttenuationDb": 0.22},
        }},
    }), encoding="utf-8")
    issues, _summary = A.check_mix_report(report, 60.0)
    assert not [issue for issue in issues if issue.kind == "audio-ducking"]


def test_audio_shape_fade_detects_missing_and_accepts_faded(tmp_path):
    raw = tmp_path / "raw.wav"
    faded = tmp_path / "faded.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-c:a", "pcm_s16le", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                    "afade=t=in:d=1,afade=t=out:st=3:d=1", str(faded)], check=True)
    report = tmp_path / "mix.json"
    report.write_text(json.dumps({
        "durationSec": 4, "mode": "asset", "voice": None,
        "settings": {"fadeInSec": 1, "fadeOutSec": 1},
        "bgm": {"kind": "asset", "tracks": [{}], "crossfadeSec": 0},
    }), encoding="utf-8")
    issues, metrics = A.audio_shape_issues(faded, report)
    assert not issues and metrics["fade"]["startDb"] < metrics["fade"]["bodyDb"] - 3
    issues, _ = A.audio_shape_issues(raw, report)
    assert {i.kind for i in issues} == {"audio-fade"}


# ── 합성 클립 통합 (ffmpeg) ──

SIZE = "320x180"


def _lavfi_concat(tmp_path, name, srcs):
    """lavfi 소스 여러 개를 concat 한 무음 mp4 생성."""
    out = tmp_path / name
    args = ["ffmpeg", "-v", "error", "-y"]
    for s in srcs:
        args += ["-f", "lavfi", "-i", s]
    args += ["-filter_complex", "".join(f"[{i}]" for i in range(len(srcs))) +
             f"concat=n={len(srcs)}:v=1[v]", "-map", "[v]",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(args, check=True, capture_output=True)
    return out


def _kinds(result, severity=None):
    return {i["kind"] for i in result["issues"]
            if severity is None or i["severity"] == severity}


def test_synthetic_hardcut_detected(tmp_path):
    """중간에 하드컷이 있는 합성 클립 → props 없이 hardcut FAIL."""
    clip = _lavfi_concat(tmp_path, "cut.mp4", [
        f"color=c=0x909090:s={SIZE}:d=2:r={FPS}",
        f"color=c=0x202020:s={SIZE}:d=2:r={FPS}"])
    r = A.run_audit(clip, out_dir=None)
    assert r["verdict"] == "FAIL"
    fails = [i for i in r["issues"] if i["severity"] == "FAIL" and i["kind"] == "hardcut"]
    assert fails and abs(fails[0]["frame"] - 60) <= 2


def test_synthetic_spike_detected(tmp_path):
    """1프레임 백색 플래시(번쩍 후 복귀) → spike FAIL (transient 분류)."""
    frames_dir = tmp_path / "fr"
    frames_dir.mkdir()
    for i in range(75):
        lum = 250 if i == 40 else 120
        Image.new("RGB", (320, 180), (lum, lum, lum)).save(frames_dir / f"{i:04d}.png")
    clip = tmp_path / "spike.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS),
                    "-i", str(frames_dir / "%04d.png"), "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)],
                   check=True, capture_output=True)
    r = A.run_audit(clip, out_dir=None)
    spikes = [i for i in r["issues"] if i["kind"] == "spike" and i["severity"] == "FAIL"]
    assert spikes and abs(spikes[0]["frame"] - 40) <= 2
    assert spikes[0]["metrics"]["transient"] is True


def test_synthetic_freeze_warn_and_report(tmp_path):
    """4.5s 정지 후 2.5s 모션 → 씬 끝이 아닌 정지 WARN + 리포트 파일 산출."""
    clip = _lavfi_concat(tmp_path, "freeze.mp4", [
        f"color=c=0x707070:s={SIZE}:d=4.5:r={FPS}",
        f"testsrc=size={SIZE}:d=2.5:r={FPS}"])
    out = tmp_path / "report"
    r = A.run_audit(clip, out_dir=out)
    fz = [i for i in r["issues"] if i["kind"] == "freeze"]
    assert fz and fz[0]["severity"] == "WARN"
    assert (out / "audit-report.md").is_file() and (out / "audit-report.json").is_file()
    assert "FIELD-LOG 초안" in (out / "audit-report.md").read_text(encoding="utf-8")


def _write_wav(path, chunks, sr=44100):
    """chunks: [(초, 진폭)] 사인/무음 연결 wav."""
    data = np.concatenate([
        (amp * np.sin(2 * np.pi * 440 * np.arange(int(sr * sec)) / sr)) for sec, amp in chunks])
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def test_synthetic_audio_silence_and_clipping(tmp_path):
    """중간 3s 무음 + 클리핑 사인(진폭 1.2) → audio-silence WARN + audio-clipping WARN."""
    wav = tmp_path / "a.wav"
    _write_wav(wav, [(2, 1.2), (3, 0.0), (2, 1.2)])
    clip = tmp_path / "audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=0x808080:s={SIZE}:d=7:r={FPS}",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
                   check=True, capture_output=True)
    r = A.run_audit(clip, out_dir=None)
    kinds = _kinds(r, "WARN")
    assert "audio-silence" in kinds and "audio-clipping" in kinds


def test_synthetic_clean_clip_passes(tmp_path):
    """서서히 변하는 모션 + 정상 오디오 → FAIL 0 (verdict 는 spec WARN 무관 PASS)."""
    wav = tmp_path / "a.wav"
    _write_wav(wav, [(4, 0.4)])
    clip = tmp_path / "clean.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size={SIZE}:d=4:r={FPS}",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
                   check=True, capture_output=True)
    r = A.run_audit(clip, out_dir=None)
    assert r["verdict"] == "PASS"
    assert r["summary"]["FAIL"] == 0


def test_remeasure_boundaries_keeps_input_order_and_only_high_risk_frames(monkeypatch):
    """경계 재측정 병렬화가 임계 초과 프레임만 원래 순서대로 연결한다."""
    calls = []

    def fake_pair(_video, frame, _fps, _width, _height):
        calls.append(frame)
        return frame / 10

    monkeypatch.setattr(A, "fullres_pair_diff", fake_pair)
    diffs = np.asarray([0.0, 3.5, 3.6, 2.1, 4.2])
    result = A.remeasure_boundaries("demo.mp4", [1, 2, 3, 4], diffs,
                                    fps=30, width=3840, height=2160, workers=2)
    assert list(result) == [2, 4]
    assert result == {2: 0.2, 4: 0.4}
    assert sorted(calls) == [2, 4]


def test_remeasure_spike_candidates_retries_none_without_changing_candidate_order(monkeypatch):
    """None 후보는 한도를 소비하지 않아 다음 후보가 같은 순서로 재측정된다."""
    calls = []

    def fake_analyze(_video, frame, _fps, _width, _height):
        calls.append(frame)
        return None if frame == 10 else {"peak": frame / 10, "corr": 1.0, "transient": False}

    monkeypatch.setattr(A, "analyze_window", fake_analyze)
    results, measured = A.remeasure_spike_candidates(
        "demo.mp4", [10, 20, 30], limit=2, fps=30, width=3840, height=2160, workers=2)
    assert list(results) == [10, 20, 30]
    assert results[10] is None and results[20]["peak"] == 2.0 and results[30]["peak"] == 3.0
    assert measured == 2
    assert sorted(calls) == [10, 20, 30]
