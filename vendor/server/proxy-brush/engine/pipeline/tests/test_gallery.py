"""qa.build_gallery 테스트 — fixture 기반 씬 카드/체크박스/경고 검증."""
import json

from PIL import Image

from brushvid.qa import build_gallery


def _fixture(tmp_path, scenes=2, captures=3):
    """props + qa 디렉토리(manifest/캡처 PNG) fixture."""
    props = {
        "schemaVersion": 1, "projectId": "gal-demo",
        "brush": {"kind": "pen", "w": 140},
        "scenes": [
            {"id": f"scene-{i + 1:02d}", "durationInFrames": 300,
             "cues": [{"text": f"자막 {i + 1}", "from": 30, "to": 200}],
             **({"topTitle": {"lines": ["제목"]}} if i == 0 else {})}
            for i in range(scenes)
        ],
    }
    props_path = tmp_path / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    entries = []
    for j in range(captures):
        frame = j * 250  # 0, 250(씬1), 500(씬2)
        name = f"frame-{frame:05d}.png"
        Image.new("RGB", (64, 36), (200, 100 * j % 255, 60)).save(qa_dir / name)
        entries.append({"frame": frame, "file": name, "label": f"cap-{j}"})
    (qa_dir / "capture-manifest.json").write_text(
        json.dumps({"projectId": "gal-demo", "captures": entries}), encoding="utf-8")
    return props_path, qa_dir


def test_gallery_cards_and_checkboxes(tmp_path):
    """씬 2개 → 카드 2개, 씬당 체크박스 5종, 복사 버튼/라이트박스/상대경로."""
    props_path, qa_dir = _fixture(tmp_path)
    out = build_gallery(props_path, qa_dir)
    html = out.read_text(encoding="utf-8")
    assert out.name == "gallery.html"
    assert html.count('class="card"') == 2
    assert html.count("data-kind=") == 10  # 5종 × 2씬
    for kind in ("drawing", "subtitle", "title", "widget", "audio"):
        assert f'data-kind="{kind}"' in html
    assert "copyFixRequest" in html and "openLightbox" in html
    assert 'src="frame-00000.png"' in html  # 상대경로
    assert "/Users/" not in html            # 절대경로 금지 (폴더 이동 가능)
    assert ">pen</span>" in html             # 프로파일 뱃지


def test_gallery_empty_scenes_warning(tmp_path):
    """씬 0개 → 크래시 없이 경고 카드."""
    props_path = tmp_path / "props.json"
    props_path.write_text(json.dumps({"projectId": "empty", "scenes": []}), encoding="utf-8")
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    html = build_gallery(props_path, qa_dir).read_text(encoding="utf-8")
    assert 'class="warncard"' in html
    assert "씬이 없습니다" in html


def test_gallery_missing_manifest_and_capture(tmp_path):
    """manifest 없음 / 캡처 파일 누락 → 경고 표시, 크래시 금지."""
    props_path, qa_dir = _fixture(tmp_path)
    (qa_dir / "frame-00000.png").unlink()          # 캡처 파일 하나 삭제
    html = build_gallery(props_path, qa_dir).read_text(encoding="utf-8")
    assert "캡처 파일 누락" in html

    (qa_dir / "capture-manifest.json").unlink()    # manifest 자체 삭제
    html2 = build_gallery(props_path, qa_dir).read_text(encoding="utf-8")
    assert "capture-manifest.json" in html2 and 'class="warncard"' in html2


def test_motion_props_are_labeled_as_full_color_motion_in_gallery(tmp_path):
    props_path, qa_dir = _fixture(tmp_path, scenes=1, captures=1)
    props = json.loads(props_path.read_text(encoding="utf-8"))
    props["scenes"][0]["movement"] = "push-in"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    html = build_gallery(props_path, qa_dir).read_text(encoding="utf-8")
    assert ">full-color-motion</span>" in html
