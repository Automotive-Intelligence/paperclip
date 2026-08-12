"""Assemble the AIPG cinematic SHORT from the one gate-passed beat.

Credits ran out after the hero beat, so this is the honest cut that costs
nothing more: the missed-call beat (12.1s, file-133 PASS) carrying a VO written
to fit it, in the same film grammar as the AvI brand film. The closing lines
move to the end card, which is where the tracked number belongs anyway.

The beat's own generated audio is DISCARDED; Michael's real ElevenLabs voice is
the only audio that ships.
"""
import os
import pathlib
import subprocess

import requests

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from brand_kit import kit, font as kfont, rgb  # noqa: E402

K = kit("aiphoneguy")          # fonts, colors, logos, CTA. Never hardcode these.

AD = pathlib.Path("/Users/michaelrodriguez/paperclip/Avo/av_assets")
BEAT = AD / "aipg_missedcall_v3.mp4"
VO = AD / "aipg_vo_short.mp3"
OUT = pathlib.Path("/Users/michaelrodriguez/avo-telemetry/marketing_deliverables"
                   "/116_video_leg_activation/renders/aipg_cinematic_short_9x16.mp4")
W, H, FPS = 1080, 1920, 30
# Don's funnel-#1 brief requires BOTH. 1:1 is a centre crop of the same beat with
# captions and end card re-laid out, never a squashed 9:16.
ASPECTS = {"9x16": (1080, 1920), "1x1": (1080, 1080)}

# Sized to the 12.1s beat. The payoff ("Missed calls are the problem. We end
# them.") moves to the end card so the VO is not rushed.
SCRIPT = ("When your hands are full, the phone still rings. "
          "And the customer who cannot reach you just calls the next name on Google. "
          "I built a system that answers every call and books the job. "
          "Call the line and hear it work.")

CAPTIONS = [
    (0.35, 3.10, ["WHEN YOUR HANDS ARE FULL,", "THE PHONE STILL RINGS"]),
    (3.25, 7.40, ["THE CUSTOMER WHO CANNOT REACH YOU", "CALLS THE NEXT NAME ON GOOGLE"]),
    (7.55, 11.20, ["I BUILT A SYSTEM THAT ANSWERS", "AND BOOKS THE JOB"]),
    (11.35, 12.05, ["CALL THE LINE", "AND HEAR IT WORK"]),
]
MAX_W = K["caption"]["max_width"]
TRACKING = K["caption"]["tracking"]
TEXT_Y = K["caption"]["baseline_y"]


def _canvas(aspect):
    return ASPECTS[aspect]


def _text_y(aspect):
    # 1:1 crops the vertical centre, so the 9:16 baseline would fall outside it.
    return TEXT_Y if aspect == "9x16" else 900


def make_vo():
    """Cache the VO. Earlier builds re-billed ElevenLabs on every cosmetic
    rebuild (font swap, logo add), which is pure waste: the script had not
    changed. Regenerates only when SCRIPT changes, keyed by its hash."""
    import hashlib
    stamp = AD / "aipg_vo_short.script.sha"
    digest = hashlib.sha256(SCRIPT.encode()).hexdigest()
    if VO.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(VO)], capture_output=True, text=True)
        print("VO cached (script unchanged), no API call", flush=True)
        return float(d.stdout.strip())
    key = os.environ["ELEVENLABS_API_KEY"].strip()
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/SEwFJO9DaRPtRkTiwanx",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"text": SCRIPT, "model_id": "eleven_multilingual_v2",
              "voice_settings": {"stability": 0.45, "similarity_boost": 0.85,
                                 "style": 0.3, "use_speaker_boost": True}},
        timeout=180)
    r.raise_for_status()
    VO.write_bytes(r.content)
    stamp.write_text(digest)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(VO)], capture_output=True, text=True)
    return float(d.stdout.strip())


def _tw(d, s, f):
    return sum(d.textlength(c, font=f) for c in s) + TRACKING * (len(s) - 1)


def _tracked(d, x, y, s, f, tracking, stroke, sfill):
    for c in s:
        d.text((x, y), c, font=f, fill=(255, 255, 255, 255),
               stroke_width=stroke, stroke_fill=sfill)
        x += d.textlength(c, font=f) + tracking


def build_cards(aspect):
    from PIL import Image, ImageDraw, ImageFont
    out = []
    for i, (_, _, lines) in enumerate(CAPTIONS):
        cw, ch = _canvas(aspect)
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        size = 62
        while size >= 40:
            f = kfont(K, "display", size)
            widths = [_tw(d, l, f) for l in lines]
            if max(widths) <= MAX_W:
                break
            size -= 2
        lh = size * 1.42
        y = _text_y(aspect) - (lh * len(lines)) / 2
        for l, wd in zip(lines, widths):
            _tracked(d, (cw - wd) / 2, y, l, f, TRACKING, 4, (6, 9, 12, 225))
            y += lh
        _paste_corner(img)
        p = AD / f"short_cap_{aspect}_{i}.png"
        img.save(p)
        out.append(p)
    return out


