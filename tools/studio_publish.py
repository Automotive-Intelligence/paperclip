"""
tools/studio_publish.py — THE STUDIO publish pipeline.

Takes a GATED batch deliverable (the file 101 format: per-brand sections, each with
4 platform sub-sections and an `images/<key>.png` reference) and schedules it to
multiple channels via Zernio, per brand, per platform.

Design guarantees:
  * DRY-RUN by default. Nothing is posted unless --commit is passed.
  * GATE-ENFORCED. --commit refuses to run unless the batch file carries BOTH
    gate stamps ("Iris visual gate" + "Scrutineer copy gate"). Maker-checker in code.
  * NO SILENT DROPS. Brands/platforms with no connected Zernio account are reported,
    not skipped quietly.
  * TRUTHFUL. Reads back the real post _id/status after scheduling (via the working
    /posts route) and writes a ledger.

Usage:
  railway run --service paperclip --environment production -- \
    python tools/studio_publish.py <batch.md> --when 2026-06-30T13:30 [--tz America/Chicago] \
      [--brands "Automotive Intelligence,Worship Digital"] [--commit]

  (omit --commit for a dry-run plan; default tz America/Chicago)
"""

from __future__ import annotations
import argparse, json, os, re, sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.zernio import (  # noqa: E402
    zernio_ready, get_zernio_profiles, list_zernio_accounts,
    upload_media_to_zernio, use_api_key,
)
from tools.social_load import PostJob, load_jobs, canonical_brand  # noqa: E402

# Brands that publish through a PARTNER's Zernio account via a SEPARATE API key.
# Book'd is intentionally NOT here: we accepted Ryan's team invite, so our default
# key already reaches his "book'd" profile. Kept for any future separate-key brand.
BRAND_KEY_ENV = {}

# Deliverable brand heading -> Zernio profile name. Extend as brands connect.
BRAND_TO_PROFILE = {
    "automotive intelligence": "Automotive Intelligence",
    # WD reuses the legacy Calling Digital profile (its pre-rebrand identity;
    # @callingdigital handles already connected). Owner decision 2026-06-30.
    "worship digital": "Calling Digital",
    "the ai phone guy": "AI Phone Guy",
    "ai phone guy": "AI Phone Guy",
    "agent empire": "Agent Empire",
    "book'd": "Book'd",
    "bookd": "Book'd",
}
# Deliverable sub-headers -> Zernio platform ids. Image-only batch (no TikTok/YouTube).
SECTION_TO_PLATFORM = {
    "linkedin": "linkedin",
    "instagram (caption)": "instagram",
    "instagram": "instagram",
    "facebook": "facebook",
    "x": "twitter",
}
GATE_MARKERS = ["Iris visual gate", "Scrutineer copy gate", "Conversion Strategist gate"]

# Per-brand native-peak stagger grid (America/Chicago HH:MM) from file 103. With
# --stagger, --when is a DATE and each platform fires at its native peak that day.
STAGGER = {
    "automotive intelligence": {"twitter": "07:15", "linkedin": "07:45", "instagram": "12:00", "facebook": "13:00"},
    "worship digital":         {"twitter": "07:30", "linkedin": "07:45", "facebook": "09:00", "instagram": "12:00"},
    "the ai phone guy":        {"twitter": "07:15", "linkedin": "07:45", "facebook": "06:30", "instagram": "12:00"},
    "agent empire":            {"twitter": "06:45", "linkedin": "07:45", "facebook": "09:00", "instagram": "12:00"},
    "book'd":                  {"linkedin": "08:00", "facebook": "12:00"},
}


