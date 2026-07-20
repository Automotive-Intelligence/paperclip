"""Render one take end-to-end: transcribe -> cut -> contact sheet. No scheduling."""
from __future__ import annotations
import os, pathlib, subprocess
from tools.media_worker.transcribe import transcribe
from tools.media_worker.edit_spec import build_cut_argv


def render_one(edit: dict, take: str, model: str, out_dir: str,
               cut_script: str, sheet_script: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    stem = pathlib.Path(take).stem
    words = transcribe(take, model, out_dir)
    master = os.path.join(out_dir, f"{stem}.mp4")
    subprocess.run(build_cut_argv(edit, take, words, master, script=cut_script), check=True)
    sheet = os.path.join(out_dir, f"{stem}.review.png")
    subprocess.run(["python3", sheet_script, master, sheet], check=True)
    return {"master": master, "sheet": sheet, "words": words}
