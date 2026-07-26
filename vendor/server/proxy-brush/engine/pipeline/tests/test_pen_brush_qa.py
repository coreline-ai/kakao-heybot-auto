import json

from brushvid.qa import write_pen_brush_report


def test_pen_brush_report_passes_strict_contract(tmp_path):
    outline = {"drawStart": 8, "drawEnd": 114, "penInvisibleAfter": 114,
               "durationInFrames": 300, "coverage": .992}
    paint = {"drawStart": 122, "drawEnd": 264, "penInvisibleAfter": 270,
             "durationInFrames": 300, "coverage": 1.0, "missingPixels": 0}
    out = tmp_path / "report.json"
    report = write_pen_brush_report(out, project_id="demo", scenes=[
        {"sceneId": "scene-01", "outline": outline, "paint": paint, "lineThickness": 2.7,
         "finalLineThickness": 2.72, "finalLineThicknessDeltaPct": .74}
    ])
    assert report["pass"] is True
    assert json.loads(out.read_text())["scenes"][0]["cursorOverlapFrames"] == 0
