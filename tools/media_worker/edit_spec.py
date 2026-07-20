"""Map a per-take edit.json onto scripts/cut_talking_head.py argv. Pure; no I/O.

edit.json faithfully captures the decisions an operator used to type as CLI flags.
No new editorial intelligence lives here (charter: port what exists)."""
from __future__ import annotations
from typing import List, Optional


def build_cut_argv(edit: dict, take: str, words: str, out: str,
                   script: str = "scripts/cut_talking_head.py") -> List[str]:
    brand = edit.get("brand")
    if not brand:
        raise ValueError("edit.json missing required 'brand'")
    argv: List[str] = ["python3", script, brand, take, words, out]
    hook: Optional[str] = edit.get("hook")
    if hook:
        argv += ["--hook", hook]
    for span in edit.get("cuts", []) or []:
        argv += ["--cut", span]
    corrections = edit.get("corrections")
    if corrections:
        argv += ["--corrections", corrections]
    for spec in edit.get("broll_at", []) or []:
        argv += ["--broll-at", spec]
    return argv
