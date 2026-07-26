"""promo-widgets 카탈로그 정합 테스트 — external_samples/implement_20260718_212631.md Phase 2.

카탈로그(assets/promo-widgets/catalog.json)가 자산의 색인으로 신뢰 가능함을 보증한다:
JSON Schema 유효성, 참조 파일 실재, TS registry/schema와 id 집합 일치, demo type 정합,
갤러리가 카탈로그를 단일 근원으로 사용하는지(하드코딩 회귀 방지).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "assets" / "promo-widgets" / "catalog.json"
SCHEMA_PATH = ROOT / "assets" / "promo-widgets" / "catalog.schema.json"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_validates_against_schema(catalog: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(catalog, schema)


def test_referenced_files_exist(catalog: dict) -> None:
    for key in ("origin", "designTokens", "widgetSchema", "registry"):
        assert (ROOT / catalog[key]).is_file(), f"{key}: {catalog[key]} 없음"
    for entry in catalog["widgets"]:
        assert (ROOT / entry["component"]).is_file(), f"component 없음: {entry['component']}"


def test_widget_ids_unique(catalog: dict) -> None:
    ids = [entry["id"] for entry in catalog["widgets"]]
    assert len(ids) == len(set(ids))


def _registry_ids() -> set[str]:
    src = (ROOT / "src" / "promo" / "registry.tsx").read_text(encoding="utf-8")
    block = re.search(r"PROMO_WIDGET_REGISTRY[^{]*\{(.*?)\}", src, re.S)
    assert block, "registry 매핑 블록을 찾지 못함"
    return set(re.findall(r"^\s*(\w+):\s*body\(", block.group(1), re.M))


def _schema_type_literals() -> set[str]:
    src = (ROOT / "src" / "promo" / "schema.ts").read_text(encoding="utf-8")
    return set(re.findall(r'type:\s*z\.literal\("(\w+)"\)', src))


def test_ids_match_registry_and_schema(catalog: dict) -> None:
    catalog_ids = {entry["id"] for entry in catalog["widgets"]}
    assert catalog_ids == _registry_ids(), "catalog ↔ registry id 불일치"
    assert catalog_ids == _schema_type_literals(), "catalog ↔ PromoWidgetSchema type 불일치"


def test_demo_types_match_parent_id(catalog: dict) -> None:
    for entry in catalog["widgets"]:
        for demo in entry["demos"]:
            assert demo["type"] == entry["id"], f"{entry['id']}의 demo에 type={demo['type']}"


def test_gallery_uses_catalog_as_single_source() -> None:
    src = (ROOT / "src" / "promo" / "PromoWidgetGallery.tsx").read_text(encoding="utf-8")
    assert "assets/promo-widgets/catalog.json" in src, "갤러리가 카탈로그를 import하지 않음"
    assert "catalog.widgets.flatMap" in src, "갤러리 데모가 카탈로그에서 파생되지 않음 (하드코딩 회귀)"
