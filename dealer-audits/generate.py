#!/usr/bin/env python3
"""Render dealer-audit report pages from JSON specs.

Usage:
    python3 generate.py specs/<slug>.json     # one audit
    python3 generate.py                       # every spec in specs/

Reads _template/report.html.tpl, fills {{TOKENS}} from the spec, writes
audits/<slug>.html. Spec strings are trusted author HTML (Chase writes
them) -- nothing is escaped, so never render a spec from an untrusted
source. See README.md for the spec schema and workflow.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "_template" / "report.html.tpl"
SPECS = ROOT / "specs"
OUT = ROOT / "audits"

TONE_TO_GRADE_CLASS = {"crit": "g-crit", "warn": "g-warn", "good": "g-good", "neutral": "g-neutral"}


def _meta_rows(pairs):
    return "".join(
        f'\n      <span>{k}&nbsp; <b>{v}</b></span>' for k, v in pairs
    ) + "\n    "


def _scorecard(cards):
    out = []
    for c in cards:
        cls = TONE_TO_GRADE_CLASS[c.get("tone", "neutral")]
        out.append(
            '      <article class="card"><div class="top">'
            f'<h3>{c["name"]}</h3><span class="grade {cls}">{c["grade"]}</span>'
            f'</div><p>{c["text"]}</p></article>'
        )
    return "\n".join(out)


def _table_head(cells):
    return "".join(f"<th>{c}</th>" for c in cells)


def _table_rows(rows):
    out = []
    for r in rows:
        cls = ' class="you"' if r.get("you") else ""
        cells = "".join(f"<td>{c}</td>" for c in r["cells"])
        out.append(f"          <tr{cls}>{cells}</tr>")
    return "\n".join(out)


def _findings(findings):
    out = []
    for f in findings:
        rows = "".join(
            f'\n        <div class="row"><div class="k">{k}</div><div class="v">{v}</div></div>'
            for k, v in f["rows"]
        )
        out.append(
            f'    <article class="find sev-{f["severity"]}">\n'
            f'      <div class="fh"><h3>{f["title"]}</h3>'
            f'<span class="chip {f["chip_class"]}">{f["chip"]}</span></div>\n'
            f'      <div class="body">{rows}\n      </div>\n'
            f'    </article>'
        )
    return "\n\n".join(out)


def _phases(phases):
    out = []
    for p in phases:
        items = "".join(f"\n        <li>{i}</li>" for i in p["items"])
        out.append(
            f'    <div class="phase">\n'
            f'      <div><div class="num display">{p["num"]}</div><div class="lbl">{p["label"]}</div></div>\n'
            f'      <ul>{items}\n      </ul>\n'
            f'    </div>'
        )
    return "\n".join(out)


def _steps(steps):
    return "\n".join(
        f'        <div class="step"><div class="p">{s["p"]}</div>'
        f'<div class="t">{s["t"]}</div><div class="d">{s["d"]}</div></div>'
        for s in steps
    )


def _sources(items):
    return "\n".join(f"      <li>{s}</li>" for s in items)


def render(spec_path: Path) -> Path:
    spec = json.loads(spec_path.read_text())
    html = TEMPLATE.read_text()

    robots = '<meta name="robots" content="noindex">' if spec.get("noindex", True) else ""
    tokens = {
        "TITLE": spec["title"],
        "META_DESCRIPTION": spec["meta_description"],
        "ROBOTS_META": robots,
        "HERO_TITLE_HTML": spec["hero"]["title_html"],
        "HERO_SUB": spec["hero"]["sub"],
        "META_ROWS": _meta_rows(spec["hero"]["meta"]),
        "GRADE": spec["grade"],
        "GAUGE_PCT": str(spec.get("gauge_pct", 50)),
        "VERDICT_TITLE": spec["verdict"]["title"],
        "VERDICT_TEXT": spec["verdict"]["text"],
        "THESIS_KICKER": spec["thesis"]["kicker"],
        "THESIS_HEADLINE": spec["thesis"]["headline"],
        "THESIS_LABEL": spec["thesis"]["label"],
        "THESIS_HTML": spec["thesis"]["html"],
        "NOTE_HTML": spec["note_html"],
        "SCORECARD_HEADLINE": spec["scorecard"]["headline"],
        "SCORECARD_CARDS": _scorecard(spec["scorecard"]["cards"]),
        "CONTEXT_HEADLINE": spec["context"]["headline"],
        "CONTEXT_LEAD_HTML": spec["context"]["lead_html"],
        "CONTEXT_TABLE_HEAD": _table_head(spec["context"]["table_head"]),
        "CONTEXT_TABLE_ROWS": _table_rows(spec["context"]["table_rows"]),
        "CONTEXT_AFTER_HTML": spec["context"]["after_html"],
        "FINDINGS_HTML": _findings(spec["findings"]),
        "PHASES_HTML": _phases(spec["phases"]),
        "CTA_HEADLINE_HTML": spec["cta"]["headline_html"],
        "CTA_TEXT_HTML": spec["cta"]["text_html"],
        "CTA_STEPS": _steps(spec["cta"]["steps"]),
        "MAILTO_SUBJECT": spec["cta"]["mailto_subject"],
        "FORM_SOURCE": spec["cta"]["form_source"],
        "SOURCES_HTML": _sources(spec["sources"]),
    }
    for key, value in tokens.items():
        html = html.replace("{{" + key + "}}", value)

    leftover = [t for t in html.split("{{")[1:] if "}}" in t]
    if leftover:
        names = sorted({t.split("}}")[0] for t in leftover})
        raise SystemExit(f"{spec_path.name}: unfilled tokens: {', '.join(names)}")

    out = OUT / f"{spec['slug']}.html"
    out.write_text(html)
    return out


def main():
    targets = [Path(a) for a in sys.argv[1:]] or sorted(SPECS.glob("*.json"))
    if not targets:
        raise SystemExit("no specs found in specs/")
    for t in targets:
        out = render(t)
        print(f"rendered {t.name} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
