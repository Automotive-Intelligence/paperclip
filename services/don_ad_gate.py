"""Don Draper's EYES: a visual gate for ad creative, with a real exit code.

Iris gates craft on a web page. Don gates whether a piece of creative can carry
paid spend, which is a different question and needs different eyes: does it stop
a scroll SOUND-OFF, does it ask for the conversion its ad set is optimising for,
and would the actual buyer believe it.

Two deliberate differences from `scripts/iris_qa_gate.py`:

1. **IT CAN ACTUALLY BLOCK.** The Iris gate ends in an unconditional `return 0`,
   so it prints a verdict and then always reports success; the 2026-08-09 audit
   found it had never stopped anything. Here PASS exits 0, HOLD exits 1, and any
   error, missing file, unparseable verdict or API failure ALSO exits 1. Money is
   downstream of this gate, so it fails CLOSED.

2. **Mechanics are measured, not asked.** Duration, aspect ratio and frame rate
   come from ffprobe. Asking a language model what ffprobe can state exactly is
   how you get a confident wrong answer.

Don's criteria are READ FROM THE REPO (his charter + funnel plan), not hardcoded,
so when he revises his brief the gate revises with him.

    python -m services.don_ad_gate <video.mp4> --funnel aipg_click_to_call
    echo $?     # 0 = PASS, 1 = HOLD
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

TELEMETRY = pathlib.Path.home() / "avo-telemetry" / "marketing_deliverables"
CHARTER = TELEMETRY / "149_don_draper_launch_prompt.md"
AD_PLAN = TELEMETRY / "151_don_draper_ad_plan_v1.md"
ICP_FIT = TELEMETRY / "117_icp_visual_fit_standard.md"

# Placement expectations Meta actually enforces, so they are checked in code.
ASPECT_OK = {"9:16": (9 / 16), "1:1": 1.0, "4:5": 0.8}
MAX_SECONDS = 60.0
MIN_SECONDS = 5.0


def probe(path: pathlib.Path) -> Dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, den = (st.get("r_frame_rate", "24/1").split("/") + ["1"])[:2]
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip() != ""
    w, h = int(st.get("width", 0)), int(st.get("height", 0))
    return {"width": w, "height": h, "aspect": (w / h) if h else 0,
            "fps": float(num) / float(den or 1),
            "duration": float((d.get("format") or {}).get("duration", 0)),
            "has_audio": has_audio}


def mechanical_findings(m: Dict[str, Any]) -> List[str]:
    """Hard facts. These are failures no critique should have to notice."""
    bad = []
    if not any(abs(m["aspect"] - r) < 0.02 for r in ASPECT_OK.values()):
        bad.append(f"aspect {m['width']}x{m['height']} matches no Meta placement "
                   f"(need 9:16, 1:1 or 4:5)")
    if m["duration"] > MAX_SECONDS:
        bad.append(f"{m['duration']:.1f}s exceeds the {MAX_SECONDS:.0f}s ceiling")
    if m["duration"] < MIN_SECONDS:
        bad.append(f"{m['duration']:.1f}s is too short to carry a hook and a CTA")
    if not m["has_audio"]:
        bad.append("no audio track")
    return bad


def grab_frames(path: pathlib.Path, duration: float, n: int = 5) -> List[bytes]:
    """Always include the OPENING and the END CARD, then fill the middle.

    Trimming this list naively once cut the end card, and Don duly reported "no
    CTA anywhere" about a piece whose end card carries the phone number. He was
    right about what he was shown; he was shown the wrong thing. On a conversion
    objective the closing frame is the single most important one, so first-two
    and last are pinned and only the middle is negotiable.
    """
    head = [0.4, 1.2]
    tail = [max(0.0, duration - 0.5)]
    mids = [duration * f for f in (0.35, 0.55, 0.75)]
    keep = head + mids[: max(0, n - len(head) - len(tail))] + tail

    out: List[bytes] = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(keep):
            fp = pathlib.Path(td) / f"f{i}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.2f}",
                            "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=540:-1", str(fp)], check=True)
            if fp.exists():
                out.append(fp.read_bytes())
    return out


def _read(p: pathlib.Path, limit: int = 6000) -> str:
    try:
        return p.read_text()[:limit]
    except OSError:
        return ""


SCHEMA_HINT = """Return ONLY this JSON:
{"verdict":"PASS"|"HOLD",
 "starting_gate":{"stop":true|false,"believe":true|false,"tap":true|false,"why":"..."},
 "findings":[{"severity":"blocker"|"note","what":"...","fix":"..."}],
 "hook_soundoff":"what a muted viewer understands in the first 2 seconds",
 "cta":"the ask as it actually appears, or NONE",
 "one_line":"the verdict in one sentence"}
