#!/usr/bin/env python3
"""Local-flank probe: do national players dominate hyper-local DFW queries, or is it open field?"""
import os, sys, json, re, time
import requests

KEY = os.environ.get("OPENROUTER_API_KEY", "")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "perplexity/sonar"
DOM_RE = re.compile(r"https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")

# The national heavyweights that dominated the HEAD-term baseline
NATIONALS = {
    "dialzara.com","smith.ai","answerforce.com","answernet.com","answerpro.com","podium.com",
    "ruby.com","abby.ai","goanswer.io","directlineinc.com","alwaysanswering.com",
    "brightlocal.com","semrush.com","agencies.semrush.com","clutch.co","highervisibility.com",
    "mailchimp.com","thumbtack.com","angi.com","yelp.com","nativz.io","atomicdc.com",
}
# local / directory / open-field signals
LOCAL_SIG = ("yelp","nextdoor","google.com/maps","maps.google","chamber",".gov","bbb.org",
             "facebook.com","angi.com","thumbtack")

CASES = {
    "The AI Phone Guy (theaiphoneguy.com)": ("theaiphoneguy.com", [
        "HVAC answering service in Prosper TX",
        "answering service for plumbers in Frisco TX",
        "who answers after hours calls for roofers in McKinney Texas",
        "AI receptionist for HVAC company in Denton TX",
        "24/7 call answering service for electricians in Plano TX",
    ]),
    "Worship Digital (worshipdigital.co)": ("worshipdigital.co", [
        "local SEO agency in Frisco TX",
        "digital marketing agency in Prosper Texas",
        "marketing agency for small business in the 380 corridor Texas",
        "SEO company in McKinney TX",
        "web design and marketing for small business in Celina TX",
    ]),
}

def ask(q):
    body = {"model": MODEL, "messages": [{"role": "user", "content": q}]}
    r = requests.post(URL, headers={"Authorization": f"Bearer {KEY}",
                      "Content-Type": "application/json"}, json=body, timeout=60)
    r.raise_for_status(); return r.json()

def domains(j):
    d = set()
    def walk(o):
        if isinstance(o, str):
            for m in DOM_RE.finditer(o): d.add(m.group(1).lower().replace("www.",""))
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
                us = dom.replace("www.","") in d
                nats = sorted(d & NATIONALS)
                locals_ = sorted(x for x in d if any(s in x for s in LOCAL_SIG))
                if us: verdict = "WE'RE CITED ✓"
                elif nats: verdict = f"CONTESTED — nationals present: {nats}"
                elif locals_: verdict = f"OPEN FIELD (directories/local): {locals_[:4]}"
                else: verdict = "OPEN FIELD (generic/local specialists, no national lock)"
                print(f"  • {q}")
                print(f"      → {verdict}")
                print(f"      cited: {sorted(d)[:6]}")
                time.sleep(1)
            except Exception as e:
                print(f"  • {q} -> ERR {str(e)[:70]}")
    print(f"\n---\nEXACT SPEND: ${cost:.4f}")

if __name__ == "__main__":
    main()
