"""Remove the fictional "Renno" chest embroidery from the AIPG hero beat.

The generator stitched a made-up company name onto Michael's shirt. Fine at
organic playback size, but this is going into PAID placements where it renders
large and a competitor can screenshot it, so it comes out.

He moves and the camera pushes in, so a static mask will not hold. This tracks
the patch by template matching (SAD on downsampled luma, local search seeded
from a hand-pinned frame, run backward and forward), then smooths the box into
the surrounding fabric with a feathered blur rather than a hard paste. Frames
where the match confidence collapses (he turns away, the patch is occluded) are
SKIPPED rather than smudged at a guessed position.

Costs nothing: pure PIL/numpy + ffmpeg. No generation credits.
"""
import pathlib
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFilter

AD = pathlib.Path("/Users/michaelrodriguez/paperclip/Avo/av_assets")
SRC = AD / "aipg_missedcall_v3.mp4"
DST = AD / "aipg_missedcall_v3_clean.mp4"
WORK = pathlib.Path("/private/tmp/claude-501/-Users-michaelrodriguez"
                    "/6a9e04ab-2371-426f-96b6-bffce06f2b44/scratchpad/patchwork")

def source_fps():
    """NEVER assume 30. HeyGen cinematic renders are 24fps; reassembling at 30
    sped a 12.1s clip to 9.6s and would have desynced the whole film."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                          str(SRC)], capture_output=True, text=True).stdout.strip()
    num, den = (out.split("/") + ["1"])[:2]
    return float(num) / float(den)


FPS = source_fps()
# Hand-pinned on the frame at t=11.5 (see zoomed crop), padded around the text.
PIN_T = 11.5
BOX = (548, 1040, 92, 38)          # x, y, w, h
SEARCH = 70                        # +/- px local search per frame
SAD_LIMIT = 26.0                   # mean abs diff per px; above this = lost, skip


def frames_out():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(SRC),
                    str(WORK / "f_%04d.png")], check=True)
    return sorted(WORK.glob("f_*.png"))


def luma(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def match(gray, tmpl, cx, cy):
    """Best (x, y) for tmpl near (cx, cy), plus its mean abs difference."""
    th, tw = tmpl.shape
    H, W = gray.shape
    x0, x1 = max(0, cx - SEARCH), min(W - tw, cx + SEARCH)
    y0, y1 = max(0, cy - SEARCH), min(H - th, cy + SEARCH)
    best, bx, by = 1e9, cx, cy
    for y in range(y0, y1 + 1, 2):
        row = gray[y:y + th]
        for x in range(x0, x1 + 1, 2):
            d = np.abs(row[:, x:x + tw] - tmpl).mean()
            if d < best:
                best, bx, by = d, x, y
    return bx, by, best


def heal(img, x, y, w, h, pad=34, iters=90, radius=4, grow=4):
    """Diffusion inpaint. Hold the pixels OUTSIDE the hole fixed and repeatedly
    blur, so surrounding fabric propagates inward and the text is never a source
    pixel. Blurring or median-filtering the region could NOT remove the text:
    those spread its brightness rather than replacing it, and a legible ghost
    survived every strength tried (blur 11/26, median 15/21)."""
    X0, Y0 = max(0, x - pad), max(0, y - pad)
    X1, Y1 = min(img.width, x + w + pad), min(img.height, y + h + pad)
    ctx = img.crop((X0, Y0, X1, Y1))
    arr = np.asarray(ctx, np.float32)

    hole = np.zeros(arr.shape[:2], bool)
    hole[(y - Y0 - grow):(y - Y0 + h + grow), (x - X0 - grow):(x - X0 + w + grow)] = True
    known = ~hole
    orig = arr.copy()
    cur = arr.copy()
    cur[hole] = arr[known].mean(0)
    for _ in range(iters):
        b = np.asarray(Image.fromarray(cur.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius)), np.float32)
        cur[hole] = b[hole]
        cur[known] = orig[known]

    m = np.zeros(arr.shape[:2], np.uint8)
    m[hole] = 255
    mask = Image.fromarray(m).filter(ImageFilter.GaussianBlur(7))
    ctx.paste(Image.fromarray(cur.astype(np.uint8)), (0, 0), mask)
    img.paste(ctx, (X0, Y0))
    return img


def main():
    frames = frames_out()
    n = len(frames)
    pin_i = min(n - 1, int(PIN_T * FPS))
    print(f"{n} frames; pinned on frame {pin_i}", flush=True)

    x, y, w, h = BOX
    tmpl = luma(Image.open(frames[pin_i]))[y:y + h, x:x + w]

    positions = {}
    for direction in (range(pin_i, -1, -1), range(pin_i, n)):
        cx, cy = x, y
        for i in direction:
            g = luma(Image.open(frames[i]))
            bx, by, sad = match(g, tmpl, cx, cy)
            if sad <= SAD_LIMIT:
                positions[i] = (bx, by)
                cx, cy = bx, by
            # lost: keep searching from the last good spot, do not paint a guess

    print(f"patch located on {len(positions)}/{n} frames", flush=True)
    for i, (px, py) in positions.items():
        im = Image.open(frames[i]).convert("RGB")
        heal(im, px, py, w, h).save(frames[i])

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{FPS:g}",
                    "-i", str(WORK / "f_%04d.png"), "-i", str(SRC),
                    "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-shortest", str(DST)], check=True)
    print("wrote", DST, flush=True)


if __name__ == "__main__":
    sys.exit(main())
