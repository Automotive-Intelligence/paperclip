"""config/brands/_schema.py -- Pydantic schema for brand.yaml.

Every brand.yaml validates against BrandConfig. The runner refuses to execute a
brand whose config fails validation. Schema mirrors sections of the Universal
Intent-to-Revenue Workflow spec (marketing_deliverables/
intent_workflow_spec_v1_2026-07-03.md):

  spec section              ->   schema field
  -------------------------------------------
  S0 config + ICP           ->   BrandConfig.brand, BrandConfig.icp
  S1 signal_sources         ->   BrandConfig.signal_sources
  S3 fit_weights            ->   BrandConfig.fit_weights
  S5 channel_roster + email ->   BrandConfig.channel_roster,
                                 BrandConfig.instantly (per-channel adapter)
  S5 compliance_profile     ->   BrandConfig.compliance_profile
  S7 success_metric         ->   BrandConfig.success_metric

CONFIG-VERSION stamping: the runner hashes the loaded YAML source bytes and
writes the hash into every CRM record it produces so S8 (learning loop) can
attribute a decision to the exact config that produced it. Non-negotiable per
spec section 3. The runner reads this file; do not compute the hash inside
BrandConfig (Pydantic normalizes the model in ways that change the bytes).

Forward compatibility: fields with `default_factory=list` or `Optional` are the
extension points for items 3-4 of the B&T flag (scoring service + unified
inbound webhook). Adding them here now lets the runner stub them cleanly and
the item 3/4 PRs fill them in without a schema migration.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# S0 -- ICP definition
# ---------------------------------------------------------------------------


class GeoRadius(BaseModel):
    """Panda-style geo constraint. Every brand can have one; email brands
    may leave it null (national or unbounded)."""
    center: str  # e.g. "Belton TX"
    miles: int = Field(gt=0)


class ICP(BaseModel):
    """Ideal Customer Profile. Segments are named list entries the runner uses
    to route into per-ICP creative + lead files (mirrors the P&P engine's
    ICP_CONFIGS map)."""
    model_config = ConfigDict(extra="forbid")

    segments: List[str] = Field(min_length=1)
    geo_radius: Optional[GeoRadius] = None
    # Roles/titles/personas we accept (for email brands). Free-form list; the
    # runner uses these only to prefill Instantly custom variables today.
    personas: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# S1 -- signal sources
# ---------------------------------------------------------------------------


SignalTier = Literal["T1", "T2", "T3"]


class SignalSource(BaseModel):
    """One S1 input. `name` matches an adapter (permit_feed, intent_topics,
    meta_lead_form, etc.). `tier` sets the base weight band per spec section 3."""
    model_config = ConfigDict(extra="forbid")

    name: str
    tier: SignalTier
    # Half-life in days for recency decay (spec section 3): permit ~ 21d,
    # pricing-page hit ~ 3d. Runner uses this at scoring time (item 3).
    half_life_days: int = Field(gt=0, default=14)


# ---------------------------------------------------------------------------
# S3 -- fit weights (config axis)
# ---------------------------------------------------------------------------


class FitWeights(BaseModel):
    """Per-axis weights that sum to a fit score 0-100. Bands (A 75-100, B 50-74,
    C 25-49, F <25 suppress) are fixed in the scoring service, not here."""
    model_config = ConfigDict(extra="forbid")

    geo_match: int = Field(ge=0, le=100, default=0)
    persona_match: int = Field(ge=0, le=100, default=0)
    solution_qualifier: int = Field(ge=0, le=100, default=0)
    firmographic: int = Field(ge=0, le=100, default=0)
    # Negative-fit deductions (competitor, wrong-segment) are hard F gates in
    # the scoring service; we do not weight them here.

    @field_validator("firmographic")
    @classmethod
    def _weights_sum_leq_100(cls, v: int, info):
        """Sanity check: axis weights should sum <=100. A brand config that
        exceeds 100 is a spec bug (the scoring service caps individual axes
        but a sum >100 means someone double-counted).
        """
        d = info.data
        total = d.get("geo_match", 0) + d.get("persona_match", 0) + d.get("solution_qualifier", 0) + v
        if total > 100:
            raise ValueError(f"fit_weights sum to {total}; must be <=100")
        return v


# ---------------------------------------------------------------------------
# S5 -- channel roster + per-channel adapter config
# ---------------------------------------------------------------------------


ChannelName = Literal[
    "cold_email",       # Instantly / Smartlead
    "warm_email",       # Loops (P&P today)
    "linkedin",         # HeyReach (future)
    "direct_mail",      # Lob / PostGrid (item 5)
    "meta_lead_ad",     # Meta custom-audience export (item 5)
    "sms",              # Twilio / GHL (consent only, future)
    "inbound_call",     # CallRail / GHL Voice AI
    "sheet_drop",       # last-resort fallback (item 5)
]


class InstantlyConfig(BaseModel):
    """Instantly-specific adapter config. Present only when cold_email is in
    channel_roster. Mirrors the P&P engine's SENDING_ACCOUNTS + COMMON + SCHEDULE."""
    model_config = ConfigDict(extra="forbid")

    api_key_env: str  # e.g. "INSTANTLY_API_KEY_PAPERANDPURPOSE"
    sending_accounts: List[str] = Field(min_length=1)
    daily_limit: int = Field(gt=0, default=90)
    daily_max_leads: int = Field(gt=0, default=40)
    email_gap_minutes: int = Field(gt=0, default=12)
    random_wait_max: int = Field(ge=0, default=8)
    open_tracking: bool = False
    link_tracking: bool = False
    text_only: bool = False
    stop_on_reply: bool = True
    stop_on_auto_reply: bool = True
    # Time-of-day + weekdays; if omitted the runner uses a conservative default.
    schedule_start_hour: int = Field(ge=0, le=23, default=8)
    schedule_end_hour: int = Field(ge=1, le=24, default=17)
    schedule_timezone: str = "America/Chicago"
    schedule_days: List[int] = Field(  # 0=Mon..6=Sun per Instantly convention
        default_factory=lambda: [0, 1, 2, 3, 4],
    )


