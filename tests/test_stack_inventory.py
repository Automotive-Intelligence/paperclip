"""Stack inventory: reads the live system, and never clobbers human decisions."""
from unittest import mock

from services import stack_inventory as SI


class _Job:
    def __init__(self, jid, name, trigger):
        self.id, self.name, self.trigger = jid, name, trigger


class _Sched:
    def get_jobs(self):
        return [_Job("lead_canary_hourly", "AIPG Funnel Canary", "cron[minute='23']"),
                _Job("sonar_inbox_hourly", "Sonar Inbox", "cron[minute='7']")]


class _Route:
    def __init__(self, path):
        self.path = path


class _App:
    routes = [_Route("/bookd/mcp"), _Route("/bookd/agent/message"),
              _Route("/admin/partner-keys"), _Route("/lead/ingest")]


def test_render_reads_the_live_scheduler_and_routes():
    out = SI.render(_App(), _Sched(), "")
    assert "lead_canary_hourly" in out and "sonar_inbox_hourly" in out
    assert "Scheduled jobs live now:** 2" in out
    assert "`/bookd` (2 routes)" in out          # grouped by top-level prefix


def test_curated_section_survives_regeneration():
    human = (SI._CURATED_MARKER + "\n\n## Decided against\n\n"
             "| Thing | Verdict |\n|---|---|\n| Something Michael decided | KILLED |\n")
    existing = "# old generated junk\nstale facts here\n" + human
    out = SI.render(_App(), _Sched(), existing)
    assert "Something Michael decided" in out       # human decision preserved verbatim
    assert "stale generated junk" not in out        # generated half replaced
    assert "stale facts here" not in out


def test_default_curated_used_when_file_is_new():
    out = SI.render(_App(), _Sched(), "")
    assert SI._CURATED_MARKER in out
    assert "Inbound Postal/Gmail rail" in out       # seeded with today's decisions
    assert "paperclipai" in out


def test_build_skips_commit_when_only_the_timestamp_changed(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "t")
    captured = {}

    def _fake_update(path, transform, message, token):
        # identical content except for the timestamp VALUE -> transform must skip.
        import re as _re
        prior = _re.sub(r"\*\*Generated:\*\*.*", "**Generated:** 1999-01-01 00:00 CST",
                        SI.render(_App(), _Sched(), ""), count=1)
        captured["result"] = transform(prior)
        return {"committed": False, "skipped": True}

    monkeypatch.setattr("services.avo_state_commit.update_state", _fake_update)
    out = SI.build(_App(), _Sched(), commit=True)
    assert out["ok"] is True and out["skipped"] is True
    assert captured["result"] is None               # idempotent: no noise commit


def test_build_without_token_fails_closed(monkeypatch):
    for v in ("GITHUB_TOKEN", "GH_TOKEN", "SLIPSTREAM_GH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    out = SI.build(_App(), _Sched(), commit=True)
    assert out["ok"] is False and "token" in out["error"]


def test_preview_mode_does_not_commit(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "t")
    out = SI.build(_App(), _Sched(), commit=False)
    assert out["committed"] is False and "preview" in out
