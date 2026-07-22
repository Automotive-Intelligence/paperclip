"""tools/media_worker/music.py -- mix a licensed music bed UNDER the VO.

Cloud-native render primitive (built here, never locally first, per charter).
ffmpeg-only: loop the bed to cover the video, drop it well under the spoken VO,
and mix. The video stream is copied (no re-encode); only audio is re-mixed.

Stage-and-flag is unaffected: this is a toolkit primitive a finish step calls,
nothing here publishes. It is deliberately NOT wired into the default talking-
head cut yet -- WHICH track, WHAT gain, and WHETHER a given cut wants a bed are
Studio/Iris creative calls, and the licensed track itself is an asset input
(flagged). This is the ready-to-wire capability, verified in the cloud image
(ffmpeg is present); wiring waits on the track + the creative go."""
from __future__ import annotations

import subprocess

DEFAULT_GAIN_DB = -18.0  # the bed sits well under the spoken VO


def build_mix_cmd(video_in: str, music_in: str, video_out: str,
                  gain_db: float = DEFAULT_GAIN_DB) -> list:
    """The exact ffmpeg argv. Split out so a test can assert the command shape
    without invoking ffmpeg. `-stream_loop -1` precedes the music input so the
    bed loops to cover any video length; `amix ... duration=first` + `-shortest`
    trim the result to the VO's length; `volume=<gain>dB` drops the bed."""
    filt = (f"[1:a]volume={gain_db}dB[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]")
    return [
        "ffmpeg", "-v", "error", "-y",
        "-i", video_in,
        "-stream_loop", "-1", "-i", music_in,
        "-filter_complex", filt,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        video_out,
    ]


def mix_music_bed(video_in: str, music_in: str, video_out: str,
                  gain_db: float = DEFAULT_GAIN_DB) -> str:
    """Mix `music_in` under `video_in`'s existing VO audio and write `video_out`.
    The bed is looped to cover the video, trimmed to the video's length, and
    dropped by `gain_db` so the VO stays in front. Returns video_out. Raises
    subprocess.CalledProcessError on ffmpeg failure."""
    subprocess.run(build_mix_cmd(video_in, music_in, video_out, gain_db), check=True)
    return video_out
