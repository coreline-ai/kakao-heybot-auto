import json
import subprocess
from pathlib import Path

import pytest

from brushvid.bgm import (BgmAssetError, FINAL_BGM_E2E, LISTENING_REVIEW_ITEMS,
                          catalog_status, discover_downloads, final_bgm_gate,
                          import_asset, load_catalog,
                          preflight_assets, record_listening_review, sha256_file, write_dashboard,
                          write_listening_review)
from brushvid.project import BgmConfig


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "assets" / "bgm" / "catalog.schema.json"


def _tone(path: Path, seconds: float = 3.0):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency=330:duration={seconds}", "-c:a", "libmp3lame", str(path),
    ], check=True)


def _evidence(repo: Path) -> tuple[Path, Path]:
    page, license_file = repo / "page.png", repo / "license.pdf"
    page.write_bytes(b"page evidence")
    license_file.write_bytes(b"license evidence")
    return page, license_file


def _catalog(repo: Path, *, local_path="local-assets/bgm/test-track/original.mp3") -> Path:
    path = repo / "catalog.json"
    path.write_text(json.dumps({
        "schemaVersion": 1,
        "assets": [{
            "id": "test-track", "title": "Test Track", "artist": None,
            "source": "pixabay",
            "sourcePage": "https://pixabay.com/music/test-track/",
            "durationSec": 3.0, "localPath": local_path,
            "downloaded": False, "sha256": None,
            "license": {
                "name": "Pixabay Content License",
                "url": "https://pixabay.com/service/license-summary/",
                "downloadedAt": None, "checkedAt": "2026-07-12",
                "contentIdStatus": "not-displayed", "evidenceFiles": [],
            },
            "tags": ["test"], "recommendedSkills": ["brush-video"],
            "youtubeAllowed": False,
        }],
    }), encoding="utf-8")
    return path


def test_repository_catalog_schema_and_unique_ids():
    catalog = load_catalog()
    assert catalog["schemaVersion"] == 1
    asset_ids = {a["id"] for a in catalog["assets"]}
    assert len(asset_ids) == len(catalog["assets"])
    assert "youtube-jesse-gallagher-satya-yuga" in asset_ids
    assert all(asset["youtubeAllowed"] is (asset["source"] != "pixabay")
               for asset in catalog["assets"])
    assert sum(asset["youtubeAllowed"] for asset in catalog["assets"]) == 9


def test_cc_by_asset_requires_attribution_text(tmp_path):
    catalog = _catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding="utf-8"))
    entry = data["assets"][0]
    entry["source"] = "youtube-audio-library"
    entry["sourcePage"] = "https://www.chriszabriskie.com/reappear/"
    entry["license"].update({
        "name": "Creative Commons Attribution 4.0 International",
        "url": "https://creativecommons.org/licenses/by/4.0/",
        "attributionRequired": True,
        "attributionText": None,
    })
    catalog.write_text(json.dumps(data), encoding="utf-8")
    source = tmp_path / "source.mp3"
    _tone(source)
    page, license_file = _evidence(tmp_path)
    import_asset(
        "test-track", source, artist="Chris Zabriskie", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_file,
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    with pytest.raises(BgmAssetError, match="attributionText"):
        preflight_assets(
            BgmConfig(mode="asset", asset_id="test-track"),
            repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        )


def test_import_status_preflight_and_tamper(tmp_path):
    catalog = _catalog(tmp_path)
    source = tmp_path / "source.mp3"
    _tone(source)
    page, license_pdf = _evidence(tmp_path)

    result = import_asset(
        "test-track", source, artist="Test Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_pdf, repo_root=tmp_path,
        catalog_path=catalog, schema_path=SCHEMA,
    )
    target = Path(result["path"])
    assert target.is_file() and result["entry"]["sha256"] == sha256_file(target)

    rows = catalog_status(repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA)
    assert rows[0]["ok"] and rows[0]["warnings"]
    cfg = BgmConfig(mode="asset", asset_id="test-track")
    resolved = preflight_assets(cfg, repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA)
    assert resolved[0]["resolvedPath"] == str(target)

    target.write_bytes(target.read_bytes() + b"tamper")
    with pytest.raises(BgmAssetError, match="SHA-256"):
        preflight_assets(cfg, repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA)


@pytest.mark.parametrize(("distribution", "mode"), [
    ("youtube", "asset"),
    ("shorts", "playlist"),
])
def test_youtube_distributions_reject_pixabay_assets(tmp_path, distribution, mode):
    catalog = _catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding="utf-8"))
    asset_ids = ["test-track"]
    if mode == "playlist":
        second = json.loads(json.dumps(data["assets"][0]))
        second.update({
            "id": "test-track-2",
            "sourcePage": "https://pixabay.com/music/test-track-2/",
            "localPath": "local-assets/bgm/test-track-2/original.mp3",
        })
        data["assets"].append(second)
        asset_ids.append("test-track-2")
        catalog.write_text(json.dumps(data), encoding="utf-8")

    page, license_file = _evidence(tmp_path)
    for index, asset_id in enumerate(asset_ids):
        source = tmp_path / f"source-{index}.mp3"
        _tone(source)
        import_asset(
            asset_id, source, artist="Artist", content_id_status="not-displayed",
            source_evidence=page, license_evidence=license_file,
            repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        )
    cfg = (BgmConfig(mode="asset", asset_id=asset_ids[0]) if mode == "asset" else
           BgmConfig(mode="playlist", asset_ids=tuple(asset_ids), license_policy="warn"))
    with pytest.raises(BgmAssetError, match="Pixabay 음원은 YouTube/Shorts"):
        preflight_assets(
            cfg, distribution=distribution, repo_root=tmp_path,
            catalog_path=catalog, schema_path=SCHEMA,
        )


