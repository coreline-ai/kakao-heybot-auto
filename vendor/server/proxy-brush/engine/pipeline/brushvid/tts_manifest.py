"""TTS voice manifest v2 생성·원자적 저장."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .tts_contract import (
    ADAPTER_VERSION,
    ENGINE_LICENSES,
    ENGINE_MODEL_IDS,
    MODEL_REVISIONS,
    NORMALIZER_VERSION,
    TTS_MANIFEST_SCHEMA_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_engine_manifest(
    *,
    project_id: str,
    config: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    engine = config["engine"]
    metadata = dict(result["voice"])
    native_rate = int(metadata.get("nativeSampleRate", metadata.get("sampleRate", 44100)))
    manifest = {
        "schemaVersion": TTS_MANIFEST_SCHEMA_VERSION,
        "projectId": project_id,
        "engine": engine,
        "voice": config["voice"],
        "model": metadata.get("model", ENGINE_MODEL_IDS[engine]),
        "modelRevision": metadata.get("modelRevision", MODEL_REVISIONS.get(engine, "legacy")),
        "packageVersion": metadata.get("packageVersion", "unknown"),
        "language": config.get("language", "ko"),
        "nativeSampleRate": native_rate,
        "outputSampleRate": 44100,
        "requestedSpeed": float(config.get("speed", 1.05)),
        "appliedSpeed": float(metadata.get("speed", config.get("speed", 1.05))),
        "speedAppliedBy": metadata.get("speedAppliedBy", "native"),
        "requestedTiming": config.get("timing", "tts"),
        "appliedTiming": "tts",
        "pauseMs": config.get("pauseMs", 300),
        "durationSec": float(result["durationSec"]),
        "sentenceCount": len(result["entries"]),
        "audioSha256": sha256_file(result["wav"]),
        "license": metadata.get(
            "license", {**ENGINE_LICENSES[engine], "aiDisclosureRequired": True}
        ),
        "aiDisclosure": metadata.get(
            "aiDisclosure", f"이 콘텐츠의 내레이션은 {engine} AI 합성 음성으로 제작되었습니다."
        ),
        "ttsCacheSchemaVersion": 1,
        "adapterVersion": ADAPTER_VERSION,
        "normalizerVersion": NORMALIZER_VERSION,
    }
    if engine == "melo-ko":
        manifest["speaker"] = metadata.get("speaker", "KR")
    elif engine == "qwen3-base":
        reference = config.get("reference") or {}
        manifest.update({
            "referenceVoiceId": config["voice"],
            "referenceAudioSha256": sha256_file(reference["audio"]),
            "referenceTranscriptSha256": sha256_file(reference["transcript"]),
            "xVectorOnlyMode": False,
        })
    return manifest


def write_manifest_atomic(manifest: dict[str, Any], path: str | Path) -> Path:
    schema_path = REPO_ROOT / "schema" / "tts-voice-manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest)
    )
    if errors:
        where = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"TTS manifest schema 오류({where}): {errors[0].message}")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
