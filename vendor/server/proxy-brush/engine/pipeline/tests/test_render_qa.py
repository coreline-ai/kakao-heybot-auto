"""render.py / qa.py 유닛 테스트 — remotion 없이 ffmpeg/PIL 경로만 검증."""
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from brushvid.qa import (capture_video_frames, completion_sample_frames,
                         completion_timing, contact_sheet,
                         public_roots_for_props, write_completion_report,
                         write_manifest)
from brushvid.render import (attach_cover_art, concat, mux_audio, probe_duration,
                             probe_video_duration, remotion_browser_executable,
                             render_segments)


def _mk_clip(path, seconds=0.2, color="red"):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)


@pytest.fixture()
def clips(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _mk_clip(a, color="red")
    _mk_clip(b, color="blue")
    return a, b


def test_concat_and_probe(tmp_path, clips):
    """세그먼트 concat 결과 길이 = 합산."""
    out = concat(list(clips), tmp_path / "joined.mp4")
    assert out.is_file()
    assert probe_duration(out) == pytest.approx(0.4, abs=0.1)


def test_mux_audio(tmp_path, clips):
    """오디오 mux 후 오디오 스트림 1개 존재."""
    audio = tmp_path / "tone.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
         "-c:a", "aac", str(audio)],
        check=True, capture_output=True)
    out = mux_audio(clips[0], audio, tmp_path / "muxed.mp4")
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "audio"


def test_mux_audio_keeps_all_video_frames_despite_aac_encoder_delay(tmp_path):
    """-shortest가 AAC 종단 지연으로 마지막 비디오 프레임을 자르면 안 된다."""
    video = tmp_path / "video.mp4"
    _mk_clip(video, seconds=1.0)
    audio = tmp_path / "silent.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "1", "-c:a", "pcm_s16le", str(audio)],
        check=True, capture_output=True)

    out = mux_audio(video, audio, tmp_path / "muxed-exact.mp4")
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets,duration", "-of", "json", str(out)],
        capture_output=True, text=True, check=True)
    stream = json.loads(res.stdout)["streams"][0]
    assert int(stream["nb_read_packets"]) == 30
    assert float(stream["duration"]) == pytest.approx(1.0, abs=0.01)
def test_attach_cover_art_preserves_timeline_and_marks_attached_picture(tmp_path, clips):
    """표지는 보조 스트림이며 실제 v:0 타임라인이나 재생 시작을 바꾸지 않는다."""
    cover = tmp_path / "cover.png"
    Image.new("RGB", (320, 180), (250, 210, 80)).save(cover)
    out = attach_cover_art(clips[0], cover, tmp_path / "with-cover.mp4")

    assert probe_video_duration(out) == pytest.approx(probe_video_duration(clips[0]), abs=0.02)
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type:stream_disposition=attached_pic", "-of", "json", str(out)],
        capture_output=True, text=True, check=True)
    streams = json.loads(res.stdout)["streams"]
    assert streams[0]["disposition"]["attached_pic"] == 0
    assert any(
        stream["codec_type"] == "video" and stream["disposition"]["attached_pic"] == 1
        for stream in streams
    )


def test_probe_video_duration_ignores_longer_aac_padding(tmp_path, clips):
    """컨테이너/오디오가 더 길어도 영상 프레임 duration을 반환한다."""
    audio = tmp_path / "longer.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.27",
         "-c:a", "aac", str(audio)],
        check=True, capture_output=True)
    muxed = tmp_path / "muxed-padding.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(clips[0]), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(muxed)],
        check=True, capture_output=True)
    assert probe_duration(muxed) > probe_video_duration(muxed)
    assert probe_video_duration(muxed) == pytest.approx(0.2, abs=0.02)


def test_concat_uses_video_duration_when_aac_padding_is_longer(tmp_path, clips):
    """AAC 패딩이 있는 청크도 영상 PTS 공백과 프레임 손실 없이 연결한다."""
    padded = []
    for index, clip in enumerate(clips):
        audio = tmp_path / f"padding-{index}.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.27",
             "-c:a", "aac", str(audio)],
            check=True, capture_output=True)
        muxed = tmp_path / f"padded-{index}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip), "-i", str(audio),
             "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(muxed)],
            check=True, capture_output=True)
        padded.append(muxed)

    out = concat(padded, tmp_path / "joined-padded.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration,nb_frames,avg_frame_rate",
         "-of", "json", str(out)], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert float(stream["duration"]) == pytest.approx(0.4, abs=0.02)
    assert int(stream["nb_frames"]) == 12
    assert stream["avg_frame_rate"] == "30/1"


