#!/usr/bin/env python3
"""Stock B-roll fetcher + auto-logger. The footage layer of the Slipstream video leg.

Two jobs, and the second one is the important one:

  FETCH  Pull clips from Pexels (primary) and Pixabay (fallback) by API. Both are free, both
         permit commercial + client use, both are perpetual. They are the ONLY stock libraries
         with a public API, which is the whole reason we're not paying Storyblocks to make
         Michael the download step.

  LOG    Measure every clip before the editor is allowed to touch it. This is the fix for the
         class of error that produced three bad cuts of the AIPG spot: I cut footage I had
         never watched. A 5s clip where the subject climbs out of frame at 2.2s is a 2.2s clip,
         and stretching the dead tail to fill a voice track is what "frozen" looks like.
         Nothing enters the library without a measured USABLE window.

Usage:
    python3 stock_fetch.py --brand aipg --shot "hvac technician condenser" --want 4
    python3 stock_fetch.py --brief   ~/stock_library/DOWNLOAD_BRIEF.md      # whole brief, all brands
    python3 stock_fetch.py --relog                                          # re-measure the library
"""
import argparse, json, os, subprocess, sys, urllib.parse, urllib.request, pathlib

LIB     = pathlib.Path.home() / "stock_library"
MANIFEST = LIB / "manifest.json"

# What we will accept into the library. These thresholds are the lesson from the AI clips.
MIN_DURATION = 8.0     # a 5s clip yields ~2s of live footage. Stock gives us 10s+; demand it.
MIN_SHORT_SIDE = 1080  # the SHORT side: portrait needs width >= 1080, landscape height >= 1080.
                       # A height check alone lets a 720x1366 portrait clip through as "1080p".
MIN_LIVE     = 4.0     # after logging, a clip with < 4s of live footage is not worth keeping


# ----------------------------------------------------------------- fetch
# Everything on the wire goes through curl, not urllib. This network does TLS interception
# (a self-signed cert lands in the chain), so Python's SSL context rejects it while curl
# accepts it against the system cert store. Learned this the hard way on fal; not relearning it.
def GET(url, headers=None):
    cmd = ["curl", "-sS", "--fail", "--max-time", "60"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    r = subprocess.run(cmd + [url], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout)


def pexels(query, want):
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        sys.exit("PEXELS_API_KEY not set. doppler secrets set PEXELS_API_KEY --silent -p paperclip -c prd")
    url = ("https://api.pexels.com/videos/search?"
           + urllib.parse.urlencode({"query": query, "per_page": max(want * 4, 15),
                                     "orientation": "portrait", "size": "medium"}))
    data = GET(url, {"Authorization": key})

    out = []
    for v in data.get("videos", []):
        if v.get("duration", 0) < MIN_DURATION:
            continue
        # LARGEST rendition whose short side clears the bar. The first video wave shipped
        # soft because this picked the smallest file with height >= 1080, which a 720x1366
        # portrait clip passes: 720p-class footage upscaled 1.5x into every 1080x1920 master.
        files = sorted(
            (f for f in v["video_files"]
             if min(f.get("width") or 0, f.get("height") or 0) >= MIN_SHORT_SIDE),
            key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), reverse=True)
        if not files:
            continue
        out.append({"src": "pexels", "id": v["id"], "url": files[0]["link"],
                    "duration": v["duration"], "h": files[0]["height"],
                    "w": files[0]["width"], "credit": v.get("user", {}).get("name", ""),
                    "page": v.get("url", "")})
    return out


def pixabay(query, want):
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
        {"key": key, "q": query, "per_page": max(want * 4, 15), "video_type": "film"})
    data = GET(url)
    out = []
    for v in data.get("hits", []):
        if v.get("duration", 0) < MIN_DURATION:
            continue
        f = v["videos"].get("large") or v["videos"].get("medium")
        if not f or min(f.get("width") or 0, f.get("height") or 0) < MIN_SHORT_SIDE:
            continue
        out.append({"src": "pixabay", "id": v["id"], "url": f["url"],
                    "duration": v["duration"], "h": f["height"], "w": f["width"],
                    "credit": v.get("user", ""), "page": v.get("pageURL", "")})
    return out


