"""cues.py 테스트 — TC-3.2(SRT frame 환산), TC-3.E2(타임코드 역전)."""
import pytest
from PIL import Image

from brushvid.cues import group_scenes, parse_srt, split_cue, srt_to_cues, title_color, visual_len

SRT_3CUE = """\
1
00:00:01,000 --> 00:00:03,000
첫 번째 자막

2
00:00:04,000 --> 00:00:06,500
두 번째 자막

3
00:00:07,000 --> 00:00:09,000
세 번째 자막
"""


def test_tc_3_2_srt_frame_conversion():
    """TC-3.2: 3-cue SRT → from/to 프레임 정확 환산 (fps 30)."""
    cues = srt_to_cues(SRT_3CUE, fps=30)
    assert len(cues) == 3
    assert (cues[0]["from"], cues[0]["to"]) == (30, 90)
    assert (cues[1]["from"], cues[1]["to"]) == (120, 195)  # 6.5s → 195
    assert (cues[2]["from"], cues[2]["to"]) == (210, 270)
    assert cues[0]["text"] == "첫 번째 자막"


def test_tc_3_e2_reversed_timecode():
    """TC-3.E2: 타임코드 역전 SRT → 명시적 에러."""
    bad = "1\n00:00:05,000 --> 00:00:02,000\n역전 자막\n"
    with pytest.raises(ValueError, match="역전"):
        parse_srt(bad)


def test_tc_3_e2_backwards_entry():
    """이전 항목보다 과거로 점프하는 SRT 도 에러."""
    bad = "1\n00:00:01,000 --> 00:00:05,000\n가\n\n2\n00:00:03,000 --> 00:00:06,000\n나\n"
    with pytest.raises(ValueError, match="역행"):
        parse_srt(bad)


def test_long_cue_split_proportional():
    """긴 문장은 한 줄 폭 이하 조각으로 분할되고 프레임이 비례 배분된다."""
    text = "아주 긴 한국어 내레이션 문장이 자막 한 줄에 다 들어가지 않으면 여러 조각으로 나뉘어야 한다"
    pieces = split_cue(text, 6, 296, max_chars=30.0)
    assert len(pieces) >= 2
    assert pieces[0]["from"] == 6
    assert pieces[-1]["to"] == 296
    for i in range(1, len(pieces)):
        assert pieces[i]["from"] == pieces[i - 1]["to"]  # 이어짐
    for piece in pieces:
        assert visual_len(piece["text"]) <= 30.0


def test_group_scenes_bounds_and_local_frames():
    """씬 그룹핑: 상한 초과 시 경계 분할 + cue 프레임은 씬-로컬."""
    cues = [{"text": f"c{i}", "from": i * 150, "to": i * 150 + 120} for i in range(6)]
    scenes = group_scenes(cues, min_frames=240, max_frames=450, tail_pad=30)
    assert len(scenes) >= 2
    total_cues = sum(len(s["cues"]) for s in scenes)
    assert total_cues == 6
    for s in scenes:
        assert s["cues"][0]["from"] == 0
        assert s["durationInFrames"] == s["cues"][-1]["to"] + 30
        assert s["durationInFrames"] <= 450 + 30


def test_title_color_dominant(tmp_path):
    """도미넌트 채도색 계열이 뽑히고 가독성 위해 어둡게 보정된다."""
    img = Image.new("RGB", (200, 100), (250, 249, 246))
    for x in range(120):  # 파란 영역이 도미넌트
        for y in range(100):
            img.putpixel((x, y), (40, 90, 200))
    p = tmp_path / "img.png"
    img.save(p)
    hexcol = title_color(p)
    r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    assert b > r and b > g  # 파란 계열
    assert b < 200  # 어둡게 보정됨
