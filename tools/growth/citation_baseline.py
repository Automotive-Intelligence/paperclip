#!/usr/bin/env python3
"""Phase-0 AEO citation baseline. Perplexity Sonar via OpenRouter (key from doppler env).
Mirrors what searchstack's perplexity provider does: ask the query, check if the brand
domain shows up in the answer/citations, and record who is cited instead."""
import os, sys, json, re, time
import requests

KEY = os.environ.get("OPENROUTER_API_KEY", "")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "perplexity/sonar"

BRANDS = {
    "Worship Digital": ("worshipdigital.co", [
        "what is local seo",
        "local seo marketing services",
        "digital marketing agency for small business in Dallas",
        "AI implementation consultant for small business",
    ]),
    "The AI Phone Guy": ("theaiphoneguy.com", [
        "AI receptionist for HVAC business",
        "answering service for plumbers",
        "best AI answering service for home service contractors",
        "never miss a call AI receptionist for trades",
    ]),
    "Automotive Intelligence": ("automotiveintelligence.io", [
        "AI for car dealerships",
        "how should a car dealership adopt AI",
        "best AI tools for auto dealers",
        "dealership AI consultant",
    ]),
    "Agent Empire": ("buildagentempire.com", [
        "how to build AI agents",
        "best course to learn to build AI agents",
        "community for building AI agents while keeping a job",
    ]),
    "Bookd": ("bookd.cx", [
        "CRM for life insurance agents",
        "compliance first CRM for insurance agency",
        "best CRM for insurance agency with audit trail",
    ]),
}

DOM_RE = re.compile(r"https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")

def extract_urls(obj, acc):
    """Recursively collect any URL strings anywhere in the response JSON."""
    if isinstance(obj, str):
        for m in DOM_RE.finditer(obj):
            acc.add(m.group(1).lower().lstrip("www."))
    elif isinstance(obj, dict):
        for v in obj.values(): extract_urls(v, acc)
    elif isinstance(obj, list):
        for v in obj: extract_urls(v, acc)

def ask(q):
    body = {"model": MODEL, "messages": [{"role": "user", "content": q}]}
    r = requests.post(URL, headers={"Authorization": f"Bearer {KEY}",
                      "Content-Type": "application/json"}, json=body, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    if not KEY:
        print("NO OPENROUTER_API_KEY in env (run under `doppler run`)"); sys.exit(1)
    if "--smoke" in sys.argv:
        j = ask("AI receptionist for HVAC business")
        print("TOP-LEVEL KEYS:", list(j.keys()))
        print("CHOICE KEYS:", list(j["choices"][0].keys()))
        print("MESSAGE KEYS:", list(j["choices"][0]["message"].keys()))
        print("USAGE:", j.get("usage"))
        doms = set(); extract_urls(j, doms)
        print("DOMAINS FOUND:", sorted(doms)[:15])
        print("CONTENT (first 300):", j["choices"][0]["message"]["content"][:300])
        return
    tot_cost = 0.0
    rows = []
    for brand, (domain, queries) in BRANDS.items():
        base = domain.lower().lstrip("www.")
        for q in queries:
            try:
                j = ask(q)
                u = j.get("usage", {}) or {}
                tot_cost += float(u.get("cost", 0) or 0)
                doms = set(); extract_urls(j, doms)
                content = j["choices"][0]["message"]["content"].lower()
                cited = (base in doms) or (base in content)
                others = sorted(d for d in doms if base.split(".")[0] not in d)[:5]
                rows.append((brand, q, "YES" if cited else "no", ", ".join(others)))
                print(f"  [{'✓CITED' if cited else '  ----'}] {brand} :: {q}")
                time.sleep(1)
            except Exception as e:
                rows.append((brand, q, "ERR", str(e)[:60]))
                print(f"  [ ERR ] {brand} :: {q} -> {str(e)[:80]}")
    print("\n\n================ PHASE-0 CITATION BASELINE ================\n")
    cur = None
    for brand, q, cited, others in rows:
        if brand != cur: print(f"\n### {brand} ({BRANDS[brand][0]})"); cur = brand
        print(f"  [{cited:>5}] {q}")
        if others and cited != "YES": print(f"          cited instead: {others}")
    n = len(rows); yes = sum(1 for r in rows if r[2] == "YES")
    print(f"\n---------------------------------------------------------")
    print(f"PORTFOLIO: cited in {yes}/{n} queries")
    print(f"EXACT SPEND (OpenRouter usage.cost sum): ${tot_cost:.4f}")

if __name__ == "__main__":
    main()
