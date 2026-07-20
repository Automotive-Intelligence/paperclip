#!/usr/bin/env python3
"""video_review_sheet.py — the visual review gate's eyes.

Extracts a labeled contact sheet (one frame every N seconds, timestamp burned on)
from a finished video so a reviewer can read the ENTIRE video's imagery at a glance
and catch what a signal check never will: unintended readings, stereotype
landmines, audio/visual semantic collisions, foreign settings, employer branding,
wrong on-screen text.

This exists because Michael has zero time to scrub 12 videos, and a resolution
check does not see that "dealer" (spoken) + a man stepping out of a Cadillac
(shown) reads as the wrong kind of dealer. A human-or-model set of EYES does.
The sheet makes that review fast and auditable: every sheet is saved, so the pass
is a receipt, not a promise.

Usage: python3 video_review_sheet.py <video.mp4> [<out.png>] [--step 2.0] [--cols 6]
"""
import argparse, os, pathlib, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=nw=1:nk=1", str(p)],
        capture_output=True, text=True).stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--step", type=float, default=2.0, help="seconds between frames")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--tw", type=int, default=240, help="tile width px")
    args = ap.parse_args()

    vid = pathlib.Path(args.video)
    out = pathlib.Path(args.out) if args.out else vid.with_suffix(".review.png")
    total = dur(vid)
    times = [round(t, 2) for t in _frange(1.0, total, args.step)]
    th = int(args.tw * 16 / 9)

    tiles = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            f = pathlib.Path(td) / f"{i:03d}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(vid),
                            "-frames:v", "1", "-vf", f"scale={args.tw}:{th}", "-update", "1", str(f)],
                           check=False)
            if f.exists():
                tiles.append((t, Image.open(f).convert("RGB").copy()))

    cols = args.cols
    rows = (len(tiles) + cols - 1) // cols
    lab = 22
    grid = Image.new("RGB", (cols * args.tw, rows * (th + lab)), (12, 12, 14))
    d = ImageDraw.Draw(grid)
    try:
        fnt = ImageFont.truetype(os.environ.get("LABEL_FONT", "/opt/media/fonts/InterTight.ttf"), 15)
    except OSError:
        fnt = ImageFont.load_default()
    for i, (t, im) in enumerate(tiles):
        x, y = (i % cols) * args.tw, (i // cols) * (th + lab)
        grid.paste(im, (x, y + lab))
        d.rectangle([x, y, x + args.tw, y + lab], fill=(20, 20, 24))
        d.text((x + 5, y + 3), f"{int(t//60)}:{t%60:04.1f}", fill=(235, 235, 240), font=fnt)
    grid.save(out)
    print(f"{out}  ({len(tiles)} frames @ {args.step}s, {total:.0f}s video)")


def _frange(a, b, step):
    x = a
    while x < b:
        yield x
        x += step


if __name__ == "__main__":
    main()
