import json

import pytest

from brushvid.delivery import package_final_delivery, write_delivery_package


def test_delivery_package_contains_cc_attribution_and_upload_note(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    asset = {"id": "cc-track", "license": {
        "attributionRequired": True,
        "attributionText": "Track by Artist\nLicense: https://creativecommons.org/licenses/by/4.0/\nChanges: trimmed.",
    }}
    result = write_delivery_package(tmp_path / "delivery", project_id="demo", video=video,
                                    title="도시의 빛을 그리다", asset=asset)
    out = tmp_path / "delivery"
    assert "Track by Artist" in (out / "youtube-description.txt").read_text(encoding="utf-8")
    assert "표준 YouTube 라이선스" in (out / "UPLOAD-NOTES.txt").read_text(encoding="utf-8")
    assert json.loads((out / "delivery-manifest.json").read_text())["assetId"] == "cc-track"
    assert result["title"] == "도시의 빛을 그리다"


def test_delivery_package_rejects_missing_required_attribution(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    with pytest.raises(ValueError, match="attributionText"):
        write_delivery_package(tmp_path / "delivery", project_id="demo", video=video,
                               title="Demo", asset={"id": "bad", "license": {"attributionRequired": True}})


def test_final_delivery_copies_only_audit_passed_uhd_bundle(tmp_path):
    video = tmp_path / "final.mp4"
    yaml = tmp_path / "project.yaml"
    source = tmp_path / "source-manifest.json"
    qa, audit = tmp_path / "qa", tmp_path / "audit"
    license_manifest, mix_report = tmp_path / "bgm-license.json", tmp_path / "mix-report.json"
    video.write_bytes(b"final-video")
    yaml.write_text("projectId: demo\n", encoding="utf-8")
    source.write_text("{}", encoding="utf-8")
    qa.mkdir()
    audit.mkdir()
    (qa / "capture-manifest.json").write_text("{}", encoding="utf-8")
    (audit / "audit-report.json").write_text(json.dumps({
        "verdict": "PASS", "summary": {"FAIL": 0, "WARN": 0, "INFO": 0},
        "spec": {"width": 3840, "height": 2160, "fps": 30.0, "vcodec": "h264",
                 "acodec": "aac", "duration": 600.0, "nbFrames": 18000},
    }), encoding="utf-8")
    license_manifest.write_text(json.dumps({"licensePolicy": "strict", "assets": [{"id": "track"}]}),
                                encoding="utf-8")
    mix_report.write_text(json.dumps({"mode": "playlist"}), encoding="utf-8")
    result = package_final_delivery(
        tmp_path / "deliveries" / "demo", project_id="demo", video=video,
        project_yaml=yaml, source_manifest=source, qa_dir=qa, audit_dir=audit,
        license_manifest=license_manifest, mix_report=mix_report,
    )
    out = tmp_path / "deliveries" / "demo"
    assert (out / "demo.mp4").read_bytes() == b"final-video"
    assert (out / "qa" / "capture-manifest.json").is_file()
    assert json.loads((out / "delivery-manifest.json").read_text())["audit"]["verdict"] == "PASS"
    assert result["directory"] == str(out)


def test_final_delivery_accepts_explicit_silent_aac_without_bgm_license(tmp_path):
    video, yaml, source = tmp_path / "final.mp4", tmp_path / "project.yaml", tmp_path / "source.json"
    qa, audit, report = tmp_path / "qa", tmp_path / "audit", tmp_path / "mix.json"
    video.write_bytes(b"final-video")
    yaml.write_text("projectId: silent\n", encoding="utf-8")
    source.write_text("{}", encoding="utf-8")
    qa.mkdir(); audit.mkdir()
    (qa / "capture-manifest.json").write_text("{}", encoding="utf-8")
    (audit / "audit-report.json").write_text(json.dumps({
        "verdict": "PASS", "summary": {"FAIL": 0},
        "spec": {"width": 3840, "height": 2160, "fps": 30.0, "vcodec": "h264",
                 "acodec": "aac", "duration": 600.0, "nbFrames": 18000},
    }), encoding="utf-8")
    report.write_text(json.dumps({"mode": "off", "bgm": None, "voice": None,
                                  "settings": {"silentAudioTrack": True}}), encoding="utf-8")
    result = package_final_delivery(
        tmp_path / "deliveries" / "silent", project_id="silent", video=video,
        project_yaml=yaml, source_manifest=source, qa_dir=qa, audit_dir=audit,
        license_manifest=None, mix_report=report,
    )
    manifest = json.loads((tmp_path / "deliveries" / "silent" / "delivery-manifest.json").read_text())
    assert result["directory"].endswith("/silent")
    assert manifest["audio"] == {"mode": "off", "silentTrack": True}
    assert not (tmp_path / "deliveries" / "silent" / "bgm-license-manifest.json").exists()


def test_final_delivery_rejects_uhd_with_missing_video_frames(tmp_path):
    video, yaml, source = tmp_path / "final.mp4", tmp_path / "project.yaml", tmp_path / "source.json"
    qa, audit, report = tmp_path / "qa", tmp_path / "audit", tmp_path / "mix.json"
    video.write_bytes(b"final-video")
    yaml.write_text("projectId: short-video\n", encoding="utf-8")
    source.write_text("{}", encoding="utf-8")
    qa.mkdir(); audit.mkdir()
    (qa / "capture-manifest.json").write_text("{}", encoding="utf-8")
    (audit / "audit-report.json").write_text(json.dumps({
        "verdict": "PASS", "summary": {"FAIL": 0},
        "spec": {"width": 3840, "height": 2160, "fps": 30.0, "vcodec": "h264",
                 "acodec": "aac", "duration": 600.0, "nbFrames": 17998},
    }), encoding="utf-8")
    report.write_text(json.dumps({"mode": "off", "bgm": None, "voice": None,
                                  "settings": {"silentAudioTrack": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="18000"):
        package_final_delivery(
            tmp_path / "deliveries" / "short-video", project_id="short-video", video=video,
            project_yaml=yaml, source_manifest=source, qa_dir=qa, audit_dir=audit,
            license_manifest=None, mix_report=report,
        )
