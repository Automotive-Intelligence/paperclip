#!/usr/bin/env python3
"""Slipstream video leg, part 2 — the talking-head cut engine.

build_short.py assembles stock + a cloned voice. THIS engine cuts Michael's real
on-camera takes (Riverside, 4K 16:9) into branded 9:16 shorts:

  CROP      3840x2160 -> center 1215x2160 -> 1080x1920. Michael centers himself on
            the webcam; the studio strike list keeps employer branding out of the
            center third (verified frame-by-frame 2026-07-15).
  WORDS     whisper.cpp small.en with -ml 1 -sow gives one word per segment with
            real timestamps. Captions light the word he is actually saying, in the
            brand's own face and accent, same renderer as build_short.
  CAPTIONS  print what he SAID (the honest cut), with a corrections map for known
            ASR misses of proper nouns (Invoca, TrustedForm, ...). Never "fix" his
            phrasing beyond that.
  FLUBS     last-read-wins. --cut A-B spans (seconds, source time) drop the first
            read of a restarted line; audio and captions stay in sync because word
            times are remapped through the same keep-windows.
  END CARD  the take is extended ~2.4s by cloning the last frame (tpad); the brand
            plate + logo fade in over the freeze. Corner mark rides the whole take.
  AUDIO     his real mic track, loudnorm -16 LUFS. Never the clone over his face.

Usage: python3 cut_talking_head.py <brand> <take.mp4> <words.json> <out.mp4>
           [--hook "ON SCREEN HOOK"] [--cut 61.2-68.4] [--corrections k=v;k=v]
"""
import argparse, json, pathlib, shutil, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HOME  = pathlib.Path.home()
FONTS = HOME / "avo-telemetry" / "assets" / "fonts"
W, H, FPS = 1080, 1920, 30
SRC_W, SRC_H = 3840, 2160
CROP_W = 1215                       # 9:16 slice of the 4K frame
BAND_H, BAND_Y = 460, 1290
SAFE_W = 930

BRANDS = {
    "avi": {"name": "Automotive Intelligence", "font": FONTS / "InterTight.ttf",
            "base": (232, 234, 237), "accent": (45, 212, 191),
            "logo": HOME / "automotive-intelligence-site/public/logo.png",
            "mark": HOME / "automotive-intelligence-site/public/logo-mark.png"},
    "wd":  {"name": "Worship Digital", "font": FONTS / "InterTight.ttf",
            "base": (245, 245, 245), "accent": (212, 175, 55),
            "logo": HOME / "worship-digital-site/public/WD-Logo.png"},
    "agent_empire": {"name": "Agent Empire", "font": FONTS / "SpaceGrotesk.ttf",
            "weight": "Bold", "base": (230, 237, 247), "accent": (56, 189, 248),
            "logo": HOME / "avo-telemetry/assets/brand/bae_lockup_cyan.png",
            "mark": HOME / "avo-telemetry/assets/brand/bae_crown_cyan.png"},
    "bookd": {"name": "Book'd", "font": FONTS / "Montserrat.ttf",
            "base": (255, 255, 255), "accent": (2, 159, 179),
            "logo": HOME / "avo-telemetry/assets/brand/bookd_logo_cyan.png"},
    "aipg": {"name": "The AI Phone Guy", "font": FONTS / "Archivo.ttf",
            "base": (247, 244, 239), "accent": (232, 119, 46),
            "logo": HOME / "ai-phone-guy-site/public/aipg-logo.png"},
}


def run(cmd): subprocess.run(cmd, check=True)


def load_words(path, corrections):
    """[(word, start, end)] in SOURCE time, ASR misses corrected 1:1."""
    segs = json.loads(pathlib.Path(path).read_text())["transcription"]
    out = []
    for s in segs:
        t = s["text"].strip()
        if not t:
            continue
        out.append([corrections.get(t.lower().strip('.,?!"'), t),
                    s["offsets"]["from"] / 1000.0, s["offsets"]["to"] / 1000.0])
    return out


def keep_windows(words, cuts):
    """[(a,b)] source-time spans to keep: lead-in to tail, minus flub cuts."""
    a, b = max(0.0, words[0][1] - 0.35), words[-1][2] + 0.6
    spans, cur = [], a
    for ca, cb in sorted(cuts):
        if ca > cur:
            spans.append((cur, ca))
        cur = max(cur, cb)
    spans.append((cur, b))
    return [(x, y) for x, y in spans if y - x > 0.05]