def test_render_segments_reuses_valid_completed_part(tmp_path, monkeypatch):
    """장편 재개 시 길이가 맞는 완료 청크는 다시 렌더하지 않는다."""
    work = tmp_path / "chunks"
    work.mkdir()
    completed = work / "seg-000.mp4"
    _mk_clip(completed, seconds=0.2, color="red")
    rendered = []

    def fake_render(_props, out, **_kwargs):
        rendered.append(Path(out).name)
        _mk_clip(Path(out), seconds=0.2, color="blue")
        return Path(out)

    monkeypatch.setattr("brushvid.render.render", fake_render)
    props = tmp_path / "props.json"
    props.write_text("{}", encoding="utf-8")
    out = render_segments(props, tmp_path / "joined.mp4", [(0, 5), (6, 11)],
                          work_dir=work, fps=30.0)
    assert out.is_file()
    assert rendered == ["seg-001.mp4"]
    assert probe_duration(out) == pytest.approx(0.4, abs=0.1)


def test_remotion_browser_uses_runtime_manifest(tmp_path):
    engine = tmp_path / "draw_proxy" / "engine"
    browser = tmp_path / "draw_proxy" / "runtime" / "remotion-browser" / "browser"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"browser")
    (browser.parent / "browser.json").write_text(
        json.dumps({"schemaVersion": 1, "executable": str(browser)}), encoding="utf-8")
    assert remotion_browser_executable(engine) == browser


def test_manifest_and_contact_sheet(tmp_path):
    """capture-manifest.json 작성 → 콘택트시트 생성."""
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    entries = []
    for f, color in [(0, (200, 60, 60)), (30, (60, 60, 200)), (59, (60, 200, 60))]:
        name = f"frame-{f:05d}.png"
        Image.new("RGB", (320, 180), color).save(qa_dir / name)
        entries.append({"frame": f, "file": name, "label": f"check-{f}"})
    manifest = write_manifest(entries, qa_dir, project_id="demo", props="data/demo/props.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["projectId"] == "demo"
    assert len(data["captures"]) == 3

    sheet = contact_sheet(qa_dir, cols=2)
    assert sheet.is_file()
    assert Image.open(sheet).width > 320


def test_capture_video_frames_uses_global_frame_numbers(tmp_path, clips):
    qa_dir = tmp_path / "qa-video"
    entries = capture_video_frames(clips[0], [0, 3], qa_dir,
                                   labels={3: "scene-01 touch"}, fps=30, workers=2)
    assert [entry["frame"] for entry in entries] == [0, 3]
    assert entries[1]["label"] == "scene-01 touch"
    assert all((qa_dir / entry["file"]).is_file() for entry in entries)


def test_public_roots_for_props_only_selects_referenced_top_levels(tmp_path):
    public = tmp_path / "public"
    for name in ("project-a", "brush-draw", "unrelated-large-project"):
        (public / name).mkdir(parents=True)
    props = tmp_path / "props.json"
    props.write_text(json.dumps({
        "projectId": "project-a",
        "brush": {"src": "brush-draw/brush.png"},
        "scenes": [{"routes": "project-a/routes/scene.routes.json"}],
    }), encoding="utf-8")
    assert [p.name for p in public_roots_for_props(props, public)] == ["brush-draw", "project-a"]


def test_completion_timing_and_samples_avoid_outro():
    timing = completion_timing({
        "id": "scene-01", "durationInFrames": 300,
        "developFrames": 36, "colorSettleFrames": 18, "outroFadeFrames": 24,
    }, {"meta": {"drawStart": 6, "drawEnd": 206},
        "strokes": [{"end": 206}]})
    assert timing["developEnd"] == 242
    assert timing["colorSettleEnd"] == 260
    assert timing["outroStart"] == 276
    samples = completion_sample_frames(timing)
    assert samples[0] == ("draw-end", 206)
    assert max(frame for _, frame in samples) < 276


def test_completion_report_accepts_monotonic_luma(tmp_path):
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    entries = []
    frames = [206, 224, 242, 251, 260, 262]
    for i, (frame, level) in enumerate(zip(frames, [210, 195, 180, 168, 156, 156])):
        name = f"frame-{frame:05d}.png"
        Image.new("RGB", (64, 36), (level, level, level)).save(qa_dir / name)
        entries.append({"frame": frame, "file": name})
    timing = {"sceneId": "scene-01", "samples": [
        {"label": str(i), "frame": frame} for i, frame in enumerate(frames)
    ]}
    report = write_completion_report(tmp_path / "report.json", timings=[timing],
                                     entries=entries, qa_dir=qa_dir)
    assert report["summary"]["pass"] is True


def test_completion_report_rejects_lighten_then_dark_pulse(tmp_path):
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    entries = []
    frames = [10, 20, 30]
    for frame, level in zip(frames, [150, 205, 130]):
        name = f"frame-{frame:05d}.png"
        Image.new("RGB", (64, 36), (level, level, level)).save(qa_dir / name)
        entries.append({"frame": frame, "file": name})
    timing = {"sceneId": "scene-pulse", "samples": [
        {"label": str(i), "frame": frame} for i, frame in enumerate(frames)
    ]}
    report = write_completion_report(tmp_path / "report.json", timings=[timing],
                                     entries=entries, qa_dir=qa_dir)
    assert report["summary"]["pass"] is False