def _paste_corner(img):
    """AIPG mascot chip, top-left, on every caption card so the brand is on
    screen for the whole film rather than only the last two seconds."""
    from PIL import Image
    cw = K["corner_logo"]["width_px"]
    m = K["corner_logo"]["margin_px"]
    chip = Image.open(K["logos"]["corner"]).convert("RGBA")
    chip = chip.resize((cw, round(cw * chip.height / chip.width)), Image.LANCZOS)
    a = chip.getchannel("A").point(lambda v: int(v * K["corner_logo"]["opacity"]))
    chip.putalpha(a)
    img.alpha_composite(chip, (m, m))


def build_endcard(aspect):
    from PIL import Image, ImageDraw
    C = K["colors"]
    cw, ch = _canvas(aspect)
    img = Image.new("RGBA", (cw, ch), rgb(C["ink"]) + (255,))
    d = ImageDraw.Draw(img)
    # 1:1 has 840px less height, so the lockup shrinks and rides higher or
    # the URL row falls off the bottom edge (it did on the first square cut).
    lock_top = 560 if aspect == "9x16" else 120

    # The brand LOCKUP is the hero of the card (mascot + wordmark), sized to
    # the frame rather than a text-only card with no mark on it anywhere.
    lock = Image.open(K["logos"]["endcard"]).convert("RGBA")
    lw = 860 if aspect == "9x16" else 620
    lock = lock.resize((lw, round(lw * lock.height / lock.width)), Image.LANCZOS)
    img.alpha_composite(lock, ((cw - lw) // 2, lock_top))

    fb = kfont(K, "display", 66)
    fm = kfont(K, "body", 46)
    fs = kfont(K, "body", 36)
    base = lock_top + lock.height + 70
    rows = [("MISSED CALLS ARE THE PROBLEM.", fm, rgb(C["muted"]), 0),
            ("WE END THEM.", fb, rgb(C["paper"]), 78),
            (K["cta"]["phone"], fb, rgb(C["accent"]), 232),
            (K["cta"]["url"], fs, rgb(C["muted"]), 322)]
    for text, f, color, dy in rows:
        wdt = _tw(d, text, f)
        _x = (cw - wdt) / 2
        for c in text:
            d.text((_x, base + dy), c, font=f, fill=color)
            _x += d.textlength(c, font=f) + TRACKING
    d.line([(cw / 2 - 130, base + 186), (cw / 2 + 130, base + 186)],
           fill=rgb(C["accent"]), width=4)
    p = AD / f"short_endcard_{aspect}.png"
    img.convert("RGB").save(p)
    return p


def main():
    vo_len = make_vo()
    body = 12.05
    card = max(2.6, vo_len - body + 1.9)
    total = body + card
    print(f"VO {vo_len:.2f}s | beat {body:.2f}s | card {card:.2f}s | total {total:.2f}s",
          flush=True)

    for aspect, (cw, ch) in ASPECTS.items():
        caps = build_cards(aspect)
        ec = build_endcard(aspect)
        out = OUT.parent / f"aipg_cinematic_short_{aspect}.mp4"

        inputs = ["-i", str(BEAT), "-i", str(VO),
                  "-loop", "1", "-framerate", str(FPS), "-t", f"{card + 0.5:.3f}", "-i", str(ec)]
        for c in caps:
            inputs += ["-loop", "1", "-framerate", str(FPS),
                       "-t", f"{total + 1:.3f}", "-i", str(c)]

        # 1:1 is a CENTRE CROP of the same 1080x1920 beat, never a squash.
        crop = "" if aspect == "9x16" else f",crop={cw}:{ch}:0:{(1920 - ch) // 2}"
        g = f"[0:v]trim=0:{body:.3f},setpts=PTS-STARTPTS{crop}[bv];"
        cur = "bv"
        for j, (a, b, _) in enumerate(CAPTIONS):
            g += (f"[{cur}][{3 + j}:v]overlay=0:0:shortest=1:"
                  f"enable='between(t,{a},{min(b, body):.3f})'[c{j}];")
            cur = f"c{j}"
        g += (f"[2:v]trim=duration={card:.3f},setpts=PTS-STARTPTS[ecv];"
              f"[{cur}][ecv]concat=n=2:v=1[outv];"
              f"[1:a]apad=whole_dur={total:.3f},atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
              "loudnorm=I=-16:TP=-1.5:LRA=11[outa]")

        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", g,
                        "-map", "[outv]", "-map", "[outa]", "-r", str(FPS),
                        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        "-t", f"{total:.3f}", str(out)], check=True)
        print("rendered:", out.name, flush=True)


if __name__ == "__main__":
    main()