def test_youtube_distribution_accepts_allowed_source(tmp_path):
    catalog = _catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding="utf-8"))
    entry = data["assets"][0]
    entry["source"] = "youtube-audio-library"
    entry["sourcePage"] = "https://www.youtube.com/audiolibrary"
    entry["youtubeAllowed"] = True
    entry["license"]["name"] = "YouTube Audio Library license"
    catalog.write_text(json.dumps(data), encoding="utf-8")
    source = tmp_path / "source.mp3"
    _tone(source)
    page, license_file = _evidence(tmp_path)
    import_asset(
        "test-track", source, artist="Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_file,
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    resolved = preflight_assets(
        BgmConfig(mode="asset", asset_id="test-track"), distribution="youtube",
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    assert resolved[0]["source"] == "youtube-audio-library"


def test_import_rejects_missing_evidence(tmp_path):
    catalog = _catalog(tmp_path)
    source = tmp_path / "source.mp3"
    _tone(source)
    with pytest.raises(BgmAssetError, match="증빙"):
        import_asset(
            "test-track", source, artist="Artist", content_id_status="not-displayed",
            source_evidence=tmp_path / "missing.png", license_evidence=tmp_path / "missing.pdf",
            repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        )


@pytest.mark.parametrize("kind", ["empty", "corrupt", "wrong-extension", "wrong-codec"])
def test_import_rejects_invalid_mp3_inputs(tmp_path, kind):
    catalog = _catalog(tmp_path)
    page, license_file = _evidence(tmp_path)
    source = tmp_path / ("source.wav" if kind == "wrong-extension" else "source.mp3")
    if kind == "empty":
        source.write_bytes(b"")
    elif kind == "corrupt":
        source.write_bytes(b"not an mp3")
    elif kind == "wrong-extension":
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=330:duration=1", "-c:a", "pcm_s16le", str(source),
        ], check=True)
    else:
        wav = tmp_path / "source.wav"
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=330:duration=1", "-c:a", "pcm_s16le", str(wav),
        ], check=True)
        wav.replace(source)

    with pytest.raises(BgmAssetError):
        import_asset(
            "test-track", source, artist="Artist", content_id_status="not-displayed",
            source_evidence=page, license_evidence=license_file,
            repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        )


