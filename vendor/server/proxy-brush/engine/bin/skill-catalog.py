#!/usr/bin/env python3
"""Project skill catalog validator and deterministic documentation helper."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT.parent / "skills"
CATALOG_PATH = SKILLS_ROOT / "catalog.json"
SCHEMA_PATH = SKILLS_ROOT / "catalog.schema.json"
README_PATH = ROOT / "README.md"
README_BEGIN = "<!-- BEGIN GENERATED SKILL CATALOG -->"
README_END = "<!-- END GENERATED SKILL CATALOG -->"
ROLE_LABELS = {
    "design": "설계",
    "production": "제작",
    "qa": "QA",
    "audit": "감사",
}
STATUS_LABELS = {
    "stable": "정식",
    "specialized": "전문화",
}


class CatalogError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"JSON 로드 실패: {path}: {exc}") from exc


def load_catalog(root: Path = ROOT) -> dict[str, Any]:
    return load_json(_skills_root(root) / "catalog.json")


def _skills_root(root: Path) -> Path:
    bundled = root.parent / "skills"
    return bundled if bundled.is_dir() else root / "skill"


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"SKILL.md 읽기 실패: {path}: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CatalogError(f"YAML frontmatter 시작 누락: {path}")
    try:
        end = lines[1:].index("---") + 1
    except ValueError as exc:
        raise CatalogError(f"YAML frontmatter 종료 누락: {path}") from exc
    data = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(data, dict):
        raise CatalogError(f"YAML frontmatter 객체가 아님: {path}")
    return data


def _expected_agent_interface(skill: dict[str, Any]) -> dict[str, str]:
    interface = skill["interface"]
    return {
        "display_name": interface["displayName"],
        "short_description": interface["shortDescription"],
        "default_prompt": interface["defaultPrompt"],
    }


def _validate_dependency_graph(skills: list[dict[str, Any]]) -> None:
    graph = {skill["id"]: skill["dependsOn"] for skill in skills}
    for skill_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in graph:
                raise CatalogError(f"알 수 없는 dependency: {skill_id} -> {dependency}")
            if dependency == skill_id:
                raise CatalogError(f"자기 자신 dependency 금지: {skill_id}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            raise CatalogError(f"dependency cycle 발견: {skill_id}")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        for dependency in graph[skill_id]:
            visit(dependency)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in graph:
        visit(skill_id)


def validate_catalog(
    catalog: dict[str, Any],
    *,
    root: Path = ROOT,
    require_agents: bool = True,
) -> list[str]:
    skills_root = _skills_root(root)
    schema = load_json(skills_root / "catalog.schema.json")
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(catalog),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors[:8]
        )
        raise CatalogError(f"catalog schema 위반: {detail}")

    skills = catalog["skills"]
    ids = [skill["id"] for skill in skills]
    folders = [skill["folder"] for skill in skills]
    display_names = [skill["interface"]["displayName"] for skill in skills]
    for label, values in (("id", ids), ("folder", folders), ("displayName", display_names)):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise CatalogError(f"중복 {label}: {', '.join(duplicates)}")

    actual_folders = sorted(
        path.parent.name
        for path in skills_root.glob("*/SKILL.md")
    )
    if sorted(folders) != actual_folders:
        raise CatalogError(
            f"catalog folder set 불일치: catalog={sorted(folders)}, actual={actual_folders}"
        )

    _validate_dependency_graph(skills)
    warnings: list[str] = []
    for skill in skills:
        skill_dir = skills_root / skill["folder"]
        skill_md = skill_dir / "SKILL.md"
        frontmatter = _frontmatter(skill_md)
        if frontmatter.get("name") != skill["id"]:
            raise CatalogError(
                f"frontmatter name 불일치: {skill_md}: "
                f"{frontmatter.get('name')!r} != {skill['id']!r}"
            )
        if not isinstance(frontmatter.get("description"), str) or not frontmatter["description"].strip():
            raise CatalogError(f"frontmatter description 누락: {skill_md}")

        agent_path = skill_dir / "agents" / "openai.yaml"
        if not agent_path.is_file():
            message = f"agents/openai.yaml 누락: {skill['id']}"
            if require_agents:
                raise CatalogError(message)
            warnings.append(message)
            continue
        try:
            agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise CatalogError(f"agent metadata 로드 실패: {agent_path}: {exc}") from exc
        actual_interface = agent.get("interface") if isinstance(agent, dict) else None
        expected_interface = _expected_agent_interface(skill)
        if actual_interface != expected_interface:
            message = (
                f"agent metadata 불일치: {skill['id']}: "
                f"actual={actual_interface!r}, expected={expected_interface!r}"
            )
            if require_agents:
                raise CatalogError(message)
            warnings.append(message)

    aliases = catalog["legacyInstallAliases"]
    alias_ids = [alias["id"] for alias in aliases]
    if len(alias_ids) != len(set(alias_ids)):
        raise CatalogError("legacy install alias ID 중복")
    for alias in aliases:
        if alias["id"] in ids:
            raise CatalogError(f"legacy alias가 정식 skill ID와 충돌: {alias['id']}")
        if alias["replacement"] not in ids:
            raise CatalogError(f"legacy replacement 누락: {alias['replacement']}")

    voice_path = root / "assets" / "voices" / "catalog.json"
    if voice_path.is_file():
        voice_catalog = load_json(voice_path)
        voice_ids = {voice["id"] for voice in voice_catalog.get("voices", [])}
        for skill in skills:
            voice = skill["defaultVoice"]
            if voice is not None and voice not in voice_ids:
                raise CatalogError(f"catalog에 없는 defaultVoice: {skill['id']} -> {voice}")

    return warnings


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_readme_table(catalog: dict[str, Any]) -> str:
    lines = [
        README_BEGIN,
        "| 스킬 | 구분 | 역할 | 진입점 | 상태 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for skill in catalog["skills"]:
        link = f"[{skill['id']}](../skills/{skill['folder']}/SKILL.md)"
        status = f"{STATUS_LABELS[skill['status']]} v{skill['version']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    link,
                    ROLE_LABELS[skill["role"]],
                    _escape_cell(skill["summary"]),
                    f"`{_escape_cell(skill['entrypoint'])}`",
                    status,
                ]
            )
            + " |"
        )
    lines.append(README_END)
    return "\n".join(lines)


def update_readme(catalog: dict[str, Any], *, check: bool, readme_path: Path = README_PATH) -> bool:
    text = readme_path.read_text(encoding="utf-8")
    if README_BEGIN not in text or README_END not in text:
        raise CatalogError(
            f"README generated marker 누락: {README_BEGIN!r}, {README_END!r}"
        )
    before, rest = text.split(README_BEGIN, 1)
    _current, after = rest.split(README_END, 1)
    expected = before + render_readme_table(catalog) + after
    if expected == text:
        return False
    if check:
        raise CatalogError("README skill catalog generated block drift")
    readme_path.write_text(expected, encoding="utf-8")
    return True


def clone_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Tests and callers can mutate a deep copy without changing the source object."""
    return copy.deepcopy(catalog)


