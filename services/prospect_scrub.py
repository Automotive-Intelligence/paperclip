"""services/prospect_scrub.py -- automated scrubbing for the legacy Tyler/
Marcus/Ryan Data prospecting backlog (Sales Desk 30-day accountability
review, 2026-09-01: "they need a data scrubbing system" -- Michael has no
time to hand-review).

Re-verifies each (marcus)/(ryan_data)-tagged Twenty opportunity's claimed
phone/email against the company's REAL, live primary site -- the same
evidence-only discipline as services/sdr_verification_gate.py, reused
directly (resolve_primary_site), not reimplemented. A claim that can't be
confirmed on a real, resolving site is marked unverifiable and never
presented as actionable. Non-destructive: this module only reads and
classifies -- it never deletes or edits a CRM record.

The crews that produced this backlog are paused (see app.py's
_prospecting_crews_enabled); this is a one-time cleanup pass over what
they already wrote, not a new production source.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import requests

from services.sdr_verification_gate import resolve_primary_site

_DIGIT = re.compile(r"\d")


def _digits(s: str) -> str:
    return "".join(_DIGIT.findall(s or ""))


def _phone_confirmed(html: str, claimed_phone: str) -> bool:
    """Last-10-digits match, formatting-agnostic (claimed '(817) 555-1234'
    must confirm against a site rendering 'tel:+18175551234' or plain text)."""
    claimed = _digits(claimed_phone)[-10:]
    if len(claimed) < 10:
        return False
    return claimed in _digits(html)


def _email_confirmed(html: str, claimed_email: str) -> bool:
    claimed = (claimed_email or "").strip().lower()
    if not claimed:
        return False
    return claimed in (html or "").lower()


def _fetch_html(domain: str) -> str:
    try:
        r = requests.get(f"https://{domain}", timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        return r.text if r.ok else ""
    except Exception:
        return ""


def verify_prospect(*, company_name: str, domain_on_file: str,
                     claimed_phone: str = "", claimed_email: str = "") -> Dict:
    """A real, deterministic verdict for one prospect. Never trusts the
    claim on file -- confirms it against the company's actual, resolved
    site before calling it usable."""
    if not (domain_on_file or "").strip():
        return {"verdict": "unverifiable", "reason": "no_domain_on_file"}
    if not claimed_phone and not claimed_email:
        return {"verdict": "unverifiable", "reason": "no_claimed_contact_to_check"}
    try:
        real_domain, _log = resolve_primary_site(domain_on_file, company_name)
    except Exception as e:
        return {"verdict": "unverifiable", "reason": f"resolve_failed:{type(e).__name__}"}
    if not real_domain:
        return {"verdict": "unverifiable", "reason": "site_unresolvable"}
    html = _fetch_html(real_domain)
    if not html:
        return {"verdict": "unverifiable", "reason": f"site_unreachable:{real_domain}"}
    phone_ok = _phone_confirmed(html, claimed_phone) if claimed_phone else False
    email_ok = _email_confirmed(html, claimed_email) if claimed_email else False
    if phone_ok or email_ok:
        return {"verdict": "verified", "real_domain": real_domain,
                "phone_confirmed": phone_ok, "email_confirmed": email_ok}
    return {"verdict": "unverifiable", "reason": "claimed_contact_not_found_on_real_site",
            "real_domain": real_domain}


# --------------------------------------------------------------------------- Twenty reads

def _company_domain(base: str, headers: dict, company_id: str) -> tuple:
    r = requests.get(f"{base}/rest/companies/{company_id}", headers=headers, timeout=20)
    if not r.ok:
        return "", ""
    c = (r.json().get("data") or {}).get("company") or {}
    raw = ((c.get("domainName") or {}).get("primaryLinkUrl")) or ""
    host = raw.strip().lower().split("://")[-1].split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return c.get("name") or "", host


def _person_contact(base: str, headers: dict, person_id: str) -> tuple:
    r = requests.get(f"{base}/rest/people/{person_id}", headers=headers, timeout=20)
    if not r.ok:
        return "", ""
    p = (r.json().get("data") or {}).get("person") or {}
    email = ((p.get("emails") or {}).get("primaryEmail")) or ""
    phone = ((p.get("phones") or {}).get("primaryPhoneNumber")) or ""
    return phone, email


def scrub_source(runtime_key: str, source_tag: str) -> Dict:
    """Read-only: re-verify every (source_tag)-tagged opportunity in this
    Twenty workspace. Returns counts plus a verified list ready to act on
    without opening a single CRM record."""
    import tools.twenty as T
    base, key = T._workspace_config(runtime_key)
    headers = T._headers(key)
    opps = T._iter_opportunities(base, key)
    tagged = [o for o in opps if f"({source_tag})" in (o.get("name") or "")]

    verified: List[Dict] = []
    unverifiable_reasons: Dict[str, int] = {}
    errors = 0

    for o in tagged:
        try:
            company_id = o.get("companyId") or ""
            person_id = o.get("pointOfContactId") or ""
            if not company_id:
                unverifiable_reasons["no_company_linked"] = unverifiable_reasons.get("no_company_linked", 0) + 1
                continue
            company_name, domain = _company_domain(base, headers, company_id)
            phone, email = _person_contact(base, headers, person_id) if person_id else ("", "")
            result = verify_prospect(company_name=company_name, domain_on_file=domain,
                                      claimed_phone=phone, claimed_email=email)
            if result["verdict"] == "verified":
                verified.append({
                    "opportunity_id": o.get("id"), "business_name": company_name or o.get("name"),
                    "real_domain": result["real_domain"],
                    "phone": phone if result.get("phone_confirmed") else "",
                    "email": email if result.get("email_confirmed") else "",
                })
            else:
                reason = result.get("reason", "unknown")
                unverifiable_reasons[reason] = unverifiable_reasons.get(reason, 0) + 1
        except Exception as e:
            errors += 1
            unverifiable_reasons[f"exception:{type(e).__name__}"] = (
                unverifiable_reasons.get(f"exception:{type(e).__name__}", 0) + 1)

    return {
        "source_tag": source_tag,
        "runtime_key": runtime_key,
        "considered": len(tagged),
        "verified": len(verified),
        "unverifiable": len(tagged) - len(verified) - errors,
        "errors": errors,
        "unverifiable_reasons": unverifiable_reasons,
        "verified_list": verified,
    }
