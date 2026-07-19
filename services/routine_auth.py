"""services/routine_auth.py -- scoped bearer for the web-based Slipstream routines.

The claude.ai blog routines cannot hold paperclip's master API_KEYS, and giving
them a full-admin key violates least privilege (a content routine should not be
able to hit every /admin endpoint). Instead the routine-facing endpoints
(blog-image, zernio-publish) ALSO accept a dedicated SLIPSTREAM_ROUTINE_TOKEN.
It grants nothing anywhere else. A blank token never authorizes.
"""
from __future__ import annotations

from typing import Optional


def routine_token_valid(authorization: Optional[str], expected_token: Optional[str]) -> bool:
    """True iff `authorization` is 'Bearer <token>' and token == expected_token,
    where expected_token is non-empty. Blank/None expected never grants."""
    expected = (expected_token or "").strip()
    if not expected:
        return False
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split("Bearer ", 1)[1].strip()
    return bool(token) and token == expected
