from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import brushvid.fill_routes as fill_routes
from brushvid.fill_routes import generate_fill_routes
from brushvid.layers import prepare_pen_brush_layers


def _illustration(path: Path, size=(240, 140)) -> None:
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    d.ellipse((30, 28, 105, 110), fill="#d79a67", outline="#292724", width=2)
    d.rectangle((128, 38, 210, 104), fill="#63a9b2", outline="#292724", width=2)
    d.line((8, 124, 230, 124), fill="#33302c", width=2)
    im.save(path)


def test_prepare_layers_keeps_outline_thin_and_color_separate(tmp_path: Path):
    src = tmp_path / "src.png"
    _illustration(src)
    result = prepare_pen_brush_layers(
        src, tmp_path / "outline.png", tmp_path / "outline-flat.png", tmp_path / "color.png",
        size=(240, 140),
    )
    outline = np.asarray(Image.open(result["outline"]).convert("RGBA"))
    color = np.asarray(Image.open(result["color"]).convert("RGBA"))
    assert result["lineThickness"] < 4.0
    assert outline[..., 3].max() > 0
    assert color[..., 3].max() > 0
    # outline RGB는 원본 채색을 들고 있지 않고 중성 charcoal만 가진다.
    visible_rgb = outline[..., :3][outline[..., 3] > 0]
    assert np.unique(visible_rgb.reshape(-1, 3), axis=0).shape[0] == 1


def test_prepare_layers_supports_saturated_dark_ink_outlines(tmp_path: Path):
    """짙은 적갈색 잉크선도 채색 경계가 아닌 선화로 분리한다."""
    src = tmp_path / "saturated-ink.png"
    im = Image.new("RGB", (240, 140), "white")
    d = ImageDraw.Draw(im)
    # RGB(96, 5, 0)는 저채도 neutral-ink 기준을 의도적으로 벗어난다.
    d.ellipse((30, 28, 105, 110), fill="#df7b35", outline="#600500", width=3)
    d.rectangle((128, 38, 210, 104), fill="#d8a437", outline="#600500", width=3)
    im.save(src)

    result = prepare_pen_brush_layers(
        src, tmp_path / "outline.png", tmp_path / "outline-flat.png", tmp_path / "color.png",
        size=(240, 140),
    )

    outline = np.asarray(Image.open(result["outline"]).convert("RGBA"))
    assert result["outlineFraction"] >= 0.00005
    assert 0 < result["lineThickness"] < 4.0
    assert outline[..., 3].max() > 0


def test_prepare_layers_rejects_blank(tmp_path: Path):
    src = tmp_path / "blank.png"
    Image.new("RGB", (120, 80), "white").save(src)
    with pytest.raises(ValueError, match="빈 이미지"):
        prepare_pen_brush_layers(src, tmp_path / "o.png", tmp_path / "of.png", tmp_path / "c.png",
                                 size=(120, 80))


def test_prepare_layers_rejects_full_bleed_and_low_contrast(tmp_path: Path):
    full = tmp_path / "full.png"
    Image.new("RGB", (120, 80), "#268bd2").save(full)
    with pytest.raises(ValueError, match="full-bleed"):
        prepare_pen_brush_layers(full, tmp_path / "o.png", tmp_path / "of.png", tmp_path / "c.png",
                                 size=(120, 80))

    low = tmp_path / "low.png"
    im = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(im).line((20, 40, 100, 40), fill="#f4f4f4", width=1)
    im.save(low)
    with pytest.raises(ValueError):
        prepare_pen_brush_layers(low, tmp_path / "lo.png", tmp_path / "lof.png", tmp_path / "lc.png",
                                 size=(120, 80))

    micro = tmp_path / "micro.png"
    im = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(im).rectangle((60, 40, 61, 41), fill="black")
    im.save(micro)
    with pytest.raises(ValueError, match="빈 이미지"):
        prepare_pen_brush_layers(micro, tmp_path / "mo.png", tmp_path / "mof.png", tmp_path / "mc.png",
                                 size=(120, 80))


