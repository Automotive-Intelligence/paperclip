"""Changelog JSON feed — powers the dashboard changelog section.

Route wired in app.py: GET /api/changelogs?week=&year=
Returns list of available weeks plus parsed content for the selected week.
"""

import glob
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def _list_files() -> list[tuple[int, int, str]]:
    """Return [(week, year, path), ...] sorted newest-first."""
    pattern = str(REPO_ROOT / "CHANGELOG_WEEK_*.md")
    out = []
    for p in glob.glob(pattern):
        m = re.search(r"CHANGELOG_WEEK_(\d+)_(\d+)\.md$", p)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), p))
    out.sort(key=lambda t: (t[1], t[0]), reverse=True)
    return out


def _week_range(week: int, year: int) -> str:
    try:
        monday = datetime.fromisocalendar(year, week, 1)
        sunday = monday + timedelta(days=6)
        if monday.month == sunday.month:
            return f"{monday.strftime('%b')} {monday.day}–{sunday.day}"
        return f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d')}"
    except ValueError:
        return ""


def _extract_int(s: str) -> int:
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _strip_sha(commit: str) -> tuple[str, str]:
    parts = commit.split(" ", 1)
    if len(parts) == 2 and re.match(r"^[0-9a-f]{6,10}$", parts[0]):
        return parts[0], parts[1]
    return "", commit


def _parse(md: str) -> dict:
    out: dict = {
        "generated": "",
        "week": None,
        "year": None,
        "rivers": [],
        "totals": {},
        "dev": {"commits": 0, "bugs_count": 0, "features_count": 0, "bugs": [], "features": []},
        "cost": [],
        "next_week": [],
    }

    m = re.search(r"Week (\d+), (\d+)", md)
    if m:
        out["week"] = int(m.group(1))
        out["year"] = int(m.group(2))

    m = re.search(r"Generated: (.+)", md)
    if m:
        out["generated"] = m.group(1).strip()

    skip_river_titles = {
        "bugs fixed", "features added",
        "cost by river (last 7 days)", "top agents by cost",
        "most expensive run", "monthly projection",
    }
    for title, body in re.findall(r"### ([^\n]+?)\n((?:- [^\n]+\n?)+)", md):
        if title.lower() in skip_river_titles:
            continue
        items = [ln[2:].strip() for ln in body.strip().split("\n") if ln.startswith("- ")]
        out["rivers"].append({"name": title.strip(), "items": items})

    rev = re.search(r"## Revenue Impact\n((?:- [^\n]+\n?)+)", md)
    if rev:
        for ln in rev.group(1).strip().split("\n"):
            if ln.startswith("- "):
                kv = ln[2:].split(":", 1)
                if len(kv) == 2:
                    out["totals"][kv[0].strip()] = kv[1].strip()

    dev = re.search(r"## Development Activity\n((?:- [^\n]+\n?)+)", md)
    if dev:
        for ln in dev.group(1).strip().split("\n"):
            if "Commits this week" in ln:
                out["dev"]["commits"] = _extract_int(ln)
            elif "Bugs fixed" in ln:
                out["dev"]["bugs_count"] = _extract_int(ln)
            elif "Features added" in ln:
                out["dev"]["features_count"] = _extract_int(ln)

    bugs = re.search(r"### Bugs Fixed\n((?:- [^\n]+\n?)+)", md)
    if bugs:
        out["dev"]["bugs"] = [
            {"sha": _strip_sha(ln[2:].strip())[0], "msg": _strip_sha(ln[2:].strip())[1]}
            for ln in bugs.group(1).strip().split("\n") if ln.startswith("- ")
        ]

    feats = re.search(r"### Features Added\n((?:- [^\n]+\n?)+)", md)
    if feats:
        out["dev"]["features"] = [
            {"sha": _strip_sha(ln[2:].strip())[0], "msg": _strip_sha(ln[2:].strip())[1]}
            for ln in feats.group(1).strip().split("\n") if ln.startswith("- ")
        ]

    cost = re.search(r"## AVO Cost Report\n(.+?)(?=\n---|\Z)", md, re.DOTALL)
    if cost:
        for ln in cost.group(1).strip().split("\n"):
            ln = ln.strip()
            if ln.startswith("- "):
                out["cost"].append({"kind": "item", "text": ln[2:]})
            elif ln.startswith("### "):
                out["cost"].append({"kind": "heading", "text": ln[4:]})

    nw = re.search(r"## Next Week Priorities\n((?:- [^\n]+\n?)+)", md)
    if nw:
        out["next_week"] = [ln[2:].strip() for ln in nw.group(1).strip().split("\n") if ln.startswith("- ")]

    return out


def feed(week: Optional[int] = None, year: Optional[int] = None) -> dict:
    files = _list_files()
    weeks = [
        {"week": w, "year": y, "date_range": _week_range(w, y)}
        for (w, y, _) in files
    ]

    if not files:
        return {"weeks": [], "selected": None}

    if week is None or year is None:
        selected_file = files[0]
    else:
        match = [f for f in files if f[0] == week and f[1] == year]
        selected_file = match[0] if match else files[0]

    with open(selected_file[2], "r") as f:
        parsed = _parse(f.read())

    parsed["date_range"] = _week_range(parsed["week"], parsed["year"]) if parsed["week"] and parsed["year"] else ""
    return {"weeks": weeks, "selected": parsed}
