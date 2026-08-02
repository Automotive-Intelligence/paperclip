"""Tests for services/cmo_override — reply-to-CMO-Daily -> CMO OVERRIDE flag."""

import services.cmo_override as ov


# ---- parsing ----

def test_strip_quoted_removes_history_and_signature():
    body = (
        "Hold the AvI post, wrong stat.\n"
        "\n"
        "On Sun, Aug 2, 2026 at 7:00 AM Michael's CMO <cmo@x> wrote:\n"
        "> CMO Daily ...\n"
        "> more quoted\n"
    )
    assert ov._strip_quoted(body) == "Hold the AvI post, wrong stat."


def test_is_inbound_distinguishes_our_send(monkeypatch):
    monkeypatch.delenv("CMO_DAILY_FROM", raising=False)
    # our Resend sending subdomain -> not inbound
    assert ov._is_inbound("Michael's CMO <cmo@mail.automotiveintelligence.io>") is False
    # Michael replying from either of his real inboxes -> inbound
    assert ov._is_inbound("Michael <michael@worshipdigital.co>") is True
    assert ov._is_inbound("Michael <michael@automotiveintelligence.io>") is True


def test_latest_inbound_picks_newest_typed_reply():
    msgs = [
        {"id": "1", "from": "cmo@mail.automotiveintelligence.io", "body": "the brief"},
        {"id": "2", "from": "michael@worshipdigital.co",
         "body": "Pull the BAE post.\nOn ... wrote:\n> brief"},
    ]
    hit = ov._latest_inbound(msgs)
    assert hit["id"] == "2"
    assert hit["typed"] == "Pull the BAE post."


def test_latest_inbound_ignores_only_our_copy():
    msgs = [{"id": "1", "from": "cmo@mail.automotiveintelligence.io", "body": "brief"}]
    assert ov._latest_inbound(msgs) is None


# ---- flag construction + idempotent transform ----

def test_build_override_block_has_flag_verbatim_and_marker():
    msg = {"id": "MID1", "from": "Michael <michael@worshipdigital.co>",
           "subject": "Re: CMO Daily — 2026-08-02", "date": "Sun, 2 Aug 2026",
           "typed": "Hold the AvI post."}
    block = ov.build_override_block(msg, now_iso="2026-08-02T13:00:00Z")
    assert "🏁 FLAG FOR: CMO" in block
    assert "CMO OVERRIDE" in block
    assert "> Hold the AvI post." in block
    assert "cmo-override:msgid=MID1" in block


def test_transform_inserts_into_flags_section():
    content = "# CMO State\n\n## Flags for other chats\n\nexisting\n\n## Recently closed\n"
    new = ov._make_transform("BLOCK\n", "MID9")(content)
    assert new is not None
    assert "## Flags for other chats" in new
    # block lands inside the flags section, above prior content
    flags_idx = new.index("## Flags for other chats")
    assert new.index("BLOCK", flags_idx) < new.index("existing")
    assert new.index("BLOCK") < new.index("## Recently closed")


def test_transform_idempotent_when_marker_present():
    content = "## Flags for other chats\n\n<!-- cmo-override:msgid=MID9 -->\n"
    block = "x " + ov._msg_marker("MID9")
    assert ov._make_transform(block, "MID9")(content) is None


def test_transform_creates_section_if_missing():
    new = ov._make_transform("BLOCK\n", "MID2")("# CMO State\nbody\n")
    assert "## Flags for other chats" in new
    assert "BLOCK" in new


# ---- run(): fail-closed + happy path (all seams mocked) ----

def test_run_fail_closed_when_inbox_unavailable(monkeypatch):
    from services import postal_inbox

    def _boom(account, query, limit=25):
        raise RuntimeError("no active postal token for account 'avi'")
    monkeypatch.setattr(postal_inbox, "search", _boom)

    called = {"update": 0}
    import services.avo_state_commit as asc
    monkeypatch.setattr(asc, "update_state",
                        lambda *a, **k: called.__setitem__("update", called["update"] + 1))

    result = ov.run()
    assert result["status"] == "inbox_unavailable"
    assert result["filed"] == 0
    assert called["update"] == 0  # wrote NOTHING


def test_run_files_override_flag(monkeypatch):
    from services import postal_inbox
    import services.avo_state_commit as asc

    monkeypatch.setenv("GITHUB_TOKEN_TELEMETRY", "tok")
    monkeypatch.setattr(postal_inbox, "search",
                        lambda a, q, limit=25: [{"id": "T1"}])
    monkeypatch.setattr(postal_inbox, "read_thread", lambda a, tid: {"messages": [
        {"id": "M1", "from": "michael@worshipdigital.co",
         "subject": "Re: CMO Daily", "date": "d", "body": "Pull the BAE post.",
         "label_ids": []},
    ]})

    writes = {}
    def _fake_update(path, transform, message, token):
        # exercise the transform against a minimal cmo_state.md
        new = transform("## Flags for other chats\n\n")
        writes["path"] = path
        writes["new"] = new
        return {"committed": True}
    monkeypatch.setattr(asc, "update_state", _fake_update)
    # label path is best-effort; force it to no-op cleanly
    import tools.gmail_multi as gm
    monkeypatch.setattr(gm, "ensure_label", lambda a, n: "LBL")
    monkeypatch.setattr(gm, "add_label", lambda a, t, l: {})

    result = ov.run()
    assert result["status"] == "ok"
    assert result["filed"] == 1
    assert writes["path"] == "cmo_state.md"
    assert "CMO OVERRIDE" in writes["new"]
    assert "> Pull the BAE post." in writes["new"]
