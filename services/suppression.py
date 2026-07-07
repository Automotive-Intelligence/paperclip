"""services/suppression.py -- Pre-send suppression / opt-out / DNC gate.

COMPLIANCE-CRITICAL. Named a HARD precondition in Boardroom Readouts 003/004.
Before ANY cold-email lead is enrolled into Instantly by
services/intent_workflow_runner.py, it MUST pass through this gate. Anyone who
has unsubscribed, bounced, been marked do-not-contact, or is an existing
customer is dropped BEFORE enrollment.

Prior to this module the only suppression was REACTIVE and on other paths:
  - services/smartlead_webhook_handler.py writes a `[DO-NOT-CONTACT]` sentinel
    into Twenty (WD only) AFTER a send provokes an unsub/bounce.
  - scripts/pp_klaviyo_suppress_cold.py is a one-off P&P Klaviyo cleanup.
Neither gates the Instantly cold path. This module closes that hole.

Suppression sources (union), most-authoritative first:
  1. Local suppression ledger  SUPPRESSION_DIR/<brand>.txt  — one lowercased
     email per line. This is where the one-click unsubscribe endpoint and any
     bounce sync record opt-outs. Absent file == empty (reachable).
  2. Customers / purchasers list  SUPPRESSION_DIR/<brand>_customers.csv  — an
     `email` column of existing customers we must never cold-email. Absent
     file == skipped (not applicable).
  3. Twenty do-not-contact markers — the `[DO-NOT-CONTACT]` sentinel the
     Smartlead handler writes. Consulted ONLY for brands whose business_key
     maps to a configured Twenty workspace (today: WD/AvI/Book'd).

FAIL-CLOSED contract:
  - A source that is CONFIGURED but UNREACHABLE (e.g. Twenty API error, or a
    ledger file that exists but can't be read) makes the whole index
    "degraded". build_suppression_index raises SuppressionSourceUnreachable
    unless allow_unreachable=True is passed (the explicit operator override).
    The runner turns that into a non-zero exit and enrolls NOBODY.
  - A source that is simply NOT CONFIGURED / not applicable (no Twenty
    workspace for the brand, no customers file) is skipped, not an error.
    The local ledger is always a reachable source (empty when the file is
    absent), so there is always at least one authoritative source.

Also hosts the runtime PLACEHOLDER-ADDRESS GUARD: refuse to enroll/send for any
brand whose compliance_profile physical address is still a placeholder, so a
fake CAN-SPAM address physically cannot go out. Today only pp.yaml passes.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# The Twenty do-not-contact sentinel the Smartlead handler writes into jobTitle.
# Keep in sync with services/smartlead_webhook_handler.py::_suppress_in_twenty.
DNC_SENTINEL = "[DO-NOT-CONTACT]"

# Where local suppression artifacts live. Overridable so tests can point at a
# temp dir. Default sits next to the repo's data/ tree.
_DEFAULT_SUPPRESSION_DIR = Path(__file__).resolve().parent.parent / "data" / "suppression"


class SuppressionError(RuntimeError):
    """Base class for suppression-gate failures."""


class SuppressionSourceUnreachable(SuppressionError):
    """A configured suppression source could not be read. FAIL-CLOSED: do not
    enroll unless the operator passes the explicit override."""


class PlaceholderAddressError(SuppressionError):
    """The brand's CAN-SPAM physical address is still a placeholder. Refuse to
    enroll/send until a real owner-provided address is present."""


# ---------------------------------------------------------------------------
# Placeholder-address guard
# ---------------------------------------------------------------------------

# Substrings that mark an address as a not-yet-real placeholder. Matched
# case-insensitively. Kept broad on purpose (fail-closed: when in doubt, block).
_PLACEHOLDER_MARKERS = (
    "update",                 # "(update with Anytime Mailbox VBA address ...)"
    "before any live send",
    "placeholder",
    "dfw, tx (",              # the specific literal shape in the stub configs
    "tbd",
    "xxxx",
    "your address",
)


def is_placeholder_address(address: Optional[str]) -> bool:
    """True if `address` is missing or looks like a placeholder (fail-closed).

    A real address must (a) be non-empty, (b) contain none of the placeholder
    markers, and (c) contain at least one digit (a street/PO/ZIP number). The
    stub brand configs ("Automotive Intelligence, DFW, TX (update ...)") fail
    on both (b) and (c); pp.yaml's real Austin address passes.
    """
    if not address or not address.strip():
        return True
    low = address.lower()
    if any(marker in low for marker in _PLACEHOLDER_MARKERS):
        return True
    if not any(ch.isdigit() for ch in address):
        # No street number / ZIP anywhere -> not a mailable CAN-SPAM address.
        return True
    return False


def assert_real_address(brand) -> None:
    """Raise PlaceholderAddressError unless the brand's compliance_profile has a
    real (non-placeholder) physical address. Call this BEFORE any enroll/send.

    `brand` is a config.brands._schema.BrandConfig (duck-typed here to avoid a
    hard import cycle).
    """
    address = getattr(getattr(brand, "compliance_profile", None), "physical_address", None)
    if is_placeholder_address(address):
        shown = (address or "").strip().splitlines()[0] if address else "(none)"
        raise PlaceholderAddressError(
            f"brand '{getattr(brand, 'brand', '?')}' physical_address is a "
            f"placeholder: {shown!r}. Refusing to enroll/send until a real "
            f"CAN-SPAM mailing address is present in the brand config "
            f"(owner-provided data — do not fabricate)."
        )


# ---------------------------------------------------------------------------
# Suppression index
# ---------------------------------------------------------------------------


@dataclass
class SuppressionIndex:
    """The union of every reachable suppression source for one brand."""

    brand: str
    emails: Set[str] = field(default_factory=set)
    sources: List[str] = field(default_factory=list)     # human-readable status
    degraded: bool = False                                # a configured source failed

    def is_suppressed(self, email: str) -> bool:
        return (email or "").strip().lower() in self.emails


def _suppression_dir() -> Path:
    override = (os.getenv("SUPPRESSION_DIR") or "").strip()
    return Path(os.path.expanduser(override)) if override else _DEFAULT_SUPPRESSION_DIR


def _ledger_path(brand_key: str) -> Path:
    return _suppression_dir() / f"{brand_key}.txt"


def _customers_path(brand_key: str) -> Path:
    return _suppression_dir() / f"{brand_key}_customers.csv"


def _read_ledger(brand_key: str) -> Tuple[Set[str], str]:
    """Read the local opt-out ledger. Absent file == empty (reachable).
    Raises SuppressionSourceUnreachable if the file exists but can't be read."""
    path = _ledger_path(brand_key)
    if not path.exists():
        return set(), f"ledger:{path.name}(absent,ok)"
    try:
        emails = {
            line.strip().lower()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        return emails, f"ledger:{path.name}({len(emails)})"
    except OSError as e:
        raise SuppressionSourceUnreachable(f"ledger {path} unreadable: {e}") from e


def _read_customers(brand_key: str) -> Tuple[Set[str], str]:
    """Read the existing-customers/purchasers suppression list. Absent == not
    applicable. Raises SuppressionSourceUnreachable if present but unreadable."""
    path = _customers_path(brand_key)
    if not path.exists():
        return set(), f"customers:{path.name}(absent,n/a)"
    try:
        emails: Set[str] = set()
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            # tolerate an 'email' column in any case
            field_map = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}
            key = field_map.get("email")
            if key is None:
                raise SuppressionSourceUnreachable(
                    f"customers file {path} has no 'email' column "
                    f"(columns={reader.fieldnames})"
                )
            for row in reader:
                e = (row.get(key) or "").strip().lower()
                if e:
                    emails.add(e)
        return emails, f"customers:{path.name}({len(emails)})"
    except OSError as e:
        raise SuppressionSourceUnreachable(f"customers {path} unreadable: {e}") from e