HOLD if ANY blocker exists. A missing or mismatched CTA is a blocker on a
conversion objective. Be specific and concrete; vague praise is useless."""


def review(video: pathlib.Path, funnel: str, objective: str,
           model: Optional[str] = None) -> Dict[str, Any]:
    from services.studio_social_llm import llm_json

    m = probe(video)
    mech = mechanical_findings(m)
    frames = grab_frames(video, m["duration"])
    if not frames:
        raise RuntimeError("could not extract frames")

    system = (
        "You are Don Draper, the paid-media seat. You decide whether a piece of "
        "creative may carry money. You did not make this and you owe it nothing.\n\n"
        "Your charter:\n" + _read(CHARTER, 2000) + "\n\n"
        "Your funnel plan:\n" + _read(AD_PLAN, 2500) + "\n\n"
        "The ICP visual-fit standard you inherit:\n" + _read(ICP_FIT, 1500) + "\n\n"
        "Judge ONLY what you can see in these frames. Do not assume anything "
        "the frames do not show. If the frames cannot answer a criterion, say so "
        "and treat it as a blocker rather than guessing."
    )
    user = (
        f"Creative under review: {video.name}\n"
        f"Funnel: {funnel}\nAd-set objective: {objective}\n"
        f"Measured: {m['width']}x{m['height']}, {m['duration']:.2f}s, "
        f"{m['fps']:.0f}fps, audio={'yes' if m['has_audio'] else 'NO'}\n"
        f"Mechanical failures already detected: {mech or 'none'}\n\n"
        "Frames in order: opening hook (x2), then through the body, then the end "
        "card.\n\n"
        "Apply the Starting Gate as the buyer, in their real moment: would THIS "
        "person stop, believe it, and actually tap. Then check: does the hook work "
        "with sound OFF; is there a CTA and does it match the objective; is this "
        "the buyer's real world; any AI artifacts (garbled text, fake plates, "
        "impossible hands, wrong tools); any claim, statistic or price that would "
        "need substantiation.\n\n" + SCHEMA_HINT
    )
    # 8000 not 2000: the model spends budget on reasoning first, and at
    # 2000 it hit stop_reason=max_tokens with no text at all, four retries
    # in a row. A vision critique needs room to think AND answer.
    res = llm_json(system, user, images=frames, model=model, max_tokens=8000)
    res["_mechanical"] = mech
    res["_measured"] = m
    if mech:
        res["verdict"] = "HOLD"
        res.setdefault("findings", []).extend(
            {"severity": "blocker", "what": f, "fix": "re-export"} for f in mech)
    return res


def render(res: Dict[str, Any]) -> str:
    L = [f"DON DRAPER — AD GATE: {res.get('verdict', 'HOLD')}",
         f"  {res.get('one_line', '')}", ""]
    sg = res.get("starting_gate") or {}
    if sg:
        L.append(f"  Starting Gate: stop={sg.get('stop')} believe={sg.get('believe')} "
                 f"tap={sg.get('tap')}")
        L.append(f"    {sg.get('why', '')}")
    L += [f"  Hook (sound off): {res.get('hook_soundoff', '?')}",
          f"  CTA: {res.get('cta', '?')}", ""]
    for f in res.get("findings") or []:
        mark = "BLOCKER" if f.get("severity") == "blocker" else "note   "
        L.append(f"  [{mark}] {f.get('what')}")
        if f.get("fix"):
            L.append(f"            fix: {f.get('fix')}")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Don Draper's visual gate for ad creative")
    ap.add_argument("video")
    ap.add_argument("--funnel", default="unspecified")
    ap.add_argument("--objective", default="QUALITY_CALL / PHONE_CALL")
    ap.add_argument("--model", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    path = pathlib.Path(a.video).expanduser()
    if not path.exists():
        print(f"don-gate: no such file: {path}", file=sys.stderr)
        return 1                      # fail closed
    try:
        res = review(path, a.funnel, a.objective, a.model)
    except Exception as e:            # noqa: BLE001 - any failure blocks
        print(f"don-gate: review failed, HOLDING: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else render(res))
    return 0 if str(res.get("verdict", "")).upper() == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
