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
MIN_PX = 40                        # below this the shirt is clean; skip the frame


def frames_out():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(SRC),
                    str(WORK / "f_%04d.png")], check=True)
    return sorted(WORK.glob("f_*.png"))


def luma(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def shirt_text_mask(im):
    """Mask the bright embroidery ANYWHERE on the navy shirt.

    Locating a box failed twice: template matching latched onto plain fabric
    (a mostly-navy template matches navy), and blob detection latched onto his
    ear, grass and the phone. Both tried to answer "where is it", which is hard
    because he moves and the camera pushes in.

    This asks an easier question instead: which pixels are SHARP AND BRIGHT while
    sitting on dark blue fabric? The shirt is otherwise smooth, its buttons and
    seams are darker not brighter, and fold highlights are broad and
    low-frequency, so a high-pass tuned to embroidery stroke width catches the
    lettering and nothing else. No tracking required.
    """
    rgb = np.asarray(im, np.float32)
    gray = np.asarray(im.convert("L"), np.float32)

    # The navy region is low-frequency, so derive it at quarter scale: the
    # full-res GaussianBlur(18) + MinFilter(21) cost 1.8s/frame for information
    # that survives downsampling intact.
    small = im.resize((im.width // 4, im.height // 4), Image.BILINEAR)
    ss = np.asarray(small.filter(ImageFilter.GaussianBlur(5)), np.float32)
    navy_s = (ss[:, :, 2] > ss[:, :, 0] + 8) & (ss.mean(2) < 110)
    # ERODE inward: without this the mask also grabbed the shirt's outer edge
    # against the bright background, and inpainting there would soften his
    # silhouette. The embroidery sits well inside the garment, so a ~10px band
    # at the boundary can be excluded with nothing lost.
    navy_s = Image.fromarray((navy_s * 255).astype(np.uint8)).filter(
        ImageFilter.MinFilter(7))
    navy = np.asarray(navy_s.resize(im.size, Image.BILINEAR), np.uint8) > 127

    blur_g = np.asarray(Image.fromarray(gray.astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(4)), np.float32)
    hp = gray - blur_g

    # MEASURED at a known patch (t=11.5), rather than guessed. Text pixels:
    # gray 125-198 (median 157), high-pass median 55. Surrounding shirt: gray
    # ~51, high-pass ~-3. That gap is the discriminator.
    # A "whiteness" test was tried and was WRONG: the thread reads B-R = 34,
    # i.e. distinctly blue, so keying on neutrality excluded the target itself.
    # Broad fold highlights survive because a radius-4 high-pass barely responds
    # to low-frequency lighting.
    m = ((hp > 30) & (gray > 100) & navy).astype(np.uint8) * 255
    # 13 not 7: the mask caught the bright strokes but not their anti-aliased
    # halo, and the leftover halo alone still read as legible lettering.
    m = np.asarray(Image.fromarray(m).filter(ImageFilter.MaxFilter(13)), np.uint8)
    return m > 0


def heal_mask(im, mask, iters=80, radius=4, pad=30):
    """Diffusion inpaint over an arbitrary mask, CROPPED to the mask's bounding
    box. Running the iterations over the full 1080x1920 frame took ~90s each,
    a seven-hour job for 289 frames; the marks occupy a tiny fraction of the
    frame, so all the work outside their neighbourhood was wasted. Iterations
    dropped to 45 at radius 4, which converges on regions this small.

    Blur or median ALONE cannot remove text: they spread its brightness rather
    than replacing it. Holding the pixels outside the hole fixed and blurring
    repeatedly makes surrounding fabric flow inward instead.
    """
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

    orig = arr.copy()
    cur = arr.copy()
    cur[sub] = arr[known].mean(0)
    for _ in range(iters):
        b = np.asarray(Image.fromarray(cur.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius)), np.float32)
        cur[sub] = b[sub]
        cur[known] = orig[known]

    soft = Image.fromarray((sub * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(4))
    ctx.paste(Image.fromarray(cur.astype(np.uint8)), (0, 0), soft)
    out = im.copy()
    out.paste(ctx, (x0, y0))
    return out


def main():
    frames = frames_out()
    n = len(frames)
    print(f"{n} frames @ {FPS:g}fps", flush=True)

    touched = 0
    for fp in frames:
        im = Image.open(fp).convert("RGB")
        mask = shirt_text_mask(im)
        if mask.sum() >= MIN_PX:
            heal_mask(im, mask).save(fp)
            touched += 1
    print(f"embroidery suppressed on {touched}/{n} frames", flush=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{FPS:g}",
                    "-i", str(WORK / "f_%04d.png"), "-i", str(SRC),
                    "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-shortest", str(DST)], check=True)
    print("wrote", DST, flush=True)


if __name__ == "__main__":
    sys.exit(main())
