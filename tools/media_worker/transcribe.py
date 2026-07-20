"""Formalize the transcribe step (was manual, documented only in comments).

take.mp4 -> 16k mono WAV -> whisper-cli -ml 1 -sow -oj -> <name>.json, in the
schema scripts/cut_talking_head.py:load_words() consumes."""
from __future__ import annotations
import os, pathlib, subprocess
from typing import List


def wav_extract_argv(take: str, wav: str) -> List[str]:
    return ["ffmpeg", "-v", "error", "-y", "-i", take,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav]


def whisper_argv(model: str, wav: str, out_base: str, binary: str = "whisper-cli") -> List[str]:
    # -ml 1 -sow: one word per segment with real timestamps. -oj: JSON output at <out_base>.json.
    return [binary, "-m", model, "-ml", "1", "-sow", "-oj", "-f", wav, "-of", out_base]


def transcribe(take: str, model: str, workdir: str) -> str:
    os.makedirs(workdir, exist_ok=True)
    name = pathlib.Path(take).stem
    wav = os.path.join(workdir, f"{name}.16k.wav")
    out_base = os.path.join(workdir, name)
    subprocess.run(wav_extract_argv(take, wav), check=True)
    subprocess.run(whisper_argv(model, wav, out_base), check=True)
    return f"{out_base}.json"
