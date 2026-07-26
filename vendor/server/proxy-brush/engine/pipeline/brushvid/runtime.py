"""Runtime path contract for an immutable engine and isolated job workspace."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class RuntimePathError(ValueError):
    pass


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def ensure_under(path: str | Path, root: str | Path, *, label: str) -> Path:
    candidate = _resolved(path)
    allowed = _resolved(root)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise RuntimePathError(f"{label} escapes allowed root") from exc
    return candidate


@dataclass(frozen=True)
class RuntimeContext:
    engine_root: Path
    package_root: Path
    workspace_root: Path
    input_root: Path
    data_root: Path
    public_root: Path
    output_root: Path
    qa_root: Path
    logs_root: Path
    model_root: Path

    @classmethod
    def create(cls, engine_root: str | Path, workspace_root: str | Path) -> "RuntimeContext":
        engine = _resolved(engine_root)
        raw_workspace = Path(workspace_root).expanduser()
        if raw_workspace.exists() and raw_workspace.is_symlink():
            raise RuntimePathError("workspace root cannot be a symlink")
        workspace = _resolved(workspace_root)
        if workspace == engine or workspace in engine.parents or engine in workspace.parents:
            raise RuntimePathError("workspace and engine roots must be disjoint")
        context = cls(
            engine_root=engine,
            package_root=engine,
            workspace_root=workspace,
            input_root=workspace / "input",
            data_root=workspace / "data",
            public_root=workspace / "public",
            output_root=workspace / "output",
            qa_root=workspace / "qa",
            logs_root=workspace / "logs",
            model_root=_resolved(os.environ.get(
                "DRAW_PROXY_MODEL_ROOT", engine.parent / "runtime" / "models")),
        )
        context.prepare()
        return context

    @classmethod
    def standalone(cls, engine_root: str | Path) -> "RuntimeContext":
        engine = _resolved(engine_root)
        return cls(
            engine_root=engine,
            package_root=engine,
            workspace_root=engine,
            input_root=engine,
            data_root=engine / "data",
            public_root=engine / "public",
            output_root=engine / "output",
            qa_root=engine / "data",
            logs_root=engine / "data" / "logs",
            model_root=_resolved(os.environ.get(
                "DRAW_PROXY_MODEL_ROOT", engine.parent / "runtime" / "models")),
        )

    def prepare(self) -> None:
        for path in (
            self.input_root,
            self.data_root,
            self.public_root,
            self.output_root,
            self.qa_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
            ensure_under(path, self.workspace_root, label="runtime directory")
        source = self.engine_root / "public" / "brush-draw"
        target = self.public_root / "brush-draw"
        if source.is_dir() and not target.exists():
            shutil.copytree(source, target, symlinks=False)

    def validate_project_inputs(self, cfg: Any, project_yaml: str | Path) -> None:
        self.validate_project_source(project_yaml)
        paths = [cfg.srt, cfg.audio, cfg.script, *cfg.bg_images]
        reference = (getattr(cfg, "tts", None) or {}).get("reference") or {}
        paths.extend(reference.values())
        for path in paths:
            if path is None:
                continue
            if Path(path).is_symlink():
                raise RuntimePathError("project input cannot be a symlink")
            checked = ensure_under(path, self.input_root, label="project input")
            if not checked.is_file():
                raise RuntimePathError("project input must be a regular file")

    def validate_project_source(self, project_yaml: str | Path) -> Path:
        raw_project = Path(project_yaml).expanduser()
        if raw_project.is_symlink():
            raise RuntimePathError("project config cannot be a symlink")
        project = ensure_under(raw_project, self.input_root, label="project config")
        if project.suffix.lower() not in {".yaml", ".yml"} or not project.is_file():
            raise RuntimePathError("project config must be a regular YAML file")
        try:
            document = yaml.safe_load(project.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimePathError("project config is not readable YAML") from exc
        if not isinstance(document, dict):
            raise RuntimePathError("project config must contain a mapping")
        declared: list[tuple[str, Any, set[str]]] = []
        input_config = document.get("input") or {}
        if isinstance(input_config, dict):
            declared.extend([
                ("input.srt", input_config.get("srt"), {".srt"}),
                ("input.audio", input_config.get("audio"), {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}),
                ("input.script", input_config.get("script"), {".txt", ".md"}),
            ])
            tts = input_config.get("tts") or {}
            reference = tts.get("reference") if isinstance(tts, dict) else None
            if isinstance(reference, dict):
                declared.extend([
                    ("input.tts.reference.audio", reference.get("audio"), {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}),
                    ("input.tts.reference.transcript", reference.get("transcript"), {".txt", ".md"}),
                ])
        background = document.get("background") or {}
        images = background.get("images") if isinstance(background, dict) else []
        if isinstance(images, list):
            declared.extend(
                (f"background.images[{index}]", value, {".png", ".jpg", ".jpeg", ".webp"})
                for index, value in enumerate(images)
            )
        for label, value, suffixes in declared:
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise RuntimePathError(f"{label} must be a non-empty relative path")
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimePathError(f"{label} escapes project input root")
            raw_input = project.parent / relative
            if raw_input.is_symlink():
                raise RuntimePathError(f"{label} cannot be a symlink")
            checked = ensure_under(raw_input, self.input_root, label=label)
            if checked.suffix.lower() not in suffixes:
                raise RuntimePathError(f"{label} has an unsupported extension")
            if not checked.is_file():
                raise RuntimePathError(f"{label} must be a regular file")
        return project

    def capability_environment(self) -> dict[str, str]:
        """Return explicit local-only model roots for child processes."""
        values = {
            "HF_HOME": str(self.model_root / "huggingface"),
            "DRAW_PROXY_SUPERTONIC_ROOT": str(self.model_root / "supertonic-3"),
        }
        stable_audio = os.environ.get("STABLE_AUDIO_3_MLX_ROOT")
        if stable_audio:
            values["STABLE_AUDIO_3_MLX_ROOT"] = str(_resolved(stable_audio))
        codex_bin = os.environ.get("DRAW_PROXY_CODEX_BIN")
        if codex_bin:
            values["DRAW_PROXY_CODEX_BIN"] = str(_resolved(codex_bin))
        return values

    @property
    def isolated(self) -> bool:
        return self.workspace_root != self.engine_root

    def relative(self, path: str | Path | Any) -> str:
        if not isinstance(path, (str, Path)):
            return str(path)
        checked = ensure_under(path, self.workspace_root, label="runtime output")
        if not self.isolated:
            return str(checked)
        return str(checked.relative_to(self.workspace_root))

    def resolve(self, value: str | Path) -> Path:
        path = Path(value)
        candidate = path if path.is_absolute() else self.workspace_root / path
        return ensure_under(candidate, self.workspace_root, label="runtime path")


def sanitize_payload(value: Any, context: RuntimeContext) -> Any:
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, dict):
        return {
            str(key): sanitize_payload(child, context)
            for key, child in value.items()
            if "secret" not in str(key).lower() and "token" not in str(key).lower()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(child, context) for child in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                return context.relative(candidate)
            except RuntimePathError:
                return candidate.name
        value = value.replace(str(context.workspace_root), "$WORKSPACE")
        value = value.replace(str(context.engine_root), "$ENGINE")
    return value


class StageEventWriter:
    def __init__(self, path: str | Path, context: RuntimeContext):
        self.path = ensure_under(path, context.workspace_root, label="event log")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.context = context
        self.sequence = self._last_sequence()

    def _last_sequence(self) -> int:
        if not self.path.is_file():
            return 0
        last = 0
        for raw in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sequence = item.get("sequence")
            if isinstance(sequence, int):
                last = max(last, sequence)
        return last

    def emit(self, stage: str, phase: str, payload: dict[str, Any] | None = None) -> None:
        if phase not in {"started", "completed", "skipped", "failed"}:
            raise ValueError(f"invalid event phase: {phase}")
        self.sequence += 1
        event = {
            "sequence": self.sequence,
            "at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "phase": phase,
            "payload": sanitize_payload(payload or {}, self.context),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            if self.path.stat().st_size:
                with self.path.open("rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    if existing.read(1) != b"\n":
                        handle.write("\n")
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
