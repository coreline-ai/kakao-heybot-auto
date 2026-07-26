from brushvid.sync import retime_range, snap_pen_brush_boundary


def test_boundary_snaps_to_nearest_cue_end_inside_window():
    cues = [{"from": 0, "to": 90}, {"from": 90, "to": 118}, {"from": 118, "to": 180}]
    assert snap_pen_brush_boundary(300, cues) == 118
    assert snap_pen_brush_boundary(300, []) == 114
    assert snap_pen_brush_boundary(300, [{"from": 40, "to": 260}]) == 114


def test_boundary_prefers_sentence_boundary_even_outside_window():
    cues = [{"from": 0, "to": 90}, {"from": 90, "to": 180}, {"from": 180, "to": 300}]
    assert snap_pen_brush_boundary(330, cues) == 90


def test_retime_range_preserves_order_and_endpoints():
    data = {"meta": {"durationInFrames": 300, "drawStart": 8, "drawEnd": 100,
                     "penInvisibleAfter": 106},
            "strokes": [{"start": 8, "end": 54}, {"start": 54, "end": 100}]}
    out = retime_range(data, 20, 120)
    assert out["strokes"][0]["start"] == 20
    assert out["strokes"][-1]["end"] == 120
    assert out["meta"]["penInvisibleAfter"] == 126
