from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bin" / "skill-catalog.py"
SPEC = importlib.util.spec_from_file_location("brush_skill_catalog", SCRIPT)
assert SPEC and SPEC.loader
skill_catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skill_catalog)


def _catalog():
    return skill_catalog.clone_catalog(skill_catalog.load_catalog(ROOT))


def _skill(catalog, skill_id: str):
    return next(skill for skill in catalog["skills"] if skill["id"] == skill_id)


def test_current_catalog_matches_all_skill_folders_allowing_pending_agents():
    catalog = _catalog()
    warnings = skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)
    assert len(catalog["skills"]) == 13
    assert isinstance(warnings, list)


def test_catalog_rejects_missing_skill():
    catalog = _catalog()
    catalog["skills"].pop()
    with pytest.raises(skill_catalog.CatalogError, match="schema 위반"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_duplicate_id():
    catalog = _catalog()
    catalog["skills"][1]["id"] = catalog["skills"][0]["id"]
    with pytest.raises(skill_catalog.CatalogError, match="중복 id"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_folder_set_drift():
    catalog = _catalog()
    _skill(catalog, "pen-video")["folder"] = "missing-pen-video"
    with pytest.raises(skill_catalog.CatalogError, match="folder set 불일치"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_dependency_cycle():
    catalog = _catalog()
    _skill(catalog, "brush-video")["dependsOn"] = ["pen-video"]
    with pytest.raises(skill_catalog.CatalogError, match="dependency cycle"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_legacy_alias_as_tenth_skill():
    catalog = _catalog()
    catalog["legacyInstallAliases"][0]["id"] = "brush-video"
    with pytest.raises(skill_catalog.CatalogError, match="정식 skill ID와 충돌"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_unknown_default_voice():
    catalog = _catalog()
    _skill(catalog, "brush-video")["defaultVoice"] = "female-11"
    with pytest.raises(skill_catalog.CatalogError, match="schema 위반"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "renderer"),
        ("profiles", ["unknown-profile"]),
    ],
)
def test_catalog_rejects_unknown_role_and_profile(field, value):
    catalog = _catalog()
    _skill(catalog, "brush-video")[field] = value
    with pytest.raises(skill_catalog.CatalogError, match="schema 위반"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_catalog_rejects_frontmatter_name_drift(monkeypatch):
    catalog = _catalog()
    original = skill_catalog._frontmatter

    def fake_frontmatter(path):
        data = original(path)
        if path.parent.name == "pen-video":
            data["name"] = "wrong-pen-video"
        return data

    monkeypatch.setattr(skill_catalog, "_frontmatter", fake_frontmatter)
    with pytest.raises(skill_catalog.CatalogError, match="frontmatter name 불일치"):
        skill_catalog.validate_catalog(catalog, root=ROOT, require_agents=False)


def test_readme_table_is_deterministic_and_contains_exact_ids():
    catalog = _catalog()
    first = skill_catalog.render_readme_table(catalog)
    second = skill_catalog.render_readme_table(skill_catalog.clone_catalog(catalog))
    assert first == second
    assert first.count("| [") == 13
    assert "cosmic-random-brush-video" not in first
    for skill in catalog["skills"]:
        assert f"[{skill['id']}]" in first


def test_readme_update_then_check_is_idempotent(tmp_path: Path):
    catalog = _catalog()
    readme = tmp_path / "README.md"
    readme.write_text(
        "before\n"
        + skill_catalog.README_BEGIN
        + "\nold\n"
        + skill_catalog.README_END
        + "\nafter\n",
        encoding="utf-8",
    )
    assert skill_catalog.update_readme(catalog, check=False, readme_path=readme) is True
    first = readme.read_bytes()
    assert skill_catalog.update_readme(catalog, check=True, readme_path=readme) is False
    assert readme.read_bytes() == first


def test_readme_update_requires_markers(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("no generated block\n", encoding="utf-8")
    with pytest.raises(skill_catalog.CatalogError, match="marker 누락"):
        skill_catalog.update_readme(_catalog(), check=False, readme_path=readme)


def test_shared_contracts_are_canonical_and_old_paths_are_thin_compatibility_docs():
    skill_root = ROOT.parent / "skills"
    names = (
        "project-yaml-guide.md",
        "transition-checklist.md",
        "bgm-policy.md",
        "supertonic-voice-catalog.md",
    )
    for name in names:
        canonical = skill_root / "_shared" / "references" / name
        compatibility = skill_root / "brush-video" / "references" / name
        canonical_text = canonical.read_text(encoding="utf-8")
        compatibility_text = compatibility.read_text(encoding="utf-8")
        assert len(canonical_text) > len(compatibility_text)
        assert "이전 경로 호환" in compatibility_text
        assert f"../../_shared/references/{name}" in compatibility_text
        assert canonical_text != compatibility_text
