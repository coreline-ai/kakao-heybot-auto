"""sync.py 테스트 — 배분/리타이밍/엣지/sync-map 우선."""
import logging

import pytest

from brushvid.sync import allocate_zones_to_cues, apply_sync


def _routes_data(zone_strokes: dict[int, int], start: float = 8.0, step: float = 2.0) -> tuple[dict, list[dict]]:
    """존별 스트로크 개수로 가짜 routes/zones 생성. 존 순서대로 연속 타이밍."""
    strokes, zones = [], []
    t = start
    i = 0
    for z, count in zone_strokes.items():
        for _ in range(count):
            strokes.append({"id": f"s{i:04d}", "kind": "contour", "width": 18.0,
                            "start": round(t, 2), "end": round(t + step * 0.8, 2),
                            "points": [[0, 0], [10, 10]], "zone": z})
            t += step
            i += 1
        zones.append({"zone": z, "inkPixels": 1000 * count, "strokeCount": count})
    meta = {"drawEnd": t, "penInvisibleAfter": t + 8, "fps": 30}
    return {"meta": meta, "strokes": strokes}, zones


def test_allocate_3zones_2cues_equal():
    """존3(등질량)/cue2(등길이) → [0,0,1]."""
    assert allocate_zones_to_cues([1, 1, 1], [100, 100]) == [0, 0, 1]


def test_allocate_2zones_4cues_spread():
    """존2/cue4 → 채워진 쿼터를 건너뛰어 분산 배치 [0,2] (단조)."""
    out = allocate_zones_to_cues([1, 1], [100, 100, 100, 100])
    assert out == [0, 2]
    assert out == sorted(out)


def test_allocate_mass_weighted():
    """질량 3:1 이면 큰 존이 첫 cue 를 다 채우고 다음 존은 다음 cue 로."""
    assert allocate_zones_to_cues([3, 1], [100, 100]) == [0, 1]


def test_retime_strokes_within_windows():
    """리타이밍 후 모든 스트로크가 배정 cue 구간 안 (창 밖 0건) + 앞 10% 여유."""
    data, zones = _routes_data({0: 5, 1: 5})
    cues = [{"from": 0, "to": 100}, {"from": 100, "to": 300}]
    out = apply_sync(data, zones, cues)
    assign = out["meta"]["zoneCueAssignment"]
    for s in out["strokes"]:
        ci = assign[s["zone"]]
        f0, f1 = cues[ci]["from"], cues[ci]["to"]
        lead = f0 + (f1 - f0) * 0.10
        assert lead - 0.01 <= s["start"] <= s["end"] <= f1 + 0.01, s
    assert out["meta"]["drawEnd"] == max(s["end"] for s in out["strokes"])
    assert out["meta"]["penInvisibleAfter"] == out["meta"]["drawEnd"] + 8
    # 원본 불변
    assert data["meta"]["drawEnd"] == 28.0


def test_edge_zone1_and_cue0(caplog):
    """존 1개 → 첫 cue 에 배정 / cue 0개 → 경고 + 원본 그대로 (크래시 없음)."""
    data, zones = _routes_data({0: 3})
    out = apply_sync(data, zones, [{"from": 10, "to": 200}])
    assert out["meta"]["zoneCueAssignment"] == [0]

    with caplog.at_level(logging.WARNING, logger="brushvid.sync"):
        same = apply_sync(data, zones, [])
    assert same is data  # 원본 그대로
    assert any("cue 0개" in r.message for r in caplog.records)


def test_sync_map_overrides_auto():
    """sync-map 이 있으면 자동 배분 대신 사용 (미지정 존은 자동 폴백)."""
    data, zones = _routes_data({0: 2, 1: 2})
    cues = [{"from": 0, "to": 150}, {"from": 150, "to": 300}]
    out = apply_sync(data, zones, cues, sync_map={"zoneToCue": {"0": 1}})
    assign = out["meta"]["zoneCueAssignment"]
    assert assign[0] == 1  # 수동 매핑 우선
    assert out["meta"]["sync"] == "map"
    z0 = [s for s in out["strokes"] if s["zone"] == 0]
    assert all(s["start"] >= 150 for s in z0)

    with pytest.raises(ValueError, match="범위 밖"):
        apply_sync(data, zones, cues, sync_map={"zoneToCue": {"1": 9}})