def parse_batch(md_path: str) -> Dict[str, Any]:
    text = open(md_path, encoding="utf-8").read()
    gates_present = all(m in text for m in GATE_MARKERS)
    # Split into brand blocks on "# N. NAME ... `images/key.png`"
    brand_re = re.compile(r"\n#\s+\d+\.\s+(.+?)\n", )
    heads = list(re.finditer(r"\n#\s+\d+\.\s+([^\n]+)\n", text))
    brands = []
    for i, h in enumerate(heads):
        head_line = h.group(1)
        start = h.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[start:end]
        name = re.split(r"\s{2,}|\(face", head_line)[0].strip()
        img_m = re.search(r"`(images/[^`]+\.png)`", head_line) or re.search(r"`(images/[^`]+\.png)`", block)
        image = img_m.group(1) if img_m else None
        posts = {}
        for sec_m in re.finditer(r"\n##\s+([^\n]+)\n(.*?)(?=\n##\s+|\Z)", block, re.S):
            sec = sec_m.group(1).strip().lower()
            platform = SECTION_TO_PLATFORM.get(sec)
            if not platform:
                continue
            body = sec_m.group(1) and sec_m.group(2).strip()
            body = body.split("\n> NOTE", 1)[0].split("\n---", 1)[0].strip()
            if "**FIRST COMMENT:**" in body:
                main, _, fc = body.partition("**FIRST COMMENT:**")
                body = main.strip() + "\n\n" + fc.strip()
            if body:
                posts[platform] = body
        if posts:
            brands.append({"name": name, "image": image, "posts": posts})
    return {"gates_present": gates_present, "brands": brands}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--when", required=True, help="ISO local datetime e.g. 2026-06-30T13:30")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--brands", default="", help="comma list to limit; default all in file")
    ap.add_argument("--platforms", default="", help="comma list to limit platforms per CMO channel policy (e.g. linkedin,facebook)")
    ap.add_argument("--stagger", action="store_true", help="treat --when as a DATE; fire each platform at its file-103 native peak")
    ap.add_argument("--commit", action="store_true", help="actually schedule (else dry-run)")
    ap.add_argument("--allow-stack", action="store_true",
                    help="override the file-121 queue guard (deliberate same-day stack)")
    args = ap.parse_args()

    if not zernio_ready():
        print("ERROR: ZERNIO_API_KEY not set (run via `railway run`)."); return 2

    md_dir = os.path.dirname(os.path.abspath(args.batch))
    parsed = parse_batch(args.batch)
    only = {b.strip().lower() for b in args.brands.split(",") if b.strip()}
    plat_only = {p.strip().lower() for p in args.platforms.split(",") if p.strip()}

    stagger_date = args.when.split("T")[0]  # used when --stagger (--when is a DATE)
    when = args.when if "T" in args.when else args.when + "T09:00"
    scheduled_for = when + (":00" if when.count(":") == 1 else "")

    if args.commit and not parsed["gates_present"]:
        print("REFUSING TO COMMIT: batch is missing a gate stamp "
              f"({' and '.join(GATE_MARKERS)}). Gate it first."); return 3

    def pid_of(a):
        p = a.get("profileId") or a.get("profile_id")
        return str(p.get("_id") if isinstance(p, dict) else (p or ""))

    # Default (our) account maps, loaded once.
    default_profiles = {p.get("name"): p.get("_id") for p in get_zernio_profiles()}
    default_accts = list_zernio_accounts()

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"=== STUDIO PUBLISH [{mode}] @ {scheduled_for} {args.tz} ===")
    print(f"gates: {'PASS' if parsed['gates_present'] else 'MISSING'}  brands in file: {len(parsed['brands'])}\n")

    ledger = []
    for b in parsed["brands"]:
        name = b["name"]
        if only and name.lower() not in only:
            continue

        # Route to the right Zernio account: a partner key (e.g. Book'd via Ryan's
        # ZERNIO_API_KEY_BOOKD) or our default. Partner brands skip cleanly if unset.
        key_env = BRAND_KEY_ENV.get(name.lower())
        brand_key = os.getenv(key_env, "").strip() if key_env else ""
        if key_env and not brand_key:
            print(f"# {name}  ->  partner Zernio ({key_env}) NOT SET  [SKIP, awaiting partner key]\n")
            continue

        try:
            if brand_key:
                use_api_key(brand_key)
                profiles = {p.get("name"): p.get("_id") for p in get_zernio_profiles()}
                accts = list_zernio_accounts()
                src = f"partner key {key_env}"
            else:
                use_api_key(None)
                profiles, accts = default_profiles, default_accts
                src = "default account"

            prof_name = BRAND_TO_PROFILE.get(name.lower())
            prof_id = None
            if prof_name:  # case-insensitive (Ryan's profile is "book'd", brand is "Book'd")
                prof_id = next((pid_ for pname, pid_ in profiles.items()
                                if pname.lower() == prof_name.lower()), None)
            print(f"# {name}  ->  profile: {prof_name or '?'} "
                  f"{'('+prof_id+')' if prof_id else '[NOT CONNECTED]'}  [{src}]")
            if not prof_id:
                for plat in b["posts"]:
                    print(f"    {plat:10} SKIP (brand not connected in Zernio)")
                print()
                continue
            acct_by_plat = {a["platform"]: a["_id"] for a in accts if pid_of(a) == str(prof_id)}

            media_url = None
            if b["image"] and args.commit:
                img_path = os.path.join(md_dir, b["image"])
                media_url = upload_media_to_zernio(open(img_path, "rb").read(),
                                                   os.path.basename(b["image"]), "image/png")

            # All scheduling flows through the file-121 loader: UTM tagging, the
            # queue-collision guard, the WD hard block, and the registry row live
            # THERE, not here. This wrapper only parses, gates, and maps accounts.
            jobs, skips = [], []
            for platform, content in b["posts"].items():
                if plat_only and platform not in plat_only:
                    skips.append((platform, "not in --platforms channel policy")); continue
                # Per-platform native-peak time when staggering; else the single --when.
                if args.stagger:
                    hhmm = STAGGER.get(name.lower(), {}).get(platform)
                    if not hhmm:
                        skips.append((platform, "no stagger slot in file-103 grid")); continue
                    when_post = f"{stagger_date}T{hhmm}:00"
                else:
                    when_post = scheduled_for
                aid = acct_by_plat.get(platform)
                if not aid:
                    skips.append((platform, f"no {platform} account on this profile")); continue
                jobs.append(PostJob(
                    brand=canonical_brand(name), platform=platform, content=content,
                    scheduled_for=when_post, tz=args.tz,
                    media_urls=[media_url] if media_url else [],
                    content_id=os.path.splitext(os.path.basename(args.batch))[0],
                    entry_point="studio", account_id=aid))
            for platform, why in skips:
                print(f"    {platform:10} SKIP ({why})")
            for r in load_jobs(jobs, commit=args.commit, allow_stack=args.allow_stack):
                j, act = r["job"], r["action"]
                if act == "dry-run":
                    print(f"    {j.platform:10} OK  {len(j.content):>5} chars  -> {j.scheduled_for}")
                elif act == "scheduled":
                    res = r["detail"]
                    print(f"    {j.platform:10} SCHEDULED id={res.get('_id')} status={res.get('status')} @ {j.scheduled_for}")
                    ledger.append({"brand": name, "platform": j.platform,
                                   "post_id": res.get("_id"), "status": res.get("status"),
                                   # the REAL per-post time (--stagger fires each platform
                                   # at its own native peak), not the top-level --when
                                   "scheduled_for": j.scheduled_for})
                else:
                    print(f"    {j.platform:10} {act.upper()} {r.get('detail')}")
            print()
        finally:
            use_api_key(None)

    if args.commit and ledger:
        out = os.path.join(md_dir, "studio_publish_ledger.json")
        # APPEND, never clobber. The Studio weekly engine invokes this once per batch
        # file (and per brand), and every call writes the SAME ledger path in the SAME
        # folder. Opening in "w" mode meant each call erased the last: the 2026-07-11
        # run really did schedule 18 posts across 3 brands, but the ledger it left
        # behind showed 4 posts and zero AvI. The posts were real; the audit trail was
        # a lie. That is the same class of blind spot that let "launch day" be reported
        # as live for two days while nothing sent. Merge, deduped by post_id.
        batch_name = os.path.basename(args.batch)
        stamped = [{**row, "tz": args.tz, "batch": batch_name} for row in ledger]
        existing: list = []
        if os.path.exists(out):
            try:
                prior = json.load(open(out, encoding="utf-8"))
                existing = prior.get("posts", []) if isinstance(prior, dict) else list(prior)
            except (ValueError, OSError) as e:
                # A corrupt/legacy ledger must not cost us THIS run's record.
                print(f"[ledger] WARNING: could not read existing ledger ({e}); starting fresh")
                existing = []
        seen = {p.get("post_id") for p in existing if p.get("post_id")}
        merged = existing + [r for r in stamped if r.get("post_id") not in seen]
        json.dump({"posts": merged}, open(out, "w"), indent=2)
        print(f"[ledger] {out} (+{len(stamped)} this call, {len(merged)} total)")
    elif not args.commit:
        print("DRY-RUN complete. Re-run with --commit to schedule (requires gate stamps).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
