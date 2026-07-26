"""background.py 테스트 — TC-3.E3(imagegen 폴백) + clean/user-images."""
import logging

import numpy as np
import pytest
from PIL import Image

import brushvid.background as background
from brushvid.background import PAPER, clean, compose_dark_rgba, generate


def test_tc_3_e3_imagegen_fallback_to_preset(tmp_path, caplog):
    """TC-3.E3: codex exec 부재 → preset 폴백 + 경고 로그."""
    out = tmp_path / "bg.png"
    with caplog.at_level(logging.WARNING, logger="brushvid.background"):
        res = generate("imagegen", out, subject="winter pine", seed=3,
                       codex_bin=str(tmp_path / "no-such-codex"))
    assert res["strategy"] == "preset"
    assert out.is_file()
    img = Image.open(out)
    assert img.size == (1920, 1080)
    assert any("폴백" in r.message for r in caplog.records)


def test_imagegen_required_fails_when_codex_is_unavailable(tmp_path):
    """명시적 fail 계약은 엔진의 legacy preset 폴백을 허용하지 않는다."""
    with pytest.raises(RuntimeError, match="DRAW_IMAGEGEN_REQUIRED_UNAVAILABLE"):
        generate("imagegen", tmp_path / "bg.png", codex_bin=str(tmp_path / "no-such-codex"),
                 imagegen_fallback="fail")


def test_imagegen_required_fails_when_generation_subprocess_errors(tmp_path, monkeypatch):
    """Codex는 존재하지만 imagegen 실행이 실패한 경우도 preset으로 대체하지 않는다."""
    codex = tmp_path / "codex"
    codex.write_text("placeholder", encoding="utf-8")

    def fail_imagegen(*_args, **_kwargs):
        raise RuntimeError("simulated imagegen failure")

    monkeypatch.setattr(background, "_gen_imagegen", fail_imagegen)
    with pytest.raises(RuntimeError, match="DRAW_IMAGEGEN_REQUIRED_FAILED"):
        generate("imagegen", tmp_path / "bg.png", codex_bin=str(codex), imagegen_fallback="fail")


def test_preset_deterministic(tmp_path):
    """같은 시드의 preset 배경은 결정적(바이트 동일 픽셀)이다."""
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    generate("preset", a, seed=42)
    generate("preset", b, seed=42)
    assert np.array_equal(np.asarray(Image.open(a)), np.asarray(Image.open(b)))


def test_user_images_contain_fit(tmp_path):
    """user-images: 종이 캔버스에 contain-fit 배치, 1920×1080 출력."""
    src = tmp_path / "src.png"
    Image.new("RGB", (640, 640), (30, 60, 120)).save(src)
    out = tmp_path / "bg.png"
    res = generate("user-images", out, images=[src])
    assert res["strategy"] == "user-images"
    img = Image.open(out)
    assert img.size == (1920, 1080)
    arr = np.asarray(img)
    assert (arr[0, 0] == PAPER).all()  # 모서리는 종이색


def test_user_images_cover_full_bleed(tmp_path):
    """cover: 여백 없이 캔버스를 채우고 중앙 크롭한다."""
    src = tmp_path / "src-cover.png"
    image = Image.new("RGB", (640, 640), (30, 60, 120))
    image.save(src)
    out = tmp_path / "cover.png"
    res = generate("user-images", out, images=[src], fit="cover")
    assert res["fit"] == "cover"
    arr = np.asarray(Image.open(out))
    assert arr.shape == (1080, 1920, 3)
    assert (arr[0, 0] == (30, 60, 120)).all()
    assert (arr[-1, -1] == (30, 60, 120)).all()


def test_dark_rgba_preserves_transparency(tmp_path):
    """우주 source의 투명 픽셀에 숨은 RGB가 불투명 색으로 노출되지 않는다."""
    src = tmp_path / "rgba.png"
    arr = np.zeros((90, 160, 4), dtype=np.uint8)
    arr[..., :3] = (0, 80, 255)  # alpha=0이어도 RGB가 든 imagegen 유형
    arr[30:60, 50:110] = (255, 180, 40, 255)
    Image.fromarray(arr, "RGBA").save(src)
    out = compose_dark_rgba(src, tmp_path / "out.png", size=(160, 90), fit="cover")
    result = np.asarray(Image.open(out).convert("RGBA"))
    assert tuple(result[0, 0]) == (0, 0, 0, 0)
    assert tuple(result[45, 80]) == (255, 180, 40, 255)


def test_clean_replaces_near_paper(tmp_path):
    """clean: near-paper(밝고 채도 낮은) 픽셀이 종이색으로 치환된다."""
    img = Image.new("RGB", (200, 100), (243, 243, 243))  # 모눈 격자 톤
    for x in range(50):
        for y in range(100):
            img.putpixel((x, y), (60, 40, 30))  # 잉크
    src = tmp_path / "src.png"
    img.save(src)
    out = clean(src, tmp_path / "clean.png")
    arr = np.asarray(Image.open(out))
    assert (arr[50, 150] == PAPER).all()   # 격자 톤 → 종이색
    assert (arr[50, 10] == (60, 40, 30)).all()  # 잉크는 유지
