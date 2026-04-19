"""Changelog JSON feed — powers the dashboard changelog section.

Route wired in app.py: GET /api/changelogs?week=&year=
Storage: Postgres `agent_logs` (agent_name='changelog'), filesystem fallback
for historical backfills committed to git. Railway filesystem is ephemeral,
so new weekly runs MUST persist to Postgres to survive container restarts.
"""

import glob
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import psycopg2 as psycopg
except ImportError:
    try:
        import psycopg
    except ImportError:
        psycopg = None

REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_TYPE_RE = re.compile(r"^week_(\d+)_(\d+)$")


def _db_conn():
    url = os.environ.get("DATABASE_URL")
    if not url or psycopg is None:
        return None
    try:
        return psycopg.connect(url, connect_timeout=5)
    except Exception as e:
        logging.warning(f"[changelog] DB connect failed: {e}")
        return None


def write_to_db(week: int, year: int, content: str) -> bool:
    """Persist a changelog to Postgres. Returns True on success."""
    conn = _db_conn()
    if conn is None:
        return False
    try:
        monday = datetime.fromisocalendar(year, week, 1).date()
        log_type = f"week_{week}_{year}"
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_logs (agent_name, log_type, run_date, content) VALUES (%s, %s, %s, %s)",
                    ("changelog", log_type, monday, content),
                )
        logging.info(f"[changelog] Persisted week {week}/{year} to Postgres ({len(content)} chars)")
        return True
    except Exception as e:
        logging.error(f"[changelog] DB write failed: {e}")
        return False
    finally:
        conn.close()


def _load_from_db() -> dict[tuple[int, int], str]:
    """Return {(week, year): latest_content} from Postgres."""
    conn = _db_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (log_type) log_type, content "
                "FROM agent_logs WHERE agent_name = 'changelog' "
                "ORDER BY log_type, created_at DESC"
            )
            rows = cur.fetchall()
        out = {}
        for log_type, content in rows:
            m = _LOG_TYPE_RE.match(log_type or "")
            if m:
                out[(int(m.group(1)), int(m.group(2)))] = content
        return out
    except Exception as e:
        logging.error(f"[changelog] DB read failed: {e}")
        return {}
    finally:
        conn.close()


def _load_from_fs() -> dict[tuple[int, int], str]:
    """Return {(week, year): content} read from repo-root markdown files."""
    out = {}
    for p in glob.glob(str(REPO_ROOT / "CHANGELOG_WEEK_*.md")):
        m = re.search(r"CHANGELOG_WEEK_(\d+)_(\d+)\.md$", p)
        if not m:
            continue
        try:
            with open(p, "r") as f:
                out[(int(m.group(1)), int(m.group(2)))] = f.read()
        except OSError:
            continue
    return out


def _load_all() -> dict[tuple[int, int], str]:
    """DB first (authoritative for new runs), filesystem for historical backfill."""
    merged = _load_from_fs()
    merged.update(_load_from_db())  # DB wins on conflict
    return merged


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
        "story": "",
        "rivers": [],
        "totals": {},
        "dev": {"commits": 0, "bugs_count": 0, "features_count": 0, "bugs": [], "features": []},
        "cost": [],
        "next_week": [],
    }

    story_match = re.search(r"## The Story This Week\n(.+?)(?=\n---|\n## )", md, re.DOTALL)
    if story_match:
        out["story"] = story_match.group(1).strip()

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
    store = _load_all()
    if not store:
        return {"weeks": [], "selected": None}

    keys = sorted(store.keys(), key=lambda t: (t[1], t[0]), reverse=True)
    weeks = [{"week": w, "year": y, "date_range": _week_range(w, y)} for (w, y) in keys]

    if week is not None and year is not None and (week, year) in store:
        key = (week, year)
    else:
        key = keys[0]

    parsed = _parse(store[key])
    parsed["date_range"] = _week_range(parsed["week"], parsed["year"]) if parsed["week"] and parsed["year"] else ""
    return {"weeks": weeks, "selected": parsed}


def purge_db() -> dict:
    """Delete all 'changelog' rows from agent_logs. Used to discard polluted
    regeneration output and rebuild from filesystem + fresh git/LLM calls."""
    conn = _db_conn()
    if conn is None:
        return {"deleted": 0, "note": "DB unavailable"}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_logs WHERE agent_name = 'changelog'")
                deleted = cur.rowcount
        return {"deleted": deleted}
    finally:
        conn.close()


def backfill_fs_to_db() -> dict:
    """Copy any filesystem changelogs missing from Postgres into Postgres. Idempotent."""
    fs = _load_from_fs()
    db = _load_from_db()
    inserted = 0
    skipped = 0
    for (week, year), content in fs.items():
        if (week, year) in db:
            skipped += 1
            continue
        if write_to_db(week, year, content):
            inserted += 1
    return {"inserted": inserted, "skipped": skipped, "fs_total": len(fs), "db_total": len(db)}
