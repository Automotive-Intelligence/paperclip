import os, sys, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker import music


def test_build_mix_cmd_shape():
    cmd = music.build_mix_cmd("vo.mp4", "bed.wav", "out.mp4", gain_db=-18.0)
    # stream_loop must precede the MUSIC input (input index 1) so the bed loops
    assert cmd.index("-stream_loop") < cmd.index("bed.wav")
    assert cmd[cmd.index("-stream_loop") + 1] == "-1"
    # video is copied, not re-encoded; audio re-mixed to aac; trimmed to VO length
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-shortest" in cmd
    assert cmd[-1] == "out.mp4"
    # the bed is dropped by the gain, and the two audios are amix'd
    filt = cmd[cmd.index("-filter_complex") + 1]
    assert "volume=-18.0dB" in filt
    assert "amix=inputs=2:duration=first" in filt
    # VO video is input 0, its audio [0:a] is one amix input
    assert "[0:a][m]amix" in filt
    assert cmd[cmd.index("-map") + 1] == "0:v"  # keep the VO's video stream


def test_default_gain_is_under_vo():
    # a bed must sit UNDER the VO; a non-negative gain would drown the voice
    assert music.DEFAULT_GAIN_DB < 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg + ffprobe required (present in the worker image / local Mac)")
def test_mix_music_bed_real(tmp_path):
    vo = str(tmp_path / "vo.mp4")
    bed = str(tmp_path / "bed.wav")
    out = str(tmp_path / "out.mp4")
    # a 3s VO clip (440Hz tone as the voice) and a longer 12s bed (220Hz) to prove looping/trim
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=3:s=160x120",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", vo], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=12", bed],
                   check=True)

    assert music.mix_music_bed(vo, bed, out) == out
    assert os.path.getsize(out) > 0

    def dur(path):
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nw=1:nk=1", path], capture_output=True, text=True).stdout or 0)

    def has_stream(path, kind):  # kind: "v" or "a"
        return bool(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip())

    # mixed output keeps the VO's length (bed looped-then-trimmed), and has both streams
    assert abs(dur(out) - dur(vo)) < 0.3
    assert has_stream(out, "v") and has_stream(out, "a")
