"""services/intent_scoring.py -- Fit x Intent 2x2 scoring service.

Item 3 of the B&T flag posted 2026-07-03 in avo-telemetry/revenue_state.md.
Complete spec: avo-telemetry/marketing_deliverables/intent_workflow_spec_v1_2026-07-03.md
section 3.

One function:  score(entity, signals, brand_config, config_version) -> ScoreResult
Same code for all 6 brands; only the brand.yaml weights differ.

Design decisions locked at the 2026-07-03 checkpoint (Michael):
  Q3.1  config_version stamps as a REQUIRED Person field in Twenty (not a tag).
        Twenty schema enforcement lands in item 6; for now this module stamps
        the value into the ScoreResult and the Twenty writer persists it.
  Q3.2  Signal tier -> base intent weight uses FIXED PLATONIC ANCHORS
        (T1=80, T2=45, T3=25). Brand configs override per-signal only with
        cited justification (schema does not enforce; convention only).
  Q3.3  fit_weights axes sum <= 100 (already enforced in config/brands/_schema.py).

Nothing in this module writes to a CRM or fires a channel adapter. The caller
(intent_workflow_runner or the unified webhook in item 4) is responsible for
persistence + activation gating on the returned quadrant/action pair.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from config.brands._schema import BrandConfig, SignalSource, SignalTier


# ---------------------------------------------------------------------------
# Signal record -- what the caller passes in per touchpoint (S1 output)
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """One S1 signal event against an entity.

    `source_name` matches a `SignalSource.name` from brand.yaml (permit_feed,
    intent_topics, meta_lead_form, etc.). If it does not, the scoring service
    treats it as tier T3 with a warning; the runner should surface those
    for the RevOps operator to add to the brand config.
    """
    source_name: str
    occurred_at: datetime
    # Optional per-signal weight override (e.g. a permit for a $5M commercial
    # property vs a $100k residential). Multiplies the tier base weight.
    intensity_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Entity record -- what the caller assembles from CRM + enrichment (S2 output)
# ---------------------------------------------------------------------------


@dataclass
class EntityFit:
    """Fit-axis inputs. Each in [0.0, 1.0]; the scoring service multiplies by
    the brand's per-axis weight cap to produce the point contribution.

    Example: brand.fit_weights.geo_match=40, EntityFit.geo_match=0.5 ->
    contributes 20 points to fit score.
    """
    geo_match: float = 0.0
    persona_match: float = 0.0
    solution_qualifier: float = 0.0
    firmographic: float = 0.0
    # Hard F gate: if any listed, fit_band forces to F regardless of the sum.
    negative_fit_reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixed platonic anchors (Q3.2 lock, 2026-07-03)
# ---------------------------------------------------------------------------

# T1 = behavioral, high-confidence (permit filed, form submitted, page hit)
# T2 = inferential (intent-topic surge, meta lead form)
# T3 = list-derived / long-tail (license registry, cold pull)
#
# Brands may override on a per-signal basis with cited justification; this
# module reads the source-tier off brand.yaml SignalSource.tier and looks up
# the anchor. A brand.yaml PR that overrides a per-signal base weight lands
# as a schema extension in a future item; for v1, tiers are the knob.
TIER_BASE_WEIGHT: Dict[SignalTier, int] = {
    "T1": 80,
    "T2": 45,
    "T3": 25,
}


# ---------------------------------------------------------------------------
# Score result -- what the caller writes to CRM + activation queue
# ---------------------------------------------------------------------------


Quadrant = Literal[
    "ACT_NOW",           # A/B fit x High intent
    "NURTURE_HOT",       # A/B fit x Med intent
    "WATCH",             # A/B fit x Low intent
    "QUALIFY_CAUTION",   # C fit x High intent (automated touch only)
    "NURTURE_LOW",       # C fit x Med intent (newsletter tier)
    "SUPPRESS_SOFT",     # C fit x Low intent (no spend)
    "SUPPRESS",          # F fit x anything (tire-kicker/competitor)
]

Action = Literal[
    "primary_channel_personalized",
    "multi_touch_nurture",
    "monitor_slow_nurture",
    "automated_touch_only",
    "newsletter_only",
    "no_spend",
    "suppress",
]

FitBand = Literal["A", "B", "C", "F"]
IntentBand = Literal["High", "Med", "Low"]


@dataclass
class ScoreResult:
    fit_score: int              # 0-100
    fit_band: FitBand
    intent_score: int           # 0-100
    intent_band: IntentBand
    quadrant: Quadrant
    action: Action
    sla_deadline_hours: Optional[float]  # None for suppress-* quadrants
    config_version: str         # stamped from the caller (Q3.1)
    # Diagnostic trail so an operator can see why an entity landed in a quadrant.
    debug_trace: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Quadrant -> action + SLA (identical across brands per spec section 3)
# ---------------------------------------------------------------------------

# SLA hours per spec section 3: Act-Now <4 business hours, replies <30 min
# (handled in the reply handler, not here). Nurture-Hot <48h.
_QUADRANT_TABLE: Dict[Quadrant, tuple] = {
    "ACT_NOW":         ("primary_channel_personalized", 4.0),
    "NURTURE_HOT":     ("multi_touch_nurture", 48.0),
    "WATCH":           ("monitor_slow_nurture", None),
    "QUALIFY_CAUTION": ("automated_touch_only", None),
    "NURTURE_LOW":     ("newsletter_only", None),
    "SUPPRESS_SOFT":   ("no_spend", None),
    "SUPPRESS":        ("suppress", None),
}


def _quadrant_from_bands(fit: FitBand, intent: IntentBand) -> Quadrant:
    """Spec section 3 table, byte-for-byte."""
    if fit == "F":
        return "SUPPRESS"
    if fit in ("A", "B"):
        if intent == "High":
            return "ACT_NOW"
        if intent == "Med":
            return "NURTURE_HOT"
        return "WATCH"
    # C fit
    if intent == "High":
        return "QUALIFY_CAUTION"
    if intent == "Med":
        return "NURTURE_LOW"
    return "SUPPRESS_SOFT"


# ---------------------------------------------------------------------------
# Fit axis (0-100)
# ---------------------------------------------------------------------------


def _fit_score(fit: EntityFit, brand: BrandConfig) -> int:
    """Weighted sum: sum over axes of (fit_axis_ratio * weight_cap).
    Weights come from brand.fit_weights (config), ratios from EntityFit
    (per-entity enrichment). Returns int in [0, 100].
    """
    if fit.negative_fit_reasons:
        return 0
    w = brand.fit_weights
    total = (
        fit.geo_match * w.geo_match
        + fit.persona_match * w.persona_match
        + fit.solution_qualifier * w.solution_qualifier
        + fit.firmographic * w.firmographic
    )
    return max(0, min(100, int(round(total))))


def _fit_band(fit_score: int, fit: EntityFit) -> FitBand:
    """Spec section 3 bands. Negative-fit reasons force F regardless of score."""
    if fit.negative_fit_reasons:
        return "F"
    if fit_score >= 75:
        return "A"
    if fit_score >= 50:
        return "B"
    if fit_score >= 25:
        return "C"
    return "F"


# ---------------------------------------------------------------------------
# Intent axis (0-100)
# ---------------------------------------------------------------------------


def _recency_multiplier(hours_since: float) -> float:
    """Step function per spec section 3:
      1.0 (<72h), 0.6 (3-10d), 0.3 (10-30d), 0.1 (>30d).
    Half-life is signal-specific (SignalSource.half_life_days) and modulates
    the boundary between the 0.6 and 0.3 tiers.
    """
    if hours_since < 72:
        return 1.0
    days = hours_since / 24.0
    if days < 10:
        return 0.6
    if days < 30:
        return 0.3
    return 0.1


def _half_life_adjustment(base_multiplier: float, hours_since: float, half_life_days: int) -> float:
    """Overlay a continuous half-life decay on top of the step function so
    two signals with the same tier but different half-lives (a permit vs a
    pricing-page hit) age at different rates within the same tier band.

    Formula: mult * 0.5 ** ((days / half_life) - 1) capped at [0.1, 1.0].
    A signal exactly one half-life old gets its step-multiplier verbatim;
    older signals get exponentially less; newer signals a boost up to 1.0.
    """
    days = hours_since / 24.0
    if half_life_days <= 0:
        return base_multiplier
    ratio = days / half_life_days
    # Center the decay so a signal at exactly one half-life gets the step
    # value; younger signals get 1.0 (capped), older signals decay.
    factor = 0.5 ** max(0.0, ratio - 1.0)
    return max(0.1, min(1.0, base_multiplier * factor))


def _frequency_boost(signal_count: int) -> float:
    """log2(n+1) boost, capped at 2.0. One signal -> 1.0, two -> 1.58,
    four -> 2.32 clipped to 2.0. Spec section 3 does not fix the formula;
    this is a reasonable default consistent with the 'signals accumulate,
    but no single signal can carry a low-fit entity' principle.
    """
    if signal_count <= 0:
        return 0.0
    return min(2.0, math.log2(signal_count + 1))


def _intent_score(
    signals: List[Signal],
    brand: BrandConfig,
    now: datetime,
) -> tuple[int, Dict[str, Any]]:
    """Intent axis 0-100.

    intent = min(100, sum(per_signal_score) * frequency_boost)

    Per-signal score = tier_base_weight * recency_multiplier
    * half_life_adjustment * intensity_multiplier.

    Returns (score, per_signal_debug_dict).
    """
    if not signals:
        return 0, {"reason": "no_signals"}

    # Build a lookup: source_name -> SignalSource from brand.yaml.
    source_map: Dict[str, SignalSource] = {s.name: s for s in brand.signal_sources}

    unknown_sources: List[str] = []
    contributions: List[Dict[str, Any]] = []
    total = 0.0
    for sig in signals:
        source = source_map.get(sig.source_name)
        if source is None:
            unknown_sources.append(sig.source_name)
            tier: SignalTier = "T3"
            half_life = 14
        else:
            tier = source.tier
            half_life = source.half_life_days
        base = TIER_BASE_WEIGHT[tier]
        hours = max(0.0, (now - sig.occurred_at).total_seconds() / 3600.0)
        step = _recency_multiplier(hours)
        adjusted = _half_life_adjustment(step, hours, half_life)
        contribution = base * adjusted * sig.intensity_multiplier
        total += contribution
        contributions.append({
            "source": sig.source_name,
            "tier": tier,
            "base": base,
            "hours_since": round(hours, 2),
            "step_mult": step,
            "adjusted_mult": round(adjusted, 3),
            "intensity_mult": sig.intensity_multiplier,
            "contribution": round(contribution, 2),
            "known_source": source is not None,
        })

    boost = _frequency_boost(len(signals))
    boosted = total * boost
    final = max(0, min(100, int(round(boosted))))
    debug = {
        "signals": contributions,
        "sum_pre_boost": round(total, 2),
        "frequency_boost": round(boost, 3),
        "sum_post_boost": round(boosted, 2),
        "unknown_sources": unknown_sources,
    }
    return final, debug


def _intent_band(intent_score: int) -> IntentBand:
    if intent_score >= 60:
        return "High"
    if intent_score >= 30:
        return "Med"
    return "Low"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score(
    fit: EntityFit,
    signals: List[Signal],
    brand: BrandConfig,
    config_version: str,
    now: Optional[datetime] = None,
) -> ScoreResult:
    """The one function all 6 brands call. Returns a ScoreResult the caller
    persists to CRM and reads to route into an activation channel.

    `config_version` must be the SHA-256 hash of the loaded brand.yaml source
    bytes (from `_load_brand_config` in intent_workflow_runner.py). The
    scoring service does not compute it; the caller does, once per brand-run.

    `now` is injectable for deterministic tests; defaults to UTC now.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    fit_score_val = _fit_score(fit, brand)
    fit_band_val = _fit_band(fit_score_val, fit)
    intent_score_val, intent_debug = _intent_score(signals, brand, now)
    intent_band_val = _intent_band(intent_score_val)
    quadrant_val = _quadrant_from_bands(fit_band_val, intent_band_val)
    action_val, sla_hours = _QUADRANT_TABLE[quadrant_val]

    debug_trace = {
        "fit_axis": {
            "geo_match": fit.geo_match,
            "persona_match": fit.persona_match,
            "solution_qualifier": fit.solution_qualifier,
            "firmographic": fit.firmographic,
            "weights": {
                "geo_match": brand.fit_weights.geo_match,
                "persona_match": brand.fit_weights.persona_match,
                "solution_qualifier": brand.fit_weights.solution_qualifier,
                "firmographic": brand.fit_weights.firmographic,
            },
            "negative_fit_reasons": fit.negative_fit_reasons,
        },
        "intent_axis": intent_debug,
        "brand": brand.brand,
        "config_version": config_version,
    }

    return ScoreResult(
        fit_score=fit_score_val,
        fit_band=fit_band_val,
        intent_score=intent_score_val,
        intent_band=intent_band_val,
        quadrant=quadrant_val,
        action=action_val,
        sla_deadline_hours=sla_hours,
        config_version=config_version,
        debug_trace=debug_trace,
    )


