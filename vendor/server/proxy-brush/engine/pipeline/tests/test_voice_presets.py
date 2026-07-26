"""여성 음성팩 catalog, alias, style blend, preview hard gate 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from brushvid.voice_presets import (
    CATALOG_PATH,
    CATALOG_SCHEMA_PATH,
    VoicePresetError,
    build_voice_style,
    load_catalog,
    resolve_voice,
    supported_voice_ids,
    validate_preview_assets,
    write_catalog_html,
)


class FakeStyle:
    def __init__(self, ttl, dp):
        self.ttl = np.asarray(ttl, dtype=np.float32)
        self.dp = np.asarray(dp, dtype=np.float32)


class FakeTTS:
    def __init__(self):
        self.styles = {
            f"F{i}": FakeStyle(np.full((1, 2, 2), i), np.full((1, 2, 1), i * 10))
            for i in range(1, 6)
        }
        self.styles.update({
            f"M{i}": FakeStyle(np.full((1, 2, 2), 10 + i), np.full((1, 2, 1), 100 + i))
            for i in range(1, 6)
        })

    def get_voice_style(self, name):
        return self.styles[name]


def _write_catalog(tmp_path: Path, mutate) -> Path:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    mutate(data)
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out


def test_catalog_has_exact_ten_canonical_ids_and_native_compatibility():
    catalog = load_catalog()
    assert [v["id"] for v in catalog["voices"]] == [f"female-{i:02d}" for i in range(1, 11)]
    assert catalog["voicePackVersion"] == "1.0.0"
    supported = supported_voice_ids(catalog)
    assert "female-01" in supported and "female-10" in supported
    assert all(name in supported for name in ("F1", "F5", "M1", "M5"))


def test_catalog_rejects_component_sum_not_one(tmp_path):
    path = _write_catalog(tmp_path, lambda d: d["voices"][5].update(
        {"components": {"F1": 0.6, "F3": 0.3}}
    ))
    with pytest.raises(VoicePresetError, match="비율 합"):
        load_catalog(path, CATALOG_SCHEMA_PATH)


def test_catalog_rejects_male_component(tmp_path):
    path = _write_catalog(tmp_path, lambda d: d["voices"][5].update(
        {"components": {"F1": 0.6, "M1": 0.4}}
    ))
    with pytest.raises(VoicePresetError, match="catalog 검증 실패"):
        load_catalog(path, CATALOG_SCHEMA_PATH)


def test_female_aliases_and_native_male_resolution():
    assert resolve_voice("F1")["voicePresetId"] == "female-01"
    assert resolve_voice("F5")["voicePresetId"] == "female-05"
    male = resolve_voice("M1")
    assert male["voicePresetId"] == "M1"
    assert male["kind"] == "native"
    assert male["components"] == {"M1": 1.0}


@pytest.mark.parametrize("voice", ["", "F6", "female-11", "female-1", "m1"])
def test_unknown_voice_fails_without_f1_fallback(voice):
    with pytest.raises(VoicePresetError, match="voice|문자열"):
        resolve_voice(voice)


def test_female_09_blend_is_deterministic_and_female_only():
    tts = FakeTTS()
    first, meta1 = build_voice_style(tts, "female-09", style_factory=FakeStyle)
    second, meta2 = build_voice_style(tts, "female-09", style_factory=FakeStyle)
    assert meta1["components"] == {"F4": 0.65, "F1": 0.35}
    assert all(name.startswith("F") for name in meta1["components"])
    assert np.allclose(first.ttl, 4 * 0.65 + 1 * 0.35)
    assert np.allclose(first.dp, 40 * 0.65 + 10 * 0.35)
    assert np.array_equal(first.ttl, second.ttl)
    assert meta1["styleSha256"] == meta2["styleSha256"]


def test_builtin_alias_and_canonical_style_are_identical():
    tts = FakeTTS()
    alias_style, alias_meta = build_voice_style(tts, "F1", style_factory=FakeStyle)
    canonical_style, canonical_meta = build_voice_style(tts, "female-01", style_factory=FakeStyle)
    assert np.array_equal(alias_style.ttl, canonical_style.ttl)
    assert alias_meta["voicePresetId"] == canonical_meta["voicePresetId"] == "female-01"


def test_model_dir_requires_style_file(tmp_path):
    tts = FakeTTS()
    tts.model_dir = tmp_path
    with pytest.raises(VoicePresetError, match="style 파일 없음"):
        build_voice_style(tts, "female-01", style_factory=FakeStyle)


def test_style_hash_drift_is_rejected(tmp_path):
    tts = FakeTTS()
    tts.model_dir = tmp_path
    styles = tmp_path / "voice_styles"
    styles.mkdir()
    (styles / "F1.json").write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(VoicePresetError, match="style drift"):
        build_voice_style(tts, "female-01", style_factory=FakeStyle)


def test_all_preview_assets_pass_format_hash_and_acoustic_gate():
    rows = validate_preview_assets()
    assert len(rows) == 10
    assert all(row["ok"] for row in rows)


def test_catalog_html_has_ten_audio_controls_and_explicit_ids(tmp_path):
    page = write_catalog_html(tmp_path / "index.html")
    text = page.read_text(encoding="utf-8")
    assert text.count("<audio controls") == 10
    assert "female-01" in text and "female-10" in text
    assert "Supertonic AI 합성 음성" in text
