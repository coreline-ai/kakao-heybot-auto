from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "bin" / "camera-prompt-catalog.py"
SKILLS_ROOT = ROOT.parent / "skills"
CATALOG = SKILLS_ROOT / "_shared" / "references" / "camera-prompt-catalog.json"
SCHEMA = SKILLS_ROOT / "_shared" / "references" / "camera-prompt-catalog.schema.json"
FIXTURES = ROOT / "pipeline" / "tests" / "fixtures" / "camera-intent-cases.json"
EXPECTED_NUMBERS = set(range(1, 37)) | {46}
EXPECTED_ALIASES = {37: 28, 38: 29, 39: 30, 40: 31, 41: 32, 42: 33, 43: 34, 44: 35, 45: 36}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run_with_catalog(tmp_path: Path, payload: dict, command: str = "validate") -> subprocess.CompletedProcess[str]:
    path = tmp_path / "catalog.json"
    write_json(path, payload)
    return run_cli("--catalog", str(path), command)


def run_with_fixtures(tmp_path: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "fixtures.json"
    write_json(path, payload)
    return run_cli("--fixtures", str(path), "check")


def test_catalog_has_exact_canonical_and_alias_sets() -> None:
    catalog = load(CATALOG)
    assert {item["canonicalNo"] for item in catalog["techniques"]} == EXPECTED_NUMBERS
    assert {item["legacyNo"]: item["canonicalNo"] for item in catalog["aliases"]} == EXPECTED_ALIASES


def test_validate_command_passes() -> None:
    result = run_cli("validate")
    assert result.returncode == 0, result.stderr
    assert "canonical=37" in result.stdout
    assert "aliases=9" in result.stdout


def test_list_is_deterministic_and_includes_aliases() -> None:
    first = run_cli("list")
    second = run_cli("list")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert "canonical=37, aliases=9" in first.stdout
    assert "object-pass-through" in first.stdout


def test_check_command_validates_fixture_coverage_and_docs() -> None:
    result = run_cli("check")
    assert result.returncode == 0, result.stderr
    assert "fixtures=96" in result.stdout
    assert "edge=12" in result.stdout


def test_missing_canonical_number_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["techniques"].pop()
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "schema violation" in result.stderr


def test_duplicate_canonical_id_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["techniques"][1]["id"] = catalog["techniques"][0]["id"]
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "duplicate id" in result.stderr


def test_wrong_alias_mapping_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["aliases"][0]["canonicalNo"] = 29
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "legacy alias mapping mismatch" in result.stderr


def test_missing_alias_back_reference_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    canonical = next(item for item in catalog["techniques"] if item["canonicalNo"] == 28)
    canonical["aliases"] = []
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "back-reference missing" in result.stderr


def test_alias_description_drift_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["aliases"][0]["nameEn"] = "Different Motion"
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "description drift" in result.stderr


def test_unknown_conflict_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["techniques"][0]["conflictsWith"] = ["missing-technique"]
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "unknown conflict" in result.stderr


def test_incomplete_target_compatibility_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    del catalog["techniques"][0]["compatibility"]["aiVideo"]
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "schema violation" in result.stderr


def test_invalid_compatibility_level_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["techniques"][0]["compatibility"]["aiVideo"] = "magic"
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "schema violation" in result.stderr


def test_less_than_two_natural_language_examples_fails(tmp_path: Path) -> None:
    catalog = load(CATALOG)
    catalog["techniques"][0]["naturalLanguage"] = ["고정해줘"]
    result = run_with_catalog(tmp_path, catalog)
    assert result.returncode == 1
    assert "schema violation" in result.stderr


def test_fixture_total_below_95_fails(tmp_path: Path) -> None:
    fixtures = load(FIXTURES)
    fixtures["cases"] = fixtures["cases"][:94]
    result = run_with_fixtures(tmp_path, fixtures)
    assert result.returncode == 1
    assert "coverage below 95" in result.stderr


def test_fixture_unknown_canonical_fails(tmp_path: Path) -> None:
    fixtures = load(FIXTURES)
    fixtures["cases"][0]["expectedCanonicalId"] = "not-real"
    result = run_with_fixtures(tmp_path, fixtures)
    assert result.returncode == 1
    assert "unknown canonical id" in result.stderr


def test_fixture_required_slots_drift_fails(tmp_path: Path) -> None:
    fixtures = load(FIXTURES)
    fixtures["cases"][0]["requiredSlots"] = ["subject"]
    result = run_with_fixtures(tmp_path, fixtures)
    assert result.returncode == 1
    assert "requiredSlots drift" in result.stderr


def test_fixture_alias_mapping_fails(tmp_path: Path) -> None:
    fixtures = load(FIXTURES)
    alias = next(case for case in fixtures["cases"] if case["kind"] == "alias")
    alias["legacyNo"] = 45
    result = run_with_fixtures(tmp_path, fixtures)
    assert result.returncode == 1
    assert "invalid legacy alias expectation" in result.stderr


def test_prompts_and_negative_rules_are_nonempty() -> None:
    catalog = load(CATALOG)
    for technique in catalog["techniques"]:
        assert technique["promptKo"].strip()
        assert technique["promptEn"].strip()
        assert technique["negativeRules"]
    for group in catalog["globalNegativeRules"].values():
        assert group