# ---------------------------------------------------------------------------
# Twenty custom-field manifest (Q3.1 lock: config_version + score fields as
# real Person fields, not tags)
#
# This is the manifest the caller (item 6 Twenty schema enforcement) will
# assert exists at startup. For item 3 the module exposes it as a constant so
# the workflow runner + Twenty writer share one source of truth.
# ---------------------------------------------------------------------------

TWENTY_PERSON_CUSTOM_FIELDS: List[Dict[str, str]] = [
    # (name, type, description) tuples; item 6 will use these to hard-fail
    # on startup if a Twenty workspace is missing any field.
    {"name": "config_version",     "type": "TEXT",   "desc": "SHA-256 (first 16) of brand.yaml at scoring time."},
    {"name": "fit_score",          "type": "NUMBER", "desc": "Fit axis 0-100."},
    {"name": "fit_band",           "type": "TEXT",   "desc": "A/B/C/F."},
    {"name": "intent_score",       "type": "NUMBER", "desc": "Intent axis 0-100."},
    {"name": "intent_band",        "type": "TEXT",   "desc": "High/Med/Low."},
    {"name": "quadrant",           "type": "TEXT",   "desc": "ACT_NOW/NURTURE_HOT/WATCH/QUALIFY_CAUTION/NURTURE_LOW/SUPPRESS_SOFT/SUPPRESS."},
    {"name": "activation_channel", "type": "TEXT",   "desc": "channel_roster entry that fired (cold_email, direct_mail, etc.)."},
    {"name": "compliance_status",  "type": "TEXT",   "desc": "clear/opt_in/opt_out/dnc/suppressed/region_restricted."},
    {"name": "consent_basis",      "type": "TEXT",   "desc": "legitimate_interest/express_consent/existing_customer/none."},
]


__all__ = [
    "Action",
    "EntityFit",
    "FitBand",
    "IntentBand",
    "Quadrant",
    "ScoreResult",
    "Signal",
    "TIER_BASE_WEIGHT",
    "TWENTY_PERSON_CUSTOM_FIELDS",
    "score",
]
