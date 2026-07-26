#!/usr/bin/env python3
"""Validate and inspect the model-neutral Camera Prompt Interpreter catalog."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT.parent / "skills"
DEFAULT_CATALOG = SKILLS_ROOT / "_shared" / "references" / "camera-prompt-catalog.json"
DEFAULT_SCHEMA = SKILLS_ROOT / "_shared" / "references" / "camera-prompt-catalog.schema.json"
DEFAULT_FIXTURES = ROOT / "pipeline" / "tests" / "fixtures" / "camera-intent-cases.json"
EXPECTED_CANONICAL = set(range(1, 37)) | {46}
EXPECTED_ALIASES = {
    37: 28,
    38: 29,
    39: 30,
    40: 31,
    41: 32,
    42: 33,
    43: 34,
    44: 35,
    45: 36,
}
EXPECTED_ALIAS_NAMES = {
    37: ("크레인 다운", "Crane Down"),
    38: ("드론 전진", "Drone Push-In"),
    39: ("드론 후진", "Drone Pull-Back"),
    40: ("헬리콥터 샷", "Helicopter Shot"),
    41: ("1인칭 시점", "First-Person POV"),
    42: ("틸트 시프트", "Tilt-Shift"),
    43: ("인피니트 줌", "Infinite Zoom"),
    44: ("어스 줌 아웃", "Earth Zoom-Out"),
    45: ("타임랩스", "Timelapse"),
}
EXPECTED_TARGETS = (
    "remotionStill",
    "aiVideo",
    "imageGeneration",
    "sceneTransition",
)
EXPECTED_LEVELS = (
    "supported",
    "simulated",
    "composition-only",
    "external-required",
    "not-applicable",
)
EDGE_KINDS = {"ambiguity", "conflict", "mixed"}


class CatalogError(ValueError):
    """A deterministic, user-readable catalog validation failure."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"JSON load failed: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"JSON root must be an object: {path}")
    return data


def _duplicates(values: list[Any]) -> list[Any]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _schema_errors(catalog: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(catalog), key=lambda error: list(error.absolute_path))
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in errors
    ]