def _read_twenty_dnc(business_key: Optional[str]) -> Tuple[Set[str], str]:
    """Read do-not-contact markers from the brand's Twenty workspace.

    Only applicable when the brand's business_key maps to a configured Twenty
    workspace (WD/AvI/Book'd). Not configured -> skipped (n/a). Configured but
    the API errors -> SuppressionSourceUnreachable (fail-closed).
    """
    bk = (business_key or "").strip().lower()
    if not bk:
        return set(), "twenty:no_business_key(n/a)"
    try:
        from tools import twenty as _twenty  # local import; avoids hard dep at import time
    except Exception as e:  # pragma: no cover - import guard
        return set(), f"twenty:import_failed(n/a:{e})"

    if not _twenty.twenty_ready(bk):
        return set(), f"twenty:{bk}(not_configured,n/a)"

    try:
        base_url, api_key = _twenty._workspace_config(bk)
        emails = _fetch_twenty_dnc_emails(base_url, api_key)
        return emails, f"twenty:{bk}({len(emails)})"
    except Exception as e:
        raise SuppressionSourceUnreachable(
            f"twenty DNC read failed for business_key={bk!r}: {e}"
        ) from e


def _fetch_twenty_dnc_emails(base_url: str, api_key: str, max_pages: int = 25) -> Set[str]:
    """Page Twenty /rest/people filtering on the DNC sentinel in jobTitle and
    collect every primary email. Raises on any HTTP error (caller fails-closed).
    """
    import requests  # local import keeps module import cheap

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    emails: Set[str] = set()
    cursor: Optional[str] = None
    # Twenty stores the sentinel as a substring of jobTitle; ilike matches it.
    filt = f"jobTitle[ilike]:%{DNC_SENTINEL}%"
    for _ in range(max_pages):
        params: Dict[str, object] = {"filter": filt, "limit": 60}
        if cursor:
            params["starting_after"] = cursor
        r = requests.get(
            f"{base_url}/rest/people", headers=headers, params=params, timeout=15
        )
        r.raise_for_status()
        body = r.json()
        people = (body.get("data") or {}).get("people") or []
        for person in people:
            primary = ((person.get("emails") or {}).get("primaryEmail") or "").strip().lower()
            if primary:
                emails.add(primary)
        page_info = body.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    return emails


