from datetime import date
from unittest import mock

from services import studio_script_engine as se


def _video(**over):
    v = {
        "brand": "avi", "stage": "awareness", "title": "The empty CRM field nobody owns",
        "seconds": 60, "format": "on camera", "pillar": "orchestration", "cta": "follow",
        "destination": "https://automotiveintelligence.io",
        "hook": "Your store already has dashboards and none of them made a decision today.",
        "meat": ("A dashboard tells you what already happened. An intelligence layer "
                 "connects sales, service, and the website and tells you what to do next. "
                 "Which lead is heating up. Which customer is about to defect. Which deal "
                 "died between two systems that never talked. That gap is where the month "
                 "quietly goes missing, and nobody owns it because every screen looks fine."),
        "climax": "One of them creates work. The other one creates decisions.",
        "comment_ask": "What is the one report you actually open every morning? Tell me below.",
        "follow_ask": "For more like this, follow Automotive Intelligence.",
        "onscreen_hook": "Your dashboards are a rearview mirror",
        "broll": ["showroom push-in", "CRM over shoulder"],
        "captions": {"tiktok": "Dashboards are rearview mirrors. #automotive #BDC",
                     "facebook": "The difference is decisions. https://automotiveintelligence.io"},
    }
    v.update(over)
    return v


# ---------------------------------------------------------------- dates
def test_next_tuesday_from_sunday_is_two_days():
    assert se.next_tuesday(date(2026, 8, 9)) == date(2026, 8, 11)


def test_next_tuesday_on_tuesday_rolls_a_week():
    assert se.next_tuesday(date(2026, 8, 11)) == date(2026, 8, 18)


def test_next_tuesday_midweek():
    assert se.next_tuesday(date(2026, 8, 12)) == date(2026, 8, 18)


# ---------------------------------------------------------------- gate
def test_gate_passes_clean_video():
    assert se.gate_videos([_video()], ["missed call math"]) == []


def test_gate_catches_em_dash():
    v = _video(meat=_video()["meat"] + " and — this breaks")
    assert any("em-dash" in x for x in se.gate_videos([v], []))


def test_gate_catches_missing_part():
    v = _video(comment_ask="")
    assert any("COMMENT_ASK" in x for x in se.gate_videos([v], []))


def test_gate_catches_dollar_figure():
    v = _video(captions={"facebook": "Only $99 to start https://x.co"})
    assert any("dollar" in x for x in se.gate_videos([v], []))


def test_gate_blocks_unapproved_stat_allows_invoca_3pct():
    bad = _video(meat=_video()["meat"] + " 47% of stores fail.")
    assert any("unapproved stat" in x for x in se.gate_videos([bad], []))
    ok = _video(meat=_video()["meat"] + " Fewer than 3% of callers leave a voicemail.")
    assert not any("unapproved stat" in x for x in se.gate_videos([ok], []))


def test_gate_catches_banned_hashtag():
    v = _video(captions={"tiktok": "Watch this #fyp"})
    assert any("banned hashtag" in x for x in se.gate_videos([v], []))


def test_gate_catches_bookd_compliance_language():
    v = _video(brand="bookd", meat=_video()["meat"] + " Agents earn more with this.")
    assert any("compliance language" in x for x in se.gate_videos([v], []))


def test_gate_catches_title_overlap_with_shot_library():
    v = _video(title="what one missed call actually costs")
    out = se.gate_videos([v], ["missed call actually costs what one"])
    assert any("overlaps existing" in x for x in out)


# ---------------------------------------------------------------- run paths
def test_run_emails_existing_sheet_without_generating(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    sent = {}
    with mock.patch.object(se, "fetch_repo_file", return_value="# SHOOT sheet body"), \
         mock.patch.object(se, "send_scripts_email",
                           side_effect=lambda s, b, **k: sent.update(subject=s, body=b) or True), \
         mock.patch.object(se, "generate_videos") as gen:
        r = se.run(dry_run=True, today=date(2026, 8, 9))
    assert r["ok"] and r["source"] == "existing_sheet"
    assert "SHOOT sheet body" in sent["body"]
    gen.assert_not_called()          # human sheets always win


def test_run_generates_gates_and_emails(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    sent = {}
    with mock.patch.object(se, "fetch_repo_file", return_value=None), \
         mock.patch.object(se, "list_existing_titles", return_value=["old angle"]), \
         mock.patch.object(se, "generate_videos", return_value=[_video()]), \
         mock.patch.object(se, "send_scripts_email",
                           side_effect=lambda s, b, **k: sent.update(subject=s, body=b) or True):
        r = se.run(dry_run=True, today=date(2026, 8, 9))
    assert r["ok"] and r["source"] == "generated" and r["videos"] == 1
    assert "[HOOK]" in sent["body"] and "Post kit" in sent["body"]


def test_run_gate_failure_twice_sends_loud_failure_email(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    bad = _video(comment_ask="")     # fails the gate every attempt
    sent = {}
    with mock.patch.object(se, "fetch_repo_file", return_value=None), \
         mock.patch.object(se, "list_existing_titles", return_value=[]), \
         mock.patch.object(se, "generate_videos", return_value=[bad]), \
         mock.patch.object(se, "send_scripts_email",
                           side_effect=lambda s, b, **k: sent.update(subject=s) or True):
        r = se.run(dry_run=True, today=date(2026, 8, 9))
    assert r["ok"] is False and "gate failed twice" in r["error"]
    assert r["failure_emailed"] and "FAILED" in sent["subject"]


def test_run_email_failure_opens_issue(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    issues = []
    with mock.patch.object(se, "fetch_repo_file", return_value="# sheet"), \
         mock.patch.object(se, "send_scripts_email", return_value=False), \
         mock.patch.object(se, "_alert_issue",
                           side_effect=lambda t, b, tok: issues.append(t) or True):
        r = se.run(dry_run=True, today=date(2026, 8, 9))
    assert r["ok"] is False and issues and "email FAILED" in issues[0]


# ---------------------------------------------------------------- render
def test_render_sheet_carries_all_parts_and_no_em_dash():
    md = se.render_sheet([_video()], date(2026, 8, 11), ["a", "b"])
    for marker in ("[HOOK]", "[MEAT]", "[CLIMAX]", "[COMMENT ASK]", "[FOLLOW ASK]",
                   "On-screen hook", "Post kit"):
        assert marker in md
    assert "—" not in md