def test_import_refuses_different_file_without_explicit_replace(tmp_path):
    catalog = _catalog(tmp_path)
    page, license_file = _evidence(tmp_path)
    first, second = tmp_path / "first.mp3", tmp_path / "second.mp3"
    _tone(first)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=880:duration=3", "-c:a", "libmp3lame", str(second),
    ], check=True)
    kwargs = dict(
        artist="Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_file,
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    import_asset("test-track", first, **kwargs)
    with pytest.raises(BgmAssetError, match="--replace"):
        import_asset("test-track", second, **kwargs)


@pytest.mark.parametrize("field", [
    "source-page", "artist", "download-date", "evidence", "content-id",
])
def test_strict_preflight_rejects_missing_or_unsafe_metadata(tmp_path, field):
    catalog = _catalog(tmp_path)
    page, license_file = _evidence(tmp_path)
    source = tmp_path / "source.mp3"
    _tone(source)
    import_asset(
        "test-track", source, artist="Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_file,
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    data = json.loads(catalog.read_text(encoding="utf-8"))
    entry = data["assets"][0]
    if field == "source-page":
        entry["sourcePage"] = ""
    elif field == "artist":
        entry["artist"] = None
    elif field == "download-date":
        entry["license"]["downloadedAt"] = None
    elif field == "evidence":
        entry["license"]["evidenceFiles"] = []
    else:
        entry["license"]["contentIdStatus"] = "unknown"
    catalog.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BgmAssetError):
        preflight_assets(
            BgmConfig(mode="asset", asset_id="test-track"),
            repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        )


def test_local_path_escape_rejected(tmp_path):
    catalog = _catalog(tmp_path, local_path="local-assets/bgm/../../outside.mp3")
    cfg = BgmConfig(mode="asset", asset_id="test-track")
    with pytest.raises(BgmAssetError, match="밖을 가리킴"):
        preflight_assets(cfg, repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA)


def test_dashboard_shows_progress_links_and_local_player(tmp_path):
    catalog = _catalog(tmp_path)
    dashboard = write_dashboard(
        tmp_path / "local-assets" / "bgm" / "index.html",
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    pending = dashboard.read_text(encoding="utf-8")
    assert "0 / 1 ready" in pending
    assert "https://pixabay.com/music/test-track/" in pending
    assert "<audio controls" not in pending

    source = tmp_path / "source.mp3"
    _tone(source)
    page, license_pdf = tmp_path / "page.png", tmp_path / "license.pdf"
    page.write_bytes(b"page")
    license_pdf.write_bytes(b"license")
    import_asset(
        "test-track", source, artist="Test Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_pdf, repo_root=tmp_path,
        catalog_path=catalog, schema_path=SCHEMA,
    )
    write_dashboard(dashboard, repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA)
    ready = dashboard.read_text(encoding="utf-8")
    assert "1 / 1 ready" in ready
    assert '<audio controls preload="metadata"' in ready
    assert "test-track/original.mp3" in ready


def test_listening_review_contains_all_demos_and_local_export(tmp_path):
    first_video = tmp_path / "output" / "bgm-gain-ab-demo.mp4"
    first_video.parent.mkdir(parents=True)
    first_video.write_bytes(b"video fixture")
    audit = tmp_path / "data" / "bgm-gain-ab-demo" / "audit" / "audit-report.md"
    audit.parent.mkdir(parents=True)
    audit.write_text("PASS", encoding="utf-8")
    out = write_listening_review(
        tmp_path / "local-assets" / "bgm" / "listening-review.html",
        repo_root=tmp_path,
    )
    page = out.read_text(encoding="utf-8")
    assert page.count('class="review"') == 5
    assert "기존 음량 vs +4/+5/+6dB" in page
    assert '<video controls preload="metadata"' in page
    assert "이어폰 승인" in page and "노트북 스피커 승인" in page
    assert "bgm-listening-review.json" in page and "localStorage" in page


def test_listening_review_import_requires_both_environments_and_all_pass(tmp_path):
    ids = [
        "gain-ab", "gentle-brush", "dreamcloud-pen-brush",
        "quiet-narration", "gentle-dreamcloud-playlist",
    ]
    for _item_id, _title, video_rel, _audit_rel in LISTENING_REVIEW_ITEMS:
        video = tmp_path / video_rel
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
    source = tmp_path / "review.json"
    items = {item_id: {"earbuds": True, "laptop": True, "decision": "pass", "notes": "ok"}
             for item_id in ids}
    source.write_text(json.dumps({
        "schemaVersion": 1, "exportedAt": "2026-07-12T06:00:00Z", "items": items,
    }), encoding="utf-8")
    out = tmp_path / "approval.json"
    result = record_listening_review(source, out, repo_root=tmp_path)
    assert result["approved"] is True and out.is_file()
    assert result["sourceSha256"] == sha256_file(source)

    items["gain-ab"]["laptop"] = False
    source.write_text(json.dumps({"schemaVersion": 1, "items": items}), encoding="utf-8")
    with pytest.raises(BgmAssetError, match="청취 승인 미완료"):
        record_listening_review(source, out, repo_root=tmp_path)


def test_final_gate_requires_assets_e2e_manifests_and_human_approval(tmp_path):
    catalog = _catalog(tmp_path)
    page, license_file = _evidence(tmp_path)
    source_audio = tmp_path / "source.mp3"
    _tone(source_audio)
    import_asset(
        "test-track", source_audio, artist="Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_file,
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
    )
    for project_id in FINAL_BGM_E2E:
        video = tmp_path / "output" / f"{project_id}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        audit = tmp_path / "data" / project_id / "audit" / "audit-report.json"
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
        manifest = tmp_path / "data" / project_id / "licenses" / "bgm-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}", encoding="utf-8")
    for _item_id, _title, video_rel, _audit_rel in LISTENING_REVIEW_ITEMS:
        video = tmp_path / video_rel
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
    items = {item_id: {"earbuds": True, "laptop": True, "decision": "pass", "notes": "ok"}
             for item_id, *_rest in LISTENING_REVIEW_ITEMS}
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"schemaVersion": 1, "items": items}), encoding="utf-8")
    approval = tmp_path / "local-assets" / "bgm" / "listening-approval.json"
    record_listening_review(review, approval, repo_root=tmp_path)

    result = final_bgm_gate(
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        out_path=tmp_path / "final-gate.json",
    )
    assert result["passed"] is True
    assert result["summary"] == {
        "assetsReady": 1, "assetsTotal": 1, "e2ePassed": 4,
        "e2eTotal": 4, "humanApproval": True,
    }

    (tmp_path / "output" / "narration-local-bgm-demo.mp4").unlink()
    failed = final_bgm_gate(
        repo_root=tmp_path, catalog_path=catalog, schema_path=SCHEMA,
        out_path=tmp_path / "final-gate.json",
    )
    assert failed["passed"] is False and failed["summary"]["e2ePassed"] == 3