# ── build.py sync 스테이지 (off 동일성 / project 검증) ──

import importlib.util
import json as _json
from pathlib import Path as _Path

_BUILD = _Path(__file__).resolve().parents[2] / "bin" / "build.py"
_spec = importlib.util.spec_from_file_location("buildmod_sync", _BUILD)
_bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bm)


def _pen_pipeline(tmp_path, monkeypatch, sync="auto", cues=None, preserve_source=False):
    monkeypatch.setattr(_bm, "REPO_ROOT", tmp_path)
    cfg = _bm.ProjectConfig(project_id="sync-unit", drawing_profile="pen", drawing_sync=sync,
                            drawing_preserve_source=preserve_source)
    pipe = _bm.Pipeline(cfg)
    scenes = [{"durationInFrames": 300, "cues": cues or []}]
    pipe._write_scenes(scenes)
    routes = {"meta": {"drawEnd": 105, "penInvisibleAfter": 113, "zoneCount": 1},
              "strokes": [{"id": "s0000", "kind": "contour", "width": 18.0,
                           "start": 8.0, "end": 20.0, "points": [[0, 0], [5, 5]], "zone": 0}]}
    rp = pipe.public_dir / "routes" / "scene-01.routes.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(_json.dumps({"meta": routes["meta"], "strokes": routes["strokes"]}), encoding="utf-8")
    zd = pipe.data_dir / "zones" / "scene-01"
    zd.mkdir(parents=True, exist_ok=True)
    (zd / "zones.json").write_text(_json.dumps(
        {"sceneId": "scene-01", "zones": [{"zone": 0, "inkPixels": 100}]}), encoding="utf-8")
    return pipe, rp


def test_stage_sync_off_bytes_identical(tmp_path, monkeypatch):
    """sync: off → routes 파일 바이트 불변 (기존 pen 결과 동일)."""
    pipe, rp = _pen_pipeline(tmp_path, monkeypatch, sync="off",
                             cues=[{"text": "가", "from": 0, "to": 150}])
    before = rp.read_bytes()
    payload = pipe.stage_sync()
    assert rp.read_bytes() == before
    assert "skippedReason" in payload


def test_stage_sync_applies_and_cue0_skips(tmp_path, monkeypatch):
    """auto + cue 있음 → 리타이밍 적용 / cue 0개 씬 → 원본 유지."""
    pipe, rp = _pen_pipeline(tmp_path, monkeypatch,
                             cues=[{"text": "가", "from": 100, "to": 200}])
    pipe.stage_sync()
    data = _json.loads(rp.read_text(encoding="utf-8"))
    assert data["meta"]["sync"] == "auto"
    assert data["strokes"][0]["start"] >= 110  # 100 + 10% lead

    pipe2, rp2 = _pen_pipeline(tmp_path / "b", monkeypatch, cues=[])
    before = rp2.read_bytes()
    payload = pipe2.stage_sync()
    assert rp2.read_bytes() == before
    assert payload["synced"] == [None]


def test_stage_sync_preserve_source_reserves_completion_tail(tmp_path, monkeypatch):
    pipe, rp = _pen_pipeline(
        tmp_path, monkeypatch, preserve_source=True,
        cues=[{"text": "첫 문장", "from": 0, "to": 115},
              {"text": "둘째 문장", "from": 115, "to": 270}],
    )
    pipe.stage_sync()
    data = _json.loads(rp.read_text(encoding="utf-8"))
    assert data["meta"]["drawEnd"] <= 240
    assert data["meta"]["completionTailFrames"] >= 60


def test_project_drawing_sync_validation(tmp_path):
    """drawing.sync 검증 — 기본 auto, off 허용, 오타 거부."""
    from brushvid.project import load_project
    y = tmp_path / "p.yaml"
    y.write_text("projectId: d\n", encoding="utf-8")
    assert load_project(y).drawing_sync == "auto"
    y.write_text("projectId: d\ndrawing:\n  sync: off\n", encoding="utf-8")
    assert load_project(y).drawing_sync == "off"
    y.write_text("projectId: d\ndrawing:\n  sync: onn\n", encoding="utf-8")
    import pytest as _pytest
    with _pytest.raises(ValueError, match="drawing.sync"):
        load_project(y)
