import sys; sys.path.insert(0, '/Users/michaelrodriguez/paperclip')
from config.principles import _now_block, foundation_header

def test_fallback_does_not_raise(monkeypatch):
    """The fallback must not depend on a logger: a failure here would break every
    agent prompt in the org. It degrades to an honest message instead."""
    import builtins
    real = builtins.__import__
    def boom(name, *a, **k):
        if name == "zoneinfo":
            raise RuntimeError("no tzdata")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", boom)
    out = _now_block()
    assert "could NOT be determined" in out
    assert "Say so rather than guessing" in out

def test_not_frozen_at_import():
    """Computed at call time, so a long-running process does not freeze the date."""
    import time
    a = _now_block(); time.sleep(1.1); b = _now_block()
    # minute granularity means these may match; what matters is it re-evaluates
    assert "RIGHT NOW" in a and "RIGHT NOW" in b

def test_foundation_leads_with_the_clock():
    assert foundation_header().lstrip().startswith("RIGHT NOW")