def _command_validate(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    warnings = validate_catalog(catalog, require_agents=not args.allow_missing_agents)
    for warning in warnings:
        print(f"WARN {warning}")
    print(f"PASS skills={len(catalog['skills'])} catalogVersion={catalog['catalogVersion']}")
    return 0


def _command_list(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    validate_catalog(catalog, require_agents=not args.allow_missing_agents)
    if args.format == "json":
        print(json.dumps(catalog["skills"], ensure_ascii=False, indent=2))
        return 0
    print(f"{'ID':34} {'구분':6} {'프로파일':22} 기본 음성")
    print("-" * 86)
    for skill in catalog["skills"]:
        profiles = ",".join(skill["profiles"]) or "-"
        voice = skill["defaultVoice"] or "-"
        print(f"{skill['id']:34} {ROLE_LABELS[skill['role']]:6} {profiles:22} {voice}")
    return 0


def _command_emit_install(_args: argparse.Namespace) -> int:
    catalog = load_catalog()
    validate_catalog(catalog, require_agents=True)
    for skill in catalog["skills"]:
        source = _skills_root(ROOT) / skill["folder"]
        print(f"{skill['id']}\t{source}")
    return 0


def _command_generate_readme(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    validate_catalog(catalog, require_agents=True)
    changed = update_readme(catalog, check=args.check)
    print("PASS README skill catalog " + ("unchanged" if not changed else "updated"))
    return 0


def _command_check(_args: argparse.Namespace) -> int:
    catalog = load_catalog()
    validate_catalog(catalog, require_agents=True)
    update_readme(catalog, check=True)
    print(f"PASS skill catalog check: skills={len(catalog['skills'])}, agents={len(catalog['skills'])}, README=sync")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="catalog, SKILL.md, agent metadata 검증")
    validate.add_argument("--allow-missing-agents", action="store_true")
    validate.set_defaults(func=_command_validate)

    list_cmd = sub.add_parser("list", help="정식 스킬 목록 표시")
    list_cmd.add_argument("--format", choices=("table", "json"), default="table")
    list_cmd.add_argument("--allow-missing-agents", action="store_true")
    list_cmd.set_defaults(func=_command_list)

    emit = sub.add_parser("emit-install", help="installer용 ID/TAB/source 출력")
    emit.set_defaults(func=_command_emit_install)

    readme = sub.add_parser("generate-readme", help="README generated skill table 갱신")
    readme.add_argument("--check", action="store_true")
    readme.set_defaults(func=_command_generate_readme)

    check = sub.add_parser("check", help="catalog, agents, README drift 전체 검사")
    check.set_defaults(func=_command_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CatalogError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