class ComplianceProfile(BaseModel):
    """Hard gate. The runner refuses to activate any channel whose compliance
    rules are not listed here (spec section 5)."""
    model_config = ConfigDict(extra="forbid")

    channels: List[ChannelName] = Field(min_length=1)
    hard_rules: List[str] = Field(default_factory=list)
    # Physical mailing address required for CAN-SPAM email footer / direct mail.
    physical_address: Optional[str] = None
    unsubscribe_footer: Optional[str] = None


# ---------------------------------------------------------------------------
# Content -- per-ICP creative (the map that used to be ICP_CONFIGS)
# ---------------------------------------------------------------------------


class EmailStep(BaseModel):
    """One touch in a cold email sequence."""
    model_config = ConfigDict(extra="forbid")

    delay_days: int = Field(ge=0)
    subject: str
    body: str  # plain-text; the runner wraps in Instantly-safe HTML at build time


class ICPContent(BaseModel):
    """Per-ICP creative payload. Segment key matches ICP.segments entries."""
    model_config = ConfigDict(extra="forbid")

    campaign_name: str  # display name in Instantly
    lead_file: Optional[str] = None  # relative to leads_dir
    steps: List[EmailStep] = Field(min_length=1)
    # First-touch subject/body use these tokens. The runner substitutes the CTA
    # from BrandConfig.cta_url and appends the footer from ComplianceProfile.
    # {{firstName}}, {{cta}}, {{footer}} auto-substitute; anything else passes
    # through raw and Instantly's own mail-merge handles it.


# ---------------------------------------------------------------------------
# Top-level brand config
# ---------------------------------------------------------------------------


class BrandConfig(BaseModel):
    """The full brand.yaml contract. Any field the runner needs to read from a
    brand-specific YAML lives here. Anything computed at runtime (config_version
    hash, per-run tallies) does NOT live here."""
    model_config = ConfigDict(extra="forbid")

    # Identity
    brand: str  # matches the filename stem: panda.yaml -> "panda"
    display_name: str  # e.g. "Panda Construction"
    business_key: Optional[str] = None  # matches Twenty workspace + AGENT_CATALOG group

    # S0
    icp: ICP

    # S1
    signal_sources: List[SignalSource] = Field(min_length=1)

    # S3 (scoring service will read this; item 3 PR wires it)
    fit_weights: FitWeights

    # S5
    channel_roster: List[ChannelName] = Field(min_length=1)
    compliance_profile: ComplianceProfile

    # Per-channel adapter blocks. Present only for channels this brand actually
    # uses; runner reads the block that matches channel_roster[0].
    instantly: Optional[InstantlyConfig] = None

    # Content per ICP segment (the ICP_CONFIGS map from pp_build_icp_campaign.py)
    icp_content: Dict[str, ICPContent] = Field(default_factory=dict)

    # Common tokens
    cta_url: Optional[str] = None
    leads_dir: Optional[str] = None  # relative to $HOME or absolute

    # S7 (attribution)
    success_metric: str  # e.g. "cost_per_meeting" | "cost_per_job" | "cost_per_signed_policy"

    # Forward-compat extension points (item 3-4 fill these; runner reads with
    # safe defaults today)
    scoring_notes: Optional[str] = None  # human notes for item 3 scoring service
    inbound_webhook_notes: Optional[str] = None  # human notes for item 4 unified webhook

    @field_validator("instantly")
    @classmethod
    def _instantly_required_if_cold_email(cls, v, info):
        """If cold_email is in channel_roster, an InstantlyConfig block must be present."""
        roster = info.data.get("channel_roster", [])
        if "cold_email" in roster and v is None:
            raise ValueError("channel_roster includes cold_email but no 'instantly' block is set")
        return v

    @field_validator("compliance_profile")
    @classmethod
    def _compliance_covers_roster(cls, v: ComplianceProfile, info):
        """Every channel in channel_roster must appear in compliance_profile.channels
        (the hard gate). A missing channel is a suppression failure waiting to happen."""
        roster = info.data.get("channel_roster", [])
        missing = [c for c in roster if c not in v.channels]
        if missing:
            raise ValueError(
                f"compliance_profile.channels missing: {missing} "
                f"(every channel in channel_roster must have compliance rules)"
            )
        return v


__all__ = [
    "BrandConfig",
    "ChannelName",
    "ComplianceProfile",
    "EmailStep",
    "FitWeights",
    "GeoRadius",
    "ICP",
    "ICPContent",
    "InstantlyConfig",
    "SignalSource",
    "SignalTier",
]