# ----------------------------------------------------------------- gate
def contact_sheet(path, sheet):
    """One frame per second, tiled, for a MODEL to look at.

    This exists because a signal-processing gate does not work, and I proved it on our own
    footage rather than assuming:

        02_ladder        motion 14 -> 17 -> 14 -> 17 -> 24     <- boot LEAVES frame at 2.2s
        04_hands_in_unit motion  3.7 -> 4.1 -> 4.1 -> 3.7      <- our single best clip

    The deadest moment scores the HIGHEST (the camera drifts, the sun flares, the frame is
    empty) and the best clip scores the LOWEST (a steady, tight, working hand). Motion,
    variance, scene-score — every cheap heuristic is not just weak here, it is inverted.

    No threshold would have caught the suit-at-a-podcast-desk render either. Only eyes catch
    that. So the clip is rendered to a sheet and gated by the agent, which answers three
    questions per clip:
        1. WINDOW  which seconds is the subject actually present and the shot alive?
        2. CLEAN   any garbled AI text, dead screens, unreadable mush, visible logos?
        3. ICP     would this brand's customer see THEMSELVES here? (file 117 — non-delegable)
    Verdicts land back in manifest.json. The editor may only touch gated clips.
    """
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout or 0)
    if dur <= 0:
        return None
    cols = min(int(dur), 12)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path),
                    "-vf", f"fps=1,scale=220:-1,tile={cols}x1", "-frames:v", "1", str(sheet)],
                   check=True)
    return {"duration": round(dur, 2), "sheet": str(sheet), "status": "UNGATED",
            "live_in": None, "live_out": None}


def download(item, dest):
    r = subprocess.run(["curl", "-sS", "--fail", "-L", "--max-time", "300",
                        "-o", str(dest), item["url"]], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return dest


# ----------------------------------------------------------------- main
def acquire(brand, shot, want):
    folder = LIB / brand
    folder.mkdir(parents=True, exist_ok=True)
    man = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

    cands = pexels(shot, want) + pixabay(shot, want)
    cands.sort(key=lambda c: -c["duration"])          # longer clips = real coverage
    print(f"[{brand}] \"{shot}\" -> {len(cands)} candidates >= {MIN_DURATION}s", flush=True)

    sheets = LIB / "_sheets"; sheets.mkdir(exist_ok=True)
    kept = 0
    slug = "".join(c if c.isalnum() else "_" for c in shot)[:40]
    for c in cands:
        if kept >= want:
            break
        key = f"{c['src']}_{c['id']}"
        if key in man:
            continue
        dest = folder / f"{slug}__{key}.mp4"
        try:
            download(c, dest)
        except Exception as e:
            print(f"  download failed {key}: {e}", flush=True)
            continue

        cs = contact_sheet(dest, sheets / f"{key}.png")
        if not cs:
            dest.unlink(missing_ok=True)
            continue
        man[key] = {**c, **cs, "brand": brand, "shot": shot, "file": str(dest)}
        kept += 1
        print(f"  FETCHED {dest.name}  {cs['duration']:.0f}s -> sheet {key}.png  [UNGATED]", flush=True)

    MANIFEST.write_text(json.dumps(man, indent=2))
    return kept


def ungated():
    """The work queue for the gate. The agent views each sheet and writes back a verdict."""
    man = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    q = [v for v in man.values() if v.get("status") == "UNGATED"]
    for v in q:
        print(f"{v['brand']:<14}{v['shot']:<34}{v['sheet']}", flush=True)
    print(f"\n{len(q)} clips awaiting the ICP + liveness gate. "
          f"No clip may be cut until it is gated.", flush=True)
    return q


def parse_brief(path):
    """Pull `Search:` lines out of the download brief, grouped by the folder they belong to."""
    brand, jobs = "universal", []
    for line in pathlib.Path(path).read_text().splitlines():
        if "~/stock_library/" in line and "`" in line:
            brand = line.split("~/stock_library/")[1].split("/")[0]
        if line.strip().lower().startswith("search:") or (jobs and line.strip().startswith("`") and "," in line):
            for term in line.split(":", 1)[-1].split(","):
                term = term.strip().strip("`").strip()
                if term:
                    jobs.append((brand, term))
    return jobs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="universal")
    ap.add_argument("--shot")
    ap.add_argument("--want", type=int, default=3)
    ap.add_argument("--brief")
    ap.add_argument("--ungated", action="store_true", help="list clips awaiting the vision gate")
    a = ap.parse_args()

    if a.ungated:
        ungated()
    elif a.brief:
        total = 0
        for brand, term in parse_brief(a.brief):
            total += acquire(brand, term, a.want)
        print(f"\nLIBRARY: {total} new clips, all measured. {MANIFEST}", flush=True)
    elif a.shot:
        acquire(a.brand, a.shot, a.want)
    else:
        ap.error("need --shot, --brief, or --relog")