def build_suppression_index(
    brand,
    allow_unreachable: bool = False,
) -> SuppressionIndex:
    """Build the union suppression index for `brand` (a BrandConfig).

    FAIL-CLOSED: if any CONFIGURED source is unreachable, raise
    SuppressionSourceUnreachable unless allow_unreachable=True (the explicit
    operator override), in which case the failure is logged, the index is
    marked degraded, and only the sources that succeeded are applied.
    """
    brand_key = getattr(brand, "brand", "") or ""
    business_key = getattr(brand, "business_key", None)

    idx = SuppressionIndex(brand=brand_key)
    readers = (
        lambda: _read_ledger(brand_key),
        lambda: _read_customers(brand_key),
        lambda: _read_twenty_dnc(business_key),
    )
    for reader in readers:
        try:
            emails, status = reader()
            idx.emails |= emails
            idx.sources.append(status)
        except SuppressionSourceUnreachable as e:
            idx.degraded = True
            idx.sources.append(f"UNREACHABLE:{e}")
            if not allow_unreachable:
                raise
            logger.warning("suppression source unreachable (override active): %s", e)

    logger.info(
        "suppression index brand=%s total=%d degraded=%s sources=%s",
        brand_key, len(idx.emails), idx.degraded, idx.sources,
    )
    return idx


# ---------------------------------------------------------------------------
# Public filter API
# ---------------------------------------------------------------------------


def is_suppressed(email: str, brand, allow_unreachable: bool = False) -> bool:
    """True if `email` is on any suppression source for `brand`.

    Builds a fresh index each call (fine for one-off checks; use
    filter_suppressed for batches). Fails closed per build_suppression_index.
    """
    idx = build_suppression_index(brand, allow_unreachable=allow_unreachable)
    return idx.is_suppressed(email)


@dataclass
class FilterResult:
    kept: List[dict]
    suppressed: List[str]
    index: SuppressionIndex

    @property
    def loaded(self) -> int:
        return len(self.kept) + len(self.suppressed)

    @property
    def enrolled(self) -> int:
        return len(self.kept)

    @property
    def suppressed_count(self) -> int:
        return len(self.suppressed)


