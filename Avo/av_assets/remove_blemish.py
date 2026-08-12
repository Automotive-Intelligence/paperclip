"""Remove the raised blemishes near Michael's right eye from the AIPG beat.

Same discipline as the embroidery removal: MEASURE, then detect, then heal by
diffusion inpainting. Measured at t=11.5 on the clean beat:
    bumps      high-pass(r3) max 44 / 34 / 12
    smooth skin high-pass(r3) max  1 -  3
That gap is the detector.

Faces are higher risk than fabric, so three guards keep it off anything that is
not a blemish:
  * WARM SKIN ONLY (R-B > 25). The sclera of an eye is bright but neutral, so
    it never qualifies.
  * BRIGHTER THAN SURROUNDINGS only (positive high-pass). Beard hairs, brows,
    nostrils and lashes are DARKER, so they are structurally excluded.
  * SMOOTH NEIGHBOURHOOD only (low local variance). Stubble is high-variance
    even where it is light, so the beard is excluded even for grey hairs.
Anything that survives all three is a small light bump on clean cheek or temple.
"""
import pathlib
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageFilter

AD = pathlib.Path("/Users/michaelrodriguez/paperclip/Avo/av_assets")
SRC = AD / "aipg_missedcall_v3_clean.mp4"
DST = AD / "aipg_missedcall_v3_final.mp4"
WORK = pathlib.Path("/private/tmp/claude-501/-Users-michaelrodriguez"
                    "/6a9e04ab-2371-426f-96b6-bffce06f2b44/scratchpad/blemwork")

HP_MIN = 15          # bumps measure 34-44; clean skin 1-3
WARM_MIN = 25        # R-B; skin is warm, sclera and teeth are not
VAR_MAX = 90         # local variance ceiling; stubble is far noisier than cheek
MIN_PX = 12


def source_fps():
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                          str(SRC)], capture_output=True, text=True).stdout.strip()
    n, d = (out.split("/") + ["1"])[:2]
    return float(n) / float(d)


FPS = source_fps()


def blemish_mask(im):
    rgb = np.asarray(im, np.float32)
    gray = np.asarray(im.convert("L"), np.float32)

    warm = (rgb[:, :, 0] - rgb[:, :, 2]) > WARM_MIN

    # A bump must sit INSIDE a large expanse of skin. Without this the mask also
    # fired on sunset-lit tree branches and the hairline: warm backlight reads
    # "warm" too. Eroding at quarter scale keeps only broad skin interiors, so
    # thin foliage and the scalp edge vanish while the cheek and temple survive.
    small = im.resize((im.width // 4, im.height // 4), Image.BILINEAR)
    ss = np.asarray(small.filter(ImageFilter.GaussianBlur(3)), np.float32)
    skin_s = ((ss[:, :, 0] - ss[:, :, 2]) > WARM_MIN) & (ss.mean(2) > 60) & (ss.mean(2) < 210)
    skin_s = Image.fromarray((skin_s * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(9))
    skin_interior = np.asarray(skin_s.resize(im.size, Image.BILINEAR), np.uint8) > 127

    g8 = Image.fromarray(gray.astype(np.uint8))
    blur = np.asarray(g8.filter(ImageFilter.GaussianBlur(3)), np.float32)
    hp = gray - blur

    # local variance, via E[x^2] - E[x]^2 on a wider blur
    mean = np.asarray(g8.filter(ImageFilter.GaussianBlur(7)), np.float32)
    sq = Image.fromarray(np.clip(gray * gray / 255.0, 0, 255).astype(np.uint8))
    mean_sq = np.asarray(sq.filter(ImageFilter.GaussianBlur(7)), np.float32) * 255.0
    var = np.clip(mean_sq - mean * mean, 0, None)
    smooth = var < VAR_MAX

    m = ((hp > HP_MIN) & warm & skin_interior & smooth & (gray > 70)).astype(np.uint8) * 255
    m = np.asarray(Image.fromarray(m).filter(ImageFilter.MaxFilter(9)), np.uint8)
    return m > 0


def heal(im, mask, iters=70, radius=3, pad=22):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return im
    x0, x1 = max(0, xs.min() - pad), min(im.width, xs.max() + pad + 1)
    y0, y1 = max(0, ys.min() - pad), min(im.height, ys.max() + pad + 1)
    ctx = im.crop((x0, y0, x1, y1))
    sub = mask[y0:y1, x0:x1]
    arr = np.asarray(ctx, np.float32)
    known = ~sub
    if not known.any():
        return im
    orig, cur = arr.copy(), arr.copy()
    cur[sub] = arr[known].mean(0)
    for _ in range(iters):
        b = np.asarray(Image.fromarray(cur.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius)), np.float32)
        cur[sub] = b[sub]
        cur[known] = orig[known]
    soft = Image.fromarray((sub * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    ctx.paste(Image.fromarray(cur.astype(np.uint8)), (0, 0), soft)
    out = im.copy()
    out.paste(ctx, (x0, y0))
    return out


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(SRC),
                    str(WORK / "f_%04d.png")], check=True)
    frames = sorted(WORK.glob("f_*.png"))
    print(f"{len(frames)} frames @ {FPS:g}fps", flush=True)

    touched = 0
    for fp in frames:
        im = Image.open(fp).convert("RGB")
        m = blemish_mask(im)
        if m.sum() >= MIN_PX:
            heal(im, m).save(fp)
            touched += 1
    print(f"blemishes healed on {touched}/{len(frames)} frames", flush=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{FPS:g}",
                    "-i", str(WORK / "f_%04d.png"), "-i", str(SRC),
                    "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-shortest", str(DST)], check=True)
    print("wrote", DST, flush=True)


if __name__ == "__main__":
    main()