def remap(t, spans):
    """Source time -> output time through the keep-windows; None if cut away."""
    off = 0.0
    for a, b in spans:
        if t < a:
            return None
        if t <= b:
            return off + (t - a)
        off += b - a
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brand"); ap.add_argument("take"); ap.add_argument("words")
    ap.add_argument("out")
    ap.add_argument("--hook", default=None)
    ap.add_argument("--cut", action="append", default=[])
    ap.add_argument("--corrections", default="")
    ap.add_argument("--broll", action="append", default=[],
                    help='"anchor phrase|/path/clip.mp4|in_point|dur" — cutaway starts '
                         "when he says the anchor; his audio and the captions continue")
    ap.add_argument("--broll-at", action="append", default=[],
                    help='"start_s|/path/clip.mp4|in_point|dur" — cutaway at an absolute output '
                         "time. Unlike --broll it may start at 0 and run to the end card: for "
                         "VO-over-b-roll brands (file 117) where the face never shows.")
    args = ap.parse_args()

    B = BRANDS[args.brand]
    corrections = dict(kv.split("=", 1) for kv in args.corrections.split(";") if "=" in kv)
    cuts = [tuple(float(x) for x in c.split("-")) for c in args.cut]
    words = load_words(args.words, corrections)
    spans = keep_windows(words, cuts)

    out = pathlib.Path(args.out)
    work = out.parent / f"_cut_{out.stem}"
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)

    # words in OUTPUT time (flub words vanish; the kept read survives)
    owords = []
    for w, a, b in words:
        oa, ob = remap(a, spans), remap(b, spans)
        if oa is not None and ob is not None:
            owords.append((w, oa, ob))
    speech_end = owords[-1][2]
    END_PAD = 2.4
    END_T = speech_end + 0.5

    # ---------------- b-roll plan: anchor phrase -> output-time window
    def norm(w): return w.lower().strip('.,?!"\':;')
    tokens = [norm(w) for w, _, _ in owords]

    def find_phrase(phrase):
        want = [norm(x) for x in phrase.split()]
        for i in range(len(tokens) - len(want) + 1):
            if tokens[i:i + len(want)] == want:
                return owords[i][1]
        return None

    brolls = []
    for spec in args.broll:
        phrase, clip, tin, dur = spec.split("|")
        at = find_phrase(phrase)
        if at is None:
            print(f"  broll SKIPPED (anchor not found): {phrase!r}")
            continue
        a = max(3.4, at)                       # never cover the hook
        b = min(a + float(dur), END_T - 1.2)   # never cover the end card approach
        if b - a < 1.2:
            print(f"  broll SKIPPED (window too small): {phrase!r}")
            continue
        brolls.append((a, b, clip, float(tin)))
        print(f"  broll {a:5.1f}-{b:5.1f}s  {pathlib.Path(clip).stem[:44]}")

    for spec in args.broll_at:
        start, clip, tin, dur = spec.split("|")
        a = max(0.0, float(start))
        b = min(a + float(dur), END_T - 0.05)  # may run under the end-card fade
        if b - a < 0.8:
            print(f"  broll-at SKIPPED (window too small): {start}s")
            continue
        brolls.append((a, b, clip, float(tin)))
        print(f"  broll-at {a:5.1f}-{b:5.1f}s  {pathlib.Path(clip).stem[:44]}")

    # ---------------- pass A: crop + trim + concat + freeze-extend, with audio
    parts = []
    for i, (a, b) in enumerate(spans):
        p = work / f"seg{i}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
             "-i", args.take,
             "-vf", f"crop={CROP_W}:{SRC_H}:{(SRC_W-CROP_W)//2}:0,scale={W}:{H},fps={FPS},format=yuv420p",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(p)])
        parts.append(p)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    base = work / "base.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-vf", f"tpad=stop_mode=clone:stop_duration={END_PAD}",
         "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur={END_PAD}",
         "-c:v", "libx264", "-crf", "18", "-preset", "fast",
         "-c:a", "aac", "-b:a", "192k", str(base)])
    total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=nw=1:nk=1", str(base)],
        capture_output=True, text=True).stdout)

    # ---------------- captions: 3-word chunks, active word lit (build_short renderer)
    WEIGHT = B.get("weight", "Black")
    font = ImageFont.truetype(str(B["font"]), 78); font.set_variation_by_name(WEIGHT)
    big  = ImageFont.truetype(str(B["font"]), 96); big.set_variation_by_name(WEIGHT)

    def fit(txt, f):
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        size = f.size
        while size > 34:
            t = ImageFont.truetype(str(B["font"]), size); t.set_variation_by_name(WEIGHT)
            if max(probe.textlength(w, font=t) for w in txt) <= SAFE_W:
                return t
            size -= 4
        return f

    def render(display, active):
        txt = [d.upper().strip(",") for d in display]
        f = fit(txt, big if len(txt) == 1 else font)
        img = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
        sh = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
        d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sh)
        rows, cur = [], []
        for i, t in enumerate(txt):
            trial = cur + [(i, t)]
            if d.textlength(" ".join(x[1] for x in trial), font=f) > SAFE_W and cur:
                rows.append(cur); cur = [(i, t)]
            else:
                cur = trial
        rows.append(cur)
        y = (BAND_H - len(rows) * (f.size + 16)) // 2
        for row in rows:
            x = (W - d.textlength(" ".join(x[1] for x in row), font=f)) / 2
            for i, t in row:
                col = B["accent"] if i == active else B["base"]
                ds.text((x + 3, y + 6), t, font=f, fill=(0, 0, 0, 170))
                d.text((x, y), t, font=f, fill=col + (255,))
                x += d.textlength(t + " ", font=f)
            y += f.size + 16
        return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9)), img)

    # chunk timeline: [(display_words, active_idx, start, end)]
    chunks = []
    for k in range(0, len(owords), 3):
        grp = owords[k:k + 3]
        for gi, (word, a, b) in enumerate(grp):
            end = grp[gi + 1][1] if gi + 1 < len(grp) else grp[-1][2] + 0.15
            chunks.append(([g[0] for g in grp], gi, a, end))

    SEQ = work / "seq"; SEQ.mkdir()
    seen = {}
    for ch in chunks:
        key = (tuple(ch[0]), ch[1])
        if key not in seen:
            p = SEQ / f"s{len(seen):03d}.png"
            render(ch[0], ch[1]).save(p)
            seen[key] = p
    blank = SEQ / "blank.png"
    Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0)).save(blank)

    nframes = int(total * FPS) + 1
    ci = 0
    for n in range(nframes):
        t = n / FPS
        while ci + 1 < len(chunks) and t >= chunks[ci + 1][2]:
            ci += 1
        ch = chunks[ci]
        src = seen[(tuple(ch[0]), ch[1])] if ch[2] <= t < ch[3] + 0.05 else blank
        dst = SEQ / f"f{n:05d}.png"
        try:
            dst.hardlink_to(src)
        except OSError:
            shutil.copy(src, dst)

    # ---------------- scrim, hook, corner, end card
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    for y in range(BAND_Y - 80, H):
        a = int(150 * min(1.0, (y - (BAND_Y - 80)) / 200))
        gd.line([(0, y), (W, y)], fill=(8, 10, 14, a))
    scrim = work / "scrim.png"; g.save(scrim)

    hook_png = None
    if args.hook:
        himg = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
        hd = ImageDraw.Draw(himg)
        hf = fit([w for w in args.hook.upper().split()], font)
        rows, cur = [], []
        for wtxt in args.hook.upper().split():
            trial = cur + [wtxt]
            if hd.textlength(" ".join(trial), font=hf) > SAFE_W and cur:
                rows.append(cur); cur = [wtxt]
            else:
                cur = trial
        rows.append(cur)
        y = 10
        for row in rows:
            line = " ".join(row)
            x = (W - hd.textlength(line, font=hf)) / 2
            hd.text((x + 3, y + 6), line, font=hf, fill=(0, 0, 0, 200))
            hd.text((x, y), line, font=hf, fill=(255, 255, 255, 255))
            y += hf.size + 12
        hook_png = work / "hook.png"; himg.save(hook_png)

    def scaled(width, src_path):
        im = Image.open(src_path).convert("RGBA")
        r = width / im.width
        return im.resize((width, int(im.height * r)), Image.LANCZOS)

    # corner mark on a soft dark chip: a flat logo vanishes over a bright shelf/poster
    corner = work / "corner.png"
    c = scaled(300, B.get("mark", B["logo"]))
    pad = 22
    chip = Image.new("RGBA", (c.width + pad * 2, c.height + pad * 2), (0, 0, 0, 0))
    cd = ImageDraw.Draw(chip)
    cd.rounded_rectangle([0, 0, chip.width - 1, chip.height - 1], radius=26,
                         fill=(8, 10, 14, 120))
    chip.paste(c, (pad, pad), c)
    chip.putalpha(chip.getchannel("A").point(lambda a: int(a * 0.88)))
    chip.save(corner)
    end_logo = work / "end_logo.png"; scaled(720, B["logo"]).save(end_logo)
    # 220 alpha: over a frozen talking-head frame, 165 left the face fighting the logo
    plate = work / "end_plate.png"
    Image.new("RGBA", (W, H), (10, 12, 15, 220)).save(plate)

    # ---------------- b-roll inserts: pre-render each cutaway at full frame
    br_files = []
    for j, (a, b, clip, tin) in enumerate(brolls):
        dur = b - a
        p = work / f"br{j}.mp4"
        z = "min(1.0+0.0018*on,1.10)" if j % 2 == 0 else "max(1.10-0.0018*on,1.0)"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{tin}", "-t", f"{dur + 0.2:.3f}",
             "-i", clip,
             "-filter:v", f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                          f"crop={W}:{H},"
                          f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
                          "eq=saturation=1.05:contrast=1.03,format=yuv420p",
             "-an", "-c:v", "libx264", "-crf", "18", "-preset", "fast", str(p)])
        br_files.append((a, b, p))

    # ---------------- pass B: base -> b-roll -> scrim -> corner -> end card -> captions -> hook
    corner_on = f"between(t,3.0,{END_T:.2f})" if args.hook else f"lt(t,{END_T:.2f})"
    ins = ["-i", str(base)]
    chain, cur, idx = [], "0:v", 1
    for a, b, p in br_files:
        ins += ["-i", str(p)]
        chain.append(f"[{idx}:v]setpts=PTS-STARTPTS+{a:.3f}/TB[br{idx}]")
        chain.append(f"[{cur}][br{idx}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{idx}]")
        cur, idx = f"v{idx}", idx + 1
    ins += ["-i", str(scrim)]
    chain.append(f"[{cur}][{idx}:v]overlay=0:0[sc]"); cur, idx = "sc", idx + 1
    ins += ["-i", str(corner)]
    chain.append(f"[{cur}][{idx}:v]overlay=54:104:enable='{corner_on}'[co]"); cur, idx = "co", idx + 1
    ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.2f}", "-i", str(plate)]
    chain.append(f"[{idx}:v]format=rgba,fade=in:st={END_T:.2f}:d=0.5:alpha=1[pl]")
    chain.append(f"[{cur}][pl]overlay=0:0:enable='gte(t,{END_T:.2f})'[pp]"); cur, idx = "pp", idx + 1
    ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.2f}", "-i", str(end_logo)]
    chain.append(f"[{idx}:v]format=rgba,fade=in:st={END_T:.2f}:d=0.5:alpha=1[el]")
    chain.append(f"[{cur}][el]overlay=(W-w)/2:700:enable='gte(t,{END_T:.2f})'[ee]"); cur, idx = "ee", idx + 1
    ins += ["-framerate", str(FPS), "-i", str(SEQ / "f%05d.png")]
    chain.append(f"[{cur}][{idx}:v]overlay=0:{BAND_Y}:enable='lt(t,{END_T:.2f})'[cp]"); cur, idx = "cp", idx + 1
    if hook_png:
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", "3.2", "-i", str(hook_png)]
        chain.append(f"[{cur}][{idx}:v]overlay=0:170:enable='lt(t,3.0)'[hk]"); cur, idx = "hk", idx + 1
    run(["ffmpeg", "-v", "error", "-y", *ins,
         "-filter_complex", ";".join(chain),
         "-map", f"[{cur}]", "-map", "0:a", "-shortest",
         "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", str(out)])
    shutil.rmtree(work, ignore_errors=True)
    print(f"CUT {out.name}  {total:.1f}s  ({out.stat().st_size // 1024 // 1024} MB)  "
          f"words={len(owords)} spans={len(spans)} end_card@{END_T:.1f}s")


if __name__ == "__main__":
    main()