def validate_catalog(catalog: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = _schema_errors(catalog, schema)
    if errors:
        raise CatalogError("schema violation: " + "; ".join(errors[:12]))

    techniques = catalog["techniques"]
    aliases = catalog["aliases"]
    numbers = [item["canonicalNo"] for item in techniques]
    ids = [item["id"] for item in techniques]
    names_ko = [item["nameKo"] for item in techniques]
    names_en = [item["nameEn"].casefold() for item in techniques]

    actual_numbers = set(numbers)
    if actual_numbers != EXPECTED_CANONICAL:
        raise CatalogError(
            "canonical number set mismatch: "
            f"missing={sorted(EXPECTED_CANONICAL - actual_numbers)}, "
            f"extra={sorted(actual_numbers - EXPECTED_CANONICAL)}"
        )
    for label, values in (
        ("canonicalNo", numbers),
        ("id", ids),
        ("nameKo", names_ko),
        ("nameEn", names_en),
    ):
        duplicates = _duplicates(values)
        if duplicates:
            raise CatalogError(f"duplicate {label}: {duplicates}")

    if tuple(catalog["targets"]) != EXPECTED_TARGETS:
        raise CatalogError(f"target set/order mismatch: {catalog['targets']}")
    if tuple(catalog["compatibilityLevels"]) != EXPECTED_LEVELS:
        raise CatalogError(f"compatibility level set/order mismatch: {catalog['compatibilityLevels']}")

    id_set = set(ids)
    by_number = {item["canonicalNo"]: item for item in techniques}
    for item in techniques:
        for conflict in item["conflictsWith"]:
            if conflict not in id_set:
                raise CatalogError(f"unknown conflict: {item['id']} -> {conflict}")
            if conflict == item["id"]:
                raise CatalogError(f"self conflict is not allowed: {item['id']}")
        if set(item["compatibility"]) != set(EXPECTED_TARGETS):
            raise CatalogError(f"target compatibility incomplete: {item['id']}")

    actual_aliases = {item["legacyNo"]: item["canonicalNo"] for item in aliases}
    if len(actual_aliases) != len(aliases):
        raise CatalogError("duplicate legacy alias number")
    if actual_aliases != EXPECTED_ALIASES:
        raise CatalogError(f"legacy alias mapping mismatch: {actual_aliases}")
    for alias in aliases:
        canonical = by_number.get(alias["canonicalNo"])
        if canonical is None:
            raise CatalogError(f"alias points to missing canonical: {alias}")
        if alias["legacyNo"] not in canonical["aliases"]:
            raise CatalogError(
                f"canonical alias back-reference missing: {canonical['id']} <- {alias['legacyNo']}"
            )
        actual_names = (alias["nameKo"], alias["nameEn"])
        if actual_names != EXPECTED_ALIAS_NAMES[alias["legacyNo"]]:
            raise CatalogError(
                f"legacy alias description drift: {alias['legacyNo']} -> {actual_names!r}"
            )
    alias_refs = {
        alias_no: item["canonicalNo"]
        for item in techniques
        for alias_no in item["aliases"]
    }
    if alias_refs != EXPECTED_ALIASES:
        raise CatalogError(f"technique alias references mismatch: {alias_refs}")


def validate_fixtures(fixtures: dict[str, Any], catalog: dict[str, Any]) -> dict[str, int]:
    if fixtures.get("schemaVersion") != 1:
        raise CatalogError("fixture schemaVersion must be 1")
    cases = fixtures.get("cases")
    if not isinstance(cases, list):
        raise CatalogError("fixture cases must be an array")
    if len(cases) < 95:
        raise CatalogError(f"fixture coverage below 95: {len(cases)}")

    ids = [case.get("id") for case in cases]
    if any(not isinstance(value, str) or not value for value in ids):
        raise CatalogError("every fixture requires a non-empty string id")
    duplicates = _duplicates(ids)
    if duplicates:
        raise CatalogError(f"duplicate fixture id: {duplicates}")

    by_id = {item["id"]: item for item in catalog["techniques"]}
    by_no = {item["canonicalNo"]: item for item in catalog["techniques"]}
    allowed_kinds = {"canonical", "alias", "ambiguity", "conflict", "mixed", "no-camera"}
    canonical_counts: Counter[int] = Counter()
    alias_coverage: set[int] = set()
    edge_count = 0

    required_fields = {
        "id", "input", "kind", "target", "expectedCanonicalId", "expectedCanonicalNo",
        "expectedCompatibility", "requiredSlots", "needsClarification",
    }
    for index, case in enumerate(cases):
        location = f"fixture[{index}]/{case.get('id', '?')}"
        if not isinstance(case, dict):
            raise CatalogError(f"{location}: fixture must be an object")
        missing = required_fields - set(case)
        if missing:
            raise CatalogError(f"{location}: missing fields {sorted(missing)}")
        if not isinstance(case["input"], str) or not case["input"].strip():
            raise CatalogError(f"{location}: input must be non-empty")
        if case["kind"] not in allowed_kinds:
            raise CatalogError(f"{location}: unknown kind {case['kind']!r}")
        if case["target"] not in EXPECTED_TARGETS:
            raise CatalogError(f"{location}: unknown target {case['target']!r}")
        if not isinstance(case["needsClarification"], bool):
            raise CatalogError(f"{location}: needsClarification must be boolean")
        if case["kind"] in EDGE_KINDS:
            edge_count += 1

        canonical_id = case["expectedCanonicalId"]
        canonical_no = case["expectedCanonicalNo"]
        if canonical_id is None or canonical_no is None:
            if case["kind"] != "no-camera":
                raise CatalogError(f"{location}: only no-camera may omit canonical expectation")
            if case["expectedCompatibility"] is not None or case["requiredSlots"] != []:
                raise CatalogError(f"{location}: no-camera must have null compatibility and no slots")
            continue

        item = by_id.get(canonical_id)
        if item is None:
            raise CatalogError(f"{location}: unknown canonical id {canonical_id!r}")
        if by_no.get(canonical_no) is not item:
            raise CatalogError(f"{location}: canonical id/number mismatch")
        expected_level = item["compatibility"][case["target"]]
        if case["expectedCompatibility"] != expected_level:
            raise CatalogError(
                f"{location}: compatibility mismatch {case['expectedCompatibility']!r} != {expected_level!r}"
            )
        slots = case["requiredSlots"]
        if slots != item["requiredSlots"]:
            raise CatalogError(f"{location}: requiredSlots drift from canonical {canonical_id}")
        if case["kind"] == "canonical":
            canonical_counts[canonical_no] += 1
        if case["kind"] == "alias":
            legacy_no = case.get("legacyNo")
            if EXPECTED_ALIASES.get(legacy_no) != canonical_no:
                raise CatalogError(f"{location}: invalid legacy alias expectation")
            alias_coverage.add(legacy_no)

    missing_canonical = sorted(no for no in EXPECTED_CANONICAL if canonical_counts[no] < 2)
    if missing_canonical:
        raise CatalogError(f"canonical fixture coverage requires 2 each: {missing_canonical}")
    if alias_coverage != set(EXPECTED_ALIASES):
        raise CatalogError(
            f"alias fixture coverage mismatch: missing={sorted(set(EXPECTED_ALIASES) - alias_coverage)}"
        )
    if edge_count < 12:
        raise CatalogError(f"ambiguity/conflict/mixed fixture coverage below 12: {edge_count}")
    return {
        "total": len(cases),
        "canonical": len(canonical_counts),
        "aliases": len(alias_coverage),
        "edge": edge_count,
    }


def _load_and_validate(args: argparse.Namespace) -> dict[str, Any]:
    catalog = load_json(args.catalog)
    schema = load_json(args.schema)
    validate_catalog(catalog, schema)
    return catalog


def _command_validate(args: argparse.Namespace) -> int:
    catalog = _load_and_validate(args)
    print(
        f"PASS camera catalog: canonical={len(catalog['techniques'])}, "
        f"aliases={len(catalog['aliases'])}, version={catalog['catalogVersion']}"
    )
    return 0


def _command_list(args: argparse.Namespace) -> int:
    catalog = _load_and_validate(args)
    techniques = sorted(catalog["techniques"], key=lambda item: item["canonicalNo"])
    if args.format == "json":
        print(json.dumps({"techniques": techniques, "aliases": catalog["aliases"]}, ensure_ascii=False, indent=2))
        return 0
    alias_map = {
        alias["canonicalNo"]: alias["legacyNo"]
        for alias in catalog["aliases"]
    }
    print(f"{'No.':>3}  {'ID':29} {'한국어':24} {'English':34} alias")
    print("-" * 108)
    for item in techniques:
        legacy = alias_map.get(item["canonicalNo"])
        print(
            f"{item['canonicalNo']:>3}  {item['id']:29} {item['nameKo'][:22]:24} "
            f"{item['nameEn'][:32]:34} {legacy or '-'}"
        )
    print(f"canonical={len(techniques)}, aliases={len(catalog['aliases'])}")
    return 0


def _command_check(args: argparse.Namespace) -> int:
    catalog = _load_and_validate(args)
    fixture_stats = validate_fixtures(load_json(args.fixtures), catalog)
    required_docs = (
        SKILLS_ROOT / "_shared" / "references" / "camera-prompt-guide.md",
        SKILLS_ROOT / "director" / "references" / "camera-intent-map.md",
        SKILLS_ROOT / "director" / "references" / "camera-prompt-examples.md",
    )
    missing_docs = [str(path.relative_to(SKILLS_ROOT)) for path in required_docs if not path.is_file()]
    if missing_docs:
        raise CatalogError(f"required camera prompt docs missing: {missing_docs}")
    director_text = (SKILLS_ROOT / "director" / "SKILL.md").read_text(encoding="utf-8")
    for doc in required_docs:
        if doc.name not in director_text:
            raise CatalogError(f"director does not reference {doc.name}")
    print(
        "PASS camera prompt check: "
        f"canonical=37, aliases=9, fixtures={fixture_stats['total']}, "
        f"edge={fixture_stats['edge']}, docs=3"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate schema and semantic invariants").set_defaults(func=_command_validate)
    list_cmd = sub.add_parser("list", help="list 37 canonical techniques and legacy aliases")
    list_cmd.add_argument("--format", choices=("table", "json"), default="table")
    list_cmd.set_defaults(func=_command_list)
    sub.add_parser("check", help="validate catalog, fixtures, and director documentation links").set_defaults(func=_command_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CatalogError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
