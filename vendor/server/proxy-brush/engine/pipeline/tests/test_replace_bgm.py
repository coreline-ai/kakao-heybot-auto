import importlib.util
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "replace-bgm.py"
spec = importlib.util.spec_from_file_location("replace_bgm", SCRIPT)
replace_bgm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replace_bgm)


def test_mux_replacement_preserves_video_stream(tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x180:r=30:d=1", "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(audio)], check=True)
    asset = {"title": "Track", "artist": "Artist", "license": {
        "name": "CC BY 4.0", "attributionText": "Track by Artist | CC BY 4.0",
    }}
    before = replace_bgm.video_stream_hash(video)
    replace_bgm.mux_replacement(video, audio, output, duration_sec=1,
                                title="Demo", asset=asset)
    assert replace_bgm.video_stream_hash(output) == before
