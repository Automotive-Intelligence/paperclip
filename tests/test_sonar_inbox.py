"""Sonar inbox monitor: exclusion, dedup, cheap-exit, fail-closed classify + ledger."""
from unittest import mock

from services import sonar_inbox as SI


def _items(*rows):
    # rows: (id, kind, account) -> raw Zernio comment/mention shape
    return [{"id": i, "content": f"text {i}", "accountUsername": a, "platform": "instagram",
             "permalink": f"http://x/{i}"} for (i, a) in rows]


def test_pull_new_excludes_bookd_and_pp_and_dedups():
    def fake_pull(ep, params):
        if "mentions" in ep:
            return _items(("m1", "agent_empire"))
        return _items(("c1", "theaiphoneguy"), ("c2", "bookdcx"),
                      ("c3", "paperandpurpose"), ("c4", "theaiphoneguy"))
    with mock.patch.object(SI, "_pull", side_effect=fake_pull):
        new = SI.pull_new(handled={"c4"})
    ids = {n["id"] for n in new}
    assert "c1" in ids and "m1" in ids          # owned comment + mention kept
    assert "c2" not in ids and "c3" not in ids   # Book'd + P&P excluded
    assert "c4" not in ids                        # already handled


def test_run_sweep_cheap_exit_when_nothing_new():
    with mock.patch.object(SI, "handled_ids", return_value=set()), \
         mock.patch.object(SI, "pull_new", return_value=[]):
        r = SI.run_sweep()
    assert r["new"] == 0 and "nothing new" in r["note"]


def test_run_sweep_no_classifier_fails_closed_to_escalation():
    new = [{"id": "c1", "kind": "comment", "account": "theaiphoneguy",
            "platform": "instagram", "text": "hi", "url": "http://x/c1"}]
    marked = []
    with mock.patch.object(SI, "handled_ids", return_value={"seed"}), \
         mock.patch.object(SI, "pull_new", return_value=new), \
         mock.patch.object(SI, "_classify", return_value={"tier": "escalate"}), \
         mock.patch.object(SI, "_mark_handled", side_effect=lambda it: marked.append(it["id"])), \
         mock.patch.object(SI, "_escalate_email", return_value=True) as esc:
        r = SI.run_sweep()
    assert r["escalations"] == 1 and r["escalation_emailed"] is True
    assert esc.called and marked == ["c1"]       # escalated AND marked handled (never re-process)


def test_run_sweep_routes_classifier_tiers():
    new = [{"id": "a", "kind": "comment", "account": "wd", "platform": "facebook", "text": "great", "url": ""},
           {"id": "b", "kind": "comment", "account": "aipg", "platform": "instagram", "text": "price?", "url": ""},
           {"id": "c", "kind": "mention", "account": "avi", "platform": "instagram", "text": "spam", "url": ""}]
    tiers = {"a": {"tier": "auto"}, "b": {"tier": "lead"}, "c": {"tier": "escalate"}}
    with mock.patch.object(SI, "handled_ids", return_value={"seed"}), \
         mock.patch.object(SI, "pull_new", return_value=new), \
         mock.patch.object(SI, "_classify", side_effect=lambda it: tiers[it["id"]]), \
         mock.patch.object(SI, "_mark_handled"), \
         mock.patch.object(SI, "_escalate_email", return_value=True):
        r = SI.run_sweep()
    assert r["auto"] == 1 and r["leads"] == 1 and r["escalations"] == 1


def test_ledger_read_failure_fails_closed_processes_nothing():
    # handled_ids returns the _AllHandled sentinel -> pull_new sees every id as handled.
    with mock.patch.object(SI, "execute_query", side_effect=RuntimeError("db down")), \
         mock.patch.object(SI, "fetch_all", side_effect=RuntimeError("db down")):
        h = SI.handled_ids()
    assert "literally-anything" in h              # sentinel: everything reads as handled


def test_first_run_seeds_backlog_without_escalating_or_llm():
    new = [{"id": "c1", "kind": "comment", "account": "aipg", "platform": "instagram", "text": "x", "url": ""},
           {"id": "c2", "kind": "comment", "account": "wd", "platform": "facebook", "text": "y", "url": ""}]
    marked = []
    with mock.patch.object(SI, "handled_ids", return_value=set()), \
         mock.patch.object(SI, "pull_new", return_value=new), \
         mock.patch.object(SI, "_mark_handled", side_effect=lambda it: marked.append(it["id"])), \
         mock.patch.object(SI, "_escalate_email") as esc, \
         mock.patch.object(SI, "_classify") as clf:
        r = SI.run_sweep()
    assert r["seeded"] == 2
    assert not esc.called and not clf.called      # seed: no escalation blast, no LLM
    assert set(marked) == {"c1", "c2"}


def test_excludes_ryan_and_velazquez():
    assert SI._excluded("Ryan Velazquez") and SI._excluded("bookdcx")
    assert not SI._excluded("theaiphoneguy") and not SI._excluded("Michael Rodriguez")