def filter_suppressed(
    leads: Iterable[dict],
    brand,
    email_key: str = "email",
    allow_unreachable: bool = False,
) -> FilterResult:
    """Split `leads` into (kept, suppressed) using the brand suppression index.

    Builds the index ONCE. A lead with no/blank email is dropped as suppressed
    (fail-closed: we never enroll an address we can't check). Fails closed on
    an unreachable configured source unless allow_unreachable=True.
    """
    idx = build_suppression_index(brand, allow_unreachable=allow_unreachable)
    kept: List[dict] = []
    suppressed: List[str] = []
    for lead in leads:
        email = (lead.get(email_key) or "").strip().lower()
        if not email or idx.is_suppressed(email):
            suppressed.append(email or "(blank)")
        else:
            kept.append(lead)
    return FilterResult(kept=kept, suppressed=suppressed, index=idx)


# ---------------------------------------------------------------------------
# Recording opt-outs (called by the one-click unsubscribe endpoint)
# ---------------------------------------------------------------------------


def record_unsubscribe(email: str, brand_key: str, reason: str = "one_click_unsub") -> str:
    """Append an opt-out to the local ledger (idempotent) and best-effort mirror
    it into Twenty. The ledger write is the load-bearing, always-available
    record; Twenty is best-effort (a cold lead may not exist as a Twenty
    person). Returns a short status string.
    """
    email = (email or "").strip().lower()
    brand_key = (brand_key or "").strip().lower()
    if not email or not brand_key:
        return "ignored_missing_args"

    path = _ledger_path(brand_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing, _ = _read_ledger(brand_key)
        if email not in existing:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{email}\n")
        status = "ledger_recorded"
    except OSError as e:
        logger.error("record_unsubscribe ledger write failed for %s/%s: %s", brand_key, email, e)
        return "ledger_write_failed"

    # Best-effort Twenty mirror so downstream CRM filters see it too.
    try:
        _mirror_unsub_to_twenty(email, brand_key, reason)
    except Exception as e:  # never let Twenty break the opt-out record
        logger.warning("record_unsubscribe twenty mirror failed for %s: %s", email, e)

    logger.info("unsubscribe recorded brand=%s email=%s reason=%s -> %s", brand_key, email, reason, status)
    return status


def _mirror_unsub_to_twenty(email: str, brand_key: str, reason: str) -> None:
    """Best-effort: flag the matching Twenty person do-not-contact, reusing the
    same jobTitle sentinel convention as the Smartlead handler. No-op if the
    brand has no Twenty workspace or the person isn't found."""
    business_key = _brand_business_key(brand_key)
    if not business_key:
        return
    from tools import twenty as _twenty

    if not _twenty.twenty_ready(business_key):
        return
    import requests

    base_url, api_key = _twenty._workspace_config(business_key)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = requests.get(
        f"{base_url}/rest/people",
        headers=headers,
        params={"filter": f"emails.primaryEmail[eq]:{email}", "limit": 1},
        timeout=15,
    )
    r.raise_for_status()
    people = (r.json().get("data") or {}).get("people") or []
    if not people:
        return
    person = people[0]
    existing_title = person.get("jobTitle") or ""
    if DNC_SENTINEL in existing_title:
        return
    new_title = f"{DNC_SENTINEL} {existing_title}".strip()
    requests.patch(
        f"{base_url}/rest/people/{person['id']}",
        headers=headers,
        json={"jobTitle": new_title},
        timeout=15,
    ).raise_for_status()


def _brand_business_key(brand_key: str) -> Optional[str]:
    """Resolve a brand key (filename stem) to its business_key by reading the
    brand config. Returns None if not resolvable (endpoint stays best-effort)."""
    try:
        from services.intent_workflow_runner import _load_brand_config

        brand, _ = _load_brand_config(brand_key)
        return getattr(brand, "business_key", None)
    except Exception:
        return None


__all__ = [
    "DNC_SENTINEL",
    "FilterResult",
    "PlaceholderAddressError",
    "SuppressionError",
    "SuppressionIndex",
    "SuppressionSourceUnreachable",
    "assert_real_address",
    "build_suppression_index",
    "filter_suppressed",
    "is_placeholder_address",
    "is_suppressed",
    "record_unsubscribe",
]
