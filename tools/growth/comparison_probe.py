#!/usr/bin/env python3
"""Phase-2 comparison-targeting probe: who owns the 'best X' / 'X alternatives' queries
for AVI, Bookd, Agent Empire — the map for comparison pages (the #1 AI-cited content type)."""
import os, sys, re, time
import requests

KEY = os.environ.get("OPENROUTER_API_KEY", "")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "perplexity/sonar"
DOM_RE = re.compile(r"https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")

CASES = {
    "Automotive Intelligence (automotiveintelligence.io)": ("automotiveintelligence.io", [
        "best AI platform for car dealerships",
        "Fullpath alternatives for dealerships",
        "top AI tools for auto dealerships 2026",
        "AI vendor for dealership BDC and lead follow-up",
    ]),
    "Bookd (bookd.cx)": ("bookd.cx", [
        "best CRM for insurance agents",
        "AgencyBloc alternatives",
        "insurance agency CRM with compliance and audit trail",
        "CRM for life insurance agents with consent tracking",
    ]),
    "Agent Empire (buildagentempire.com)": ("buildagentempire.com", [
        "best course to learn to build AI agents",
        "AI agent building bootcamp",
        "how to start an AI automation agency",
        "community for people building AI agents",
    ]),
}

def ask(q):
    r = requests.post(URL, headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                      json={"model": MODEL, "messages": [{"role": "user", "content": q}]}, timeout=60)
    r.raise_for_status(); return r.json()

def domains(j):
    d = set()
    def walk(o):
        if isinstance(o, str):
            for m in DOM_RE.finditer(o): d.add(m.group(1).lower().replace("www.", ""))
        elif isinstance(o, dict):
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(j); return d

def main():
    if not KEY: print("no key"); sys.exit(1)
    cost = 0.0
    for label, (dom, queries) in CASES.items():
        print(f"\n### {label}")
        for q in queries:
            try:
                j = ask(q); cost += float((j.get("usage") or {}).get("cost", 0) or 0)
                d = domains(j)
                us = dom.replace("www.", "") in d
                owners = sorted(x for x in d if "google" not in x and "youtube" not in x)[:6]
                print(f"  • {q}")
                print(f"      {'★ WE ARE CITED' if us else 'not cited — owners to beat:'} {owners}")
                time.sleep(1)
            except Exception as e:
                print(f"  • {q} -> ERR {str(e)[:70]}")
    print(f"\n---\nEXACT SPEND: ${cost:.4f}")

if __name__ == "__main__":
    main()