def test_import_reuses_evidence_already_in_asset_directory(tmp_path):
    catalog = _catalog(tmp_path)
    source = tmp_path / "source.mp3"
    _tone(source)
    evidence = tmp_path / "local-assets" / "bgm" / "test-track" / "evidence"
    evidence.mkdir(parents=True)
    page, license_png = evidence / "source-page.png", evidence / "license.png"
    page.write_bytes(b"page")
    license_png.write_bytes(b"license")
    result = import_asset(
        "test-track", source, artist="Test Artist", content_id_status="not-displayed",
        source_evidence=page, license_evidence=license_png, repo_root=tmp_path,
        catalog_path=catalog, schema_path=SCHEMA,
    )
    assert Path(result["path"]).is_file()
    assert page.read_bytes() == b"page" and license_png.read_bytes() == b"license"


def test_discover_downloads_matches_official_slug_with_artist_prefix(tmp_path):
    catalog = _catalog(tmp_path)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    exact = downloads / "artist-test-track.mp3"
    exact.write_bytes(b"audio")
    (downloads / "unrelated-track.mp3").write_bytes(b"other")
    (downloads / "artist-test-track-remix.mp3").write_bytes(b"false positive")
    found = discover_downloads([downloads], catalog_path=catalog, schema_path=SCHEMA)
    assert len(found) == 1
    assert found[0]["sourceSlug"] == "test-track"
    assert found[0]["candidates"] == [str(exact)]


def test_discover_downloads_accepts_pixabay_category_prefix_omitted_from_mp3(tmp_path):
    catalog = _catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding="utf-8"))
    data["assets"][0]["sourcePage"] = (
        "https://pixabay.com/music/meditationspiritual-test-track-1234/"
    )
    catalog.write_text(json.dumps(data), encoding="utf-8")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    exact = downloads / "natureseye-test-track-1234.mp3"
    exact.write_bytes(b"audio")
    found = discover_downloads([downloads], catalog_path=catalog, schema_path=SCHEMA)
    assert found[0]["candidates"] == [str(exact)]
    assert "test-track-1234" in found[0]["slugAliases"]
