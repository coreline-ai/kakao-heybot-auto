from pathlib import Path

from brushvid.capabilities import probe_capabilities
from brushvid.runtime import RuntimeContext


def test_capability_probe_is_local_and_sanitized(tmp_path: Path, monkeypatch):
    engine = tmp_path / "draw_proxy" / "engine"
    engine.mkdir(parents=True)
    context = RuntimeContext.create(engine, tmp_path / "job")
    monkeypatch.delenv("STABLE_AUDIO_3_MLX_ROOT", raising=False)
    monkeypatch.delenv("DRAW_PROXY_CODEX_BIN", raising=False)
    result = probe_capabilities(context)
    assert result["preset"] == {"available": True, "code": "OK"}
    assert result["stable-audio"]["code"] == "MODEL_MISSING"
    assert result["i2v"] == {"available": False, "code": "CAPABILITY_MISSING"}
    assert str(tmp_path) not in str(result)
