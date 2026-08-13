"""Word-level animated captions (the Hormozi / karaoke style) for AIPG creative.

Michael's note: he hated the old captions. They were static two-line cards that
sat there for four seconds at a time, which reads as a subtitle track rather
than as social creative. The reference he pointed at (github.com/topics/
ai-captions) is dominated by one pattern, and the leading open-source entry
states the stack outright: Whisper for word timings + FFmpeg for burn-in, with
word-level animation. We already have both, so this needs no new dependency,
no SaaS subscription and no per-render cost.

How it works:
  * whisper.cpp token offsets give per-WORD timings off the VO we already cache.
  * Words are grouped into short chunks (3 by default). Only the chunk is on
    screen, so the eye never has to read a paragraph.
  * The word being spoken RIGHT NOW is drawn in the brand accent and scaled up
    slightly; the rest of the chunk stays white. That is the whole effect.
  * The layer is rendered as ONE transparent video, not N overlay inputs. A
    45-word script would otherwise mean 45 looped image inputs in a single
    ffmpeg command.

Only unique word-states are rasterised (about one per word) and reused across
frames, so a 12s 30fps layer costs ~45 renders rather than 360.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

_SKIP = re.compile(r"^\s*(\[.*\]|[^\w]*)\s*$")     # [_BEG_], bare punctuation


def word_timings(audio: pathlib.Path, model: Optional[pathlib.Path] = None
                 ) -> List[Tuple[float, float, str]]:
    """(start, end, WORD) from whisper.cpp token offsets."""
    model = model or (pathlib.Path.home() /
                      "stock_library/.whisper_models/ggml-small.en.bin")
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(audio),
                        "-ar", "16000", "-ac", "1", str(wav)], check=True)
        out = pathlib.Path(td) / "a"
        subprocess.run(["whisper-cli", "-m", str(model), "-f", str(wav),
                        "-ojf", "-of", str(out)],
                       check=True, capture_output=True)
        data = json.loads((out.with_suffix(".json")).read_text())

    words: List[Tuple[float, float, str, int]] = []
    for si, seg in enumerate(data["transcription"]):
        for tok in seg.get("tokens", []):
            raw = tok.get("text", "")
            if _SKIP.match(raw):
                # Punctuation is not noise: it marks where a phrase ends.
                # Dropping it produced "ARE FULL THE", which hangs the next
                # clause's first word off the previous one. Record a break.
                if words and re.search(r"[,.!?;:]", raw):
                    a_, b_, w_, s_ = words[-1]
                    words[-1] = (a_, b_, w_, s_ + 1000)   # +1000 = break marker
                continue
            w = re.sub(r"[^\w'|-]", "", raw).strip()
            if not w:
                continue
            a = tok["offsets"]["from"] / 1000.0
            b = tok["offsets"]["to"] / 1000.0
            # whisper splits some words into pieces; a piece with no leading
            # space continues the previous word rather than starting a new one.
            if words and not raw.startswith(" ") and words[-1][3] == si:
                pa, _, pw, ps = words[-1]
                words[-1] = (pa, max(b, words[-1][1]), pw + w, ps)
            else:
                words.append((a, b, w, si))
    # A break marker closes the group; the NEXT word starts a fresh clause id.
    out, clause = [], 0
    for a, b, w, s in words:
        if b <= a:
            continue
        brk = s >= 1000
        out.append((a, b, w.upper(), clause))
        if brk:
            clause += 1
    return out


def chunk(words, size: int = 3):
    """Group within a SENTENCE, never across one.

    Fixed-size grouping over the flat word list produced "ARE FULL THE", which
    hangs the first word of the next sentence off the end of the previous one.
    Whisper already gives sentence segments, so chunks are cut inside them and
    a short remainder is merged back rather than left as an orphan word.
    """
    out = []
    for si in sorted({w[3] for w in words}):
        sent = [w for w in words if w[3] == si]
        groups = [sent[i:i + size] for i in range(0, len(sent), size)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2] = groups[-2] + groups[-1]      # no orphan single word
            groups.pop()
        out.extend(groups)
    return out


def _measure(d: ImageDraw.ImageDraw, text: str, font, tracking: float) -> float:
    return sum(d.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def render_layer(words, canvas: Tuple[int, int], baseline_y: int, fps: float,
                 duration: float, kit: Dict[str, Any], font_for,
                 rgb, out_path: pathlib.Path, chunk_size: int = 3,
                 corner_paint=None) -> pathlib.Path:
    """Write a transparent qtrle .mov of the animated captions."""
    cw, ch = canvas
    accent = rgb(kit["colors"]["accent"])
    tracking = kit["caption"]["tracking"]
    max_w = kit["caption"]["max_width"]
    groups = chunk(words, chunk_size)

    # active-word index -> rendered PNG, so each state rasterises once
    cache: Dict[Tuple[int, int], Image.Image] = {}

    def state(gi: int, wi: int) -> Image.Image:
        key = (gi, wi)
        if key in cache:
            return cache[key]
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        group = groups[gi]

        size = 72
        while size >= 40:
            base_f = font_for(kit, "display", size)
            big_f = font_for(kit, "display", round(size * 1.16))
            widths = [_measure(d, g[2], big_f if i == wi else base_f, tracking)
                      for i, g in enumerate(group)]
            gap = size * 0.34
            if sum(widths) + gap * (len(group) - 1) <= max_w:
                break
            size -= 2

        total = sum(widths) + gap * (len(group) - 1)
        x = (cw - total) / 2
        for i, g in enumerate(group):
            w = g[2]
            f = big_f if i == wi else base_f
            colour = accent if i == wi else (255, 255, 255)
            # active word rides slightly higher as it pops
            y = baseline_y - (size * 0.58) - (size * 0.06 if i == wi else 0)
            for c in w:
                d.text((x, y), c, font=f, fill=colour + (255,),
                       stroke_width=6, stroke_fill=(8, 10, 13, 235))
                x += d.textlength(c, font=f) + tracking
            x += gap
        if corner_paint:
            corner_paint(img)
        cache[key] = img
        return img

    n_frames = int(duration * fps)
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        blank = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if corner_paint:
            corner_paint(blank)
        for n in range(n_frames):
            t = n / fps
            img = blank
            for gi, group in enumerate(groups):
                if group[0][0] <= t <= group[-1][1] + 0.12:
                    wi = 0
                    for i, g in enumerate(group):
                        if t >= g[0]:
                            wi = i
                    img = state(gi, wi)
                    break
            img.save(work / f"c{n:05d}.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}",
                        "-i", str(work / "c%05d.png"), "-c:v", "qtrle",
                        str(out_path)], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path
