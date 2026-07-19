"""services/social_load_service.py -- endpoint wrapper around tools/social_load.

The web-based Slipstream routine builds its social pack as a jobs list and POSTs
it here (it cannot hold ZERNIO_API_KEY; paperclip does). This converts the JSON
jobs to PostJob, runs the ONE loader (UTMs + queue-guard + registry + Zernio
scheduling), and returns a JSON-serializable summary. Never raises to the route.
"""
from __future__ import annotations

from typing import Any, Dict, List

from tools.social_load import PostJob, load_jobs


def run_social_load(
    jobs: List[Dict[str, Any]],
    *,
    commit: bool = True,
    allow_stack: bool = False,
) -> Dict[str, Any]:
    if not isinstance(jobs, list) or not jobs:
        return {"ok": False, "error": "jobs must be a non-empty list", "results": []}

    fields = PostJob.__dataclass_fields__
    try:
        post_jobs = [PostJob(**{k: v for k, v in j.items() if k in fields}) for j in jobs]
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": f"invalid job: {e}", "results": []}

    try:
        results = load_jobs(post_jobs, commit=commit, allow_stack=allow_stack)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "results": []}

    serialized: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for r in results:
        job = r.get("job")
        action = r.get("action", "unknown")
        counts[action] = counts.get(action, 0) + 1
        serialized.append({
            "brand": getattr(job, "brand", ""),
            "platform": getattr(job, "platform", ""),
            "action": action,
            "detail": r.get("detail"),
        })

    ok = counts.get("error", 0) == 0 and counts.get("conflict", 0) == 0
    return {"ok": ok, "results": serialized, "counts": counts}
