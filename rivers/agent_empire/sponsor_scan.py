"""Wade's sponsor scanner — extracts every tool, API, library, and service
used in the paperclip codebase to build a prioritized sponsor target list.

Scans:
- All imports in .py files
- All packages in requirements.txt
- All API keys in .env.example
- All services mentioned in comments
"""

import os
import re
from pathlib import Path
from core.logger import log_info

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# Known tool → sponsor mapping with contact info
SPONSOR_DATABASE = {
    "anthropic": {"tool": "Anthropic/Claude", "tier": "premium", "url": "https://anthropic.com", "contact_email": "partnerships@anthropic.com"},
    "twilio": {"tool": "Twilio", "tier": "premium", "url": "https://twilio.com", "contact_email": "partnerships@twilio.com"},
    "openai": {"tool": "OpenAI", "tier": "premium", "url": "https://openai.com", "contact_email": "partnerships@openai.com"},
    "hubspot": {"tool": "HubSpot", "tier": "premium", "url": "https://hubspot.com", "contact_email": "partnerships@hubspot.com"},
    "fastapi": {"tool": "FastAPI", "tier": "mid", "url": "https://fastapi.tiangolo.com", "contact_email": ""},
    "apscheduler": {"tool": "APScheduler", "tier": "mid", "url": "https://apscheduler.readthedocs.io", "contact_email": ""},
    "requests": {"tool": "Requests (PSF)", "tier": "mid", "url": "https://requests.readthedocs.io", "contact_email": ""},
    "uvicorn": {"tool": "Uvicorn", "tier": "mid", "url": "https://uvicorn.org", "contact_email": ""},
    "crewai": {"tool": "CrewAI", "tier": "premium", "url": "https://crewai.com", "contact_email": "partnerships@crewai.com"},
    "google-auth": {"tool": "Google Cloud", "tier": "premium", "url": "https://cloud.google.com", "contact_email": "partnerships@google.com"},
    "railway": {"tool": "Railway", "tier": "premium", "url": "https://railway.app", "contact_email": "partnerships@railway.app"},
    "gohighlevel": {"tool": "GoHighLevel", "tier": "premium", "url": "https://gohighlevel.com", "contact_email": "partnerships@gohighlevel.com"},
    "attio": {"tool": "Attio", "tier": "mid", "url": "https://attio.com", "contact_email": "partnerships@attio.com"},
}


def scan_imports() -> set:
    """Scan all .py files for import statements."""
    imports = set()
    for root, _, files in os.walk(REPO_ROOT):
        if "node_modules" in root or ".git" in root or "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                try:
                    content = Path(os.path.join(root, f)).read_text(errors="ignore")
                    for match in re.findall(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
                        imports.add(match.split(".")[0].lower())
                except Exception:
                    pass
    return imports


def scan_requirements() -> set:
    """Scan requirements.txt for package names."""
    packages = set()
    for req_file in ["requirements.txt", "requirements-py314.txt"]:
        req_path = os.path.join(REPO_ROOT, req_file)
        if os.path.exists(req_path):
            try:
                content = Path(req_path).read_text()
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = re.split(r"[>=<~!]", line)[0].strip().lower()
                        packages.add(pkg)
            except Exception:
                pass
    return packages


def scan_env_keys() -> set:
    """Scan .env.example for service references."""
    services = set()
    env_path = os.path.join(REPO_ROOT, ".env.example")
    if os.path.exists(env_path):
        try:
            content = Path(env_path).read_text()
            for line in content.splitlines():
                key = line.split("=")[0].strip().lower()
                if "ghl" in key:
                    services.add("gohighlevel")
                elif "attio" in key:
                    services.add("attio")
                elif "hubspot" in key:
                    services.add("hubspot")
                elif "twilio" in key:
                    services.add("twilio")
                elif "anthropic" in key:
                    services.add("anthropic")
                elif "gmail" in key:
                    services.add("google-auth")
                elif "skool" in key:
                    services.add("skool")
        except Exception:
            pass
    return services


def get_sponsor_targets() -> list:
    """Build prioritized sponsor target list from actual codebase usage."""
    log_info("agent_empire", "[WADE] Scanning codebase for sponsor targets...")

    all_tools = set()
    all_tools.update(scan_imports())
    all_tools.update(scan_requirements())
    all_tools.update(scan_env_keys())

    targets = []
    matched = set()
    for key, info in SPONSOR_DATABASE.items():
        if key in all_tools or any(key in t for t in all_tools):
            if info["tool"] not in matched:
                targets.append(info)
                matched.add(info["tool"])

    # Sort: premium first, then mid
    targets.sort(key=lambda x: 0 if x["tier"] == "premium" else 1)

    log_info("agent_empire", f"[WADE] Found {len(targets)} sponsor targets from codebase scan")
    for t in targets:
        log_info("agent_empire", f"  [{t['tier'].upper()}] {t['tool']} — {t['url']}")

    return targets


if __name__ == "__main__":
    targets = get_sponsor_targets()
    print(f"\n=== SPONSOR TARGETS ({len(targets)}) ===")
    for t in targets:
        print(f"  [{t['tier'].upper()}] {t['tool']} — {t['contact_email'] or 'no email'}")