def test_prepare_layers_allows_opt_in_full_bleed_pen_brush(tmp_path: Path):
    """풀블리드 쇼츠는 여백 없이 전체 원본을 brush 마스크로 사용한다."""
    src = tmp_path / "full-illustration.png"
    im = Image.new("RGB", (160, 100), "#65adc3")
    d = ImageDraw.Draw(im)
    d.ellipse((28, 20, 118, 85), fill="#f3bd6b", outline="#292724", width=3)
    d.line((4, 92, 156, 92), fill="#292724", width=3)
    im.save(src)
    result = prepare_pen_brush_layers(
        src, tmp_path / "o.png", tmp_path / "of.png", tmp_path / "c.png",
        size=(160, 100), allow_full_bleed=True,
    )
    color = np.asarray(Image.open(result["color"]).convert("RGBA"))
    assert result["fullBleed"] is True
    assert result["contentFraction"] == 1.0
    assert color[..., 3].min() == 255
    assert result["outlineFraction"] > 0.00005


@pytest.mark.parametrize("size", [(240, 140), (140, 240)])
def test_fill_routes_are_general_and_complete(tmp_path: Path, size: tuple[int, int]):
    w, h = size
    rgba = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(rgba)
    d.ellipse((w * .1, h * .15, w * .55, h * .8), fill=(220, 120, 60, 255))
    d.rectangle((w * .58, h * .25, w * .9, h * .75), fill=(60, 160, 190, 255))
    path = tmp_path / f"color-{w}x{h}.png"
    rgba.save(path)
    result = generate_fill_routes(path, image_rel="demo/color.png", duration=300,
                                  draw_start=100, draw_end=270)
    assert result["meta"]["width"] == w
    assert result["meta"]["height"] == h
    assert result["meta"]["coverage"] >= 0.9999
    assert result["meta"]["missingPixels"] == 0
    assert result["strokes"][0]["start"] == 100
    assert result["strokes"][-1]["end"] == 270


def test_fill_routes_are_seeded_freehand_swooshes_but_reproducible(tmp_path: Path):
    src = tmp_path / "src.png"
    _illustration(src, size=(320, 220))
    layers = prepare_pen_brush_layers(
        src, tmp_path / "outline.png", tmp_path / "outline-flat.png", tmp_path / "color.png",
        size=(320, 220),
    )

    same_a = generate_fill_routes(layers["color"], image_rel="demo/color.png", duration=300,
                                  draw_start=100, draw_end=270, seed=17)
    same_b = generate_fill_routes(layers["color"], image_rel="demo/color.png", duration=300,
                                  draw_start=100, draw_end=270, seed=17)
    other = generate_fill_routes(layers["color"], image_rel="demo/color.png", duration=300,
                                 draw_start=100, draw_end=270, seed=29)

    assert same_a["meta"]["routeStyle"] == "seeded-freehand-swoosh"
    assert same_a["meta"]["seed"] == 17
    assert same_a["strokes"] == same_b["strokes"]
    assert same_a["strokes"] != other["strokes"]
    fills = [stroke for stroke in same_a["strokes"] if stroke["kind"] == "fill"]
    assert all(len(stroke["points"]) == 11 for stroke in fills)
    # 수평 왕복 스캔이 아니라 모두 대각 또는 세로 방향의 쓱쓱 붓질이다.
    assert all(abs(stroke["points"][-1][1] - stroke["points"][0][1])
               >= abs(stroke["points"][-1][0] - stroke["points"][0][0]) * 0.45
               for stroke in fills)
    for data in (same_a, other):
        assert data["meta"]["coverage"] >= 0.9999
        assert data["meta"]["missingPixels"] == 0


def test_fill_routes_seal_sparse_residual_components(tmp_path: Path, monkeypatch):
    """자유 붓길이 모두 빗나가도 작은 alpha 섬은 route로 정확히 마감한다."""
    src = tmp_path / "sparse.png"
    rgba = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    for x, y in ((12, 15), (58, 37), (101, 61)):
        rgba.putpixel((x, y), (210, 110, 55, 255))
    rgba.save(src)

    monkeypatch.setattr(
        fill_routes,
        "_freehand_swoosh",
        lambda *_args, **_kwargs: [[-1000.0, -1000.0], [-999.0, -999.0]],
    )
    result = fill_routes.generate_fill_routes(
        src, image_rel="demo/sparse.png", duration=300, draw_start=100, draw_end=270, seed=7,
    )

    seals = [stroke for stroke in result["strokes"] if stroke["kind"] == "fill-touchup-seal"]
    assert result["meta"]["missingPixels"] == 0
    assert result["meta"]["componentSealCount"] == 3
    assert len(seals) == 3
    assert all(len(stroke["points"]) == 2 and stroke["points"][0] != stroke["points"][1]
               for stroke in seals)
