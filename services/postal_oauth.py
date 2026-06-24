"""Postal Agent OAuth — per-account Google OAuth flow + encrypted token storage.

Wired to FastAPI routes in app.py:
    @app.get("/oauth/google/start")
    @app.get("/oauth/google/callback")

Plan: ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md (Phase 1)

Env vars required (Doppler paperclip/prd):
    POSTAL_GOOGLE_CLIENT_ID         — OAuth 2.0 Web Client ID from Google Cloud
    POSTAL_GOOGLE_CLIENT_SECRET     — OAuth 2.0 Web Client Secret
    POSTAL_OAUTH_REDIRECT_URI       — https://paperclip-production-ba14.up.railway.app/oauth/google/callback
    APP_SECRET                      — already set; used to derive Fernet encryption key

Scopes requested (v1 read-only + label-modify; SEND deferred to v1.5):
    https://www.googleapis.com/auth/gmail.readonly
    https://www.googleapis.com/auth/gmail.modify   (needed for label apply + mark-as-read + archive)
    https://www.googleapis.com/auth/gmail.labels

Iron rules:
- Never log refresh tokens
- Always encrypt at rest with Fernet (key derived from APP_SECRET)
- Validate state token to prevent CSRF
- One row per account_label in postal_tokens
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]

VALID_ACCOUNT_LABELS = {"avi", "wd", "salesdroid", "aipg", "agentempire", "bookd"}


# ---------- Fernet encryption helpers ----------

def _fernet() -> Fernet:
    """Derive a Fernet key deterministically from APP_SECRET."""
    secret = os.environ.get("APP_SECRET", "")
    if not secret:
        raise RuntimeError("APP_SECRET not set — cannot encrypt postal tokens")
    # Fernet keys must be 32 url-safe base64-encoded bytes. Derive from APP_SECRET via SHA256.
    digest = hashlib.sha256(secret.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as e:
        raise RuntimeError(f"postal token decryption failed: {e}") from e


# ---------- OAuth Flow factory ----------

def _build_flow(state: str | None = None) -> Flow:
    client_id = os.environ.get("POSTAL_GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("POSTAL_GOOGLE_CLIENT_SECRET")
    redirect_uri = os.environ.get("POSTAL_OAUTH_REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        raise HTTPException(
            status_code=500,
            detail="POSTAL_GOOGLE_CLIENT_ID / SECRET / REDIRECT_URI not configured",
        )
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=SCOPES,
        state=state,
        # Disable PKCE — newer google-auth-oauthlib auto-enables it, but PKCE
        # requires the same code_verifier on both auth + token exchange. Since
        # we rebuild Flow on the callback (no shared memory across requests),
        # PKCE would fail with "Missing code verifier." We're a confidential
        # Web client with a real client_secret, so classic OAuth is fine.
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    flow.code_verifier = None
    return flow


# ---------- State token (CSRF) ----------

def _issue_state(account_label: str) -> str:
    state = secrets.token_urlsafe(32)
    execute_query(
        "INSERT INTO postal_oauth_state (state_token, account_label) VALUES (%s, %s)",
        (state, account_label),
    )
    return state


def _consume_state(state: str) -> str:
    """Returns the account_label this state was issued for. Raises 400 on bad/expired."""
    rows = fetch_all(
        """
        DELETE FROM postal_oauth_state
        WHERE state_token = %s
          AND created_at > now() - interval '10 minutes'
        RETURNING account_label
        """,
        (state,),
    )
    if not rows:
        raise HTTPException(status_code=400, detail="invalid or expired state token")
    return rows[0][0]


# ---------- Public entry points (called from app.py routes) ----------

def start_oauth(account_label: str) -> RedirectResponse:
    """Redirect user to Google OAuth consent screen for the given account_label."""
    if account_label not in VALID_ACCOUNT_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown account_label '{account_label}'. valid: {sorted(VALID_ACCOUNT_LABELS)}",
        )
    state = _issue_state(account_label)
    flow = _build_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",            # force refresh_token issuance even on re-auth
        include_granted_scopes="true",
    )
    return RedirectResponse(auth_url, status_code=302)


def handle_callback(code: str | None, state: str | None, error: str | None) -> HTMLResponse:
    """Google redirects here after user consent. Exchange code → tokens → store."""
    if error:
        return HTMLResponse(
            f"<h1>OAuth error</h1><pre>{error}</pre><p>Try /oauth/google/start?account=&lt;label&gt; again.</p>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    account_label = _consume_state(state)

    flow = _build_flow(state=state)
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.exception("postal oauth fetch_token failed")
        raise HTTPException(status_code=502, detail=f"token exchange failed: {e}")

    creds = flow.credentials
    if not creds.refresh_token:
        # Force re-consent: Google withholds refresh_token on re-auth unless prompt=consent.
        # We set prompt=consent above, so this should not happen — but defend in depth.
        raise HTTPException(
            status_code=502,
            detail="Google did not return a refresh_token. Revoke access at https://myaccount.google.com/permissions and retry.",
        )

    # Get the authorized email address from id_token or userinfo endpoint
    email = _resolve_authorized_email(creds)
    if not email:
        raise HTTPException(status_code=502, detail="could not resolve authorized email")

    # Upsert encrypted refresh_token
    enc_refresh = encrypt_token(creds.refresh_token)
    granted_scopes = " ".join(creds.scopes or SCOPES)
    execute_query(
        """
        INSERT INTO postal_tokens (account_label, email, refresh_token, scopes, status, last_reauth_at, updated_at)
        VALUES (%s, %s, %s, %s, 'active', now(), now())
        ON CONFLICT (account_label) DO UPDATE
          SET email = EXCLUDED.email,
              refresh_token = EXCLUDED.refresh_token,
              scopes = EXCLUDED.scopes,
              status = 'active',
              last_reauth_at = now(),
              updated_at = now()
        """,
        (account_label, email, enc_refresh, granted_scopes),
    )

    # Ensure a postal_state row exists so the polling loop has a watermark to update
    execute_query(
        """
        INSERT INTO postal_state (account_label, last_synced_at, updated_at)
        VALUES (%s, now(), now())
        ON CONFLICT (account_label) DO NOTHING
        """,
        (account_label,),
    )

    logger.info(f"postal oauth: account_label={account_label} email={email} connected")

    return HTMLResponse(
        f"""
        <html>
          <head><title>Postal Agent — connected</title></head>
          <body style="font-family: system-ui; max-width: 640px; margin: 4em auto; line-height: 1.5;">
            <h1>✓ Connected</h1>
            <p><strong>Account:</strong> {account_label}<br><strong>Email:</strong> {email}</p>
            <p>The Postal Agent can now sync, classify, and route mail for this inbox.</p>
            <p style="color: #555;">
              To connect another account, visit
              <code>/oauth/google/start?account=&lt;label&gt;</code>
              with labels: avi, wd, salesdroid, aipg, agentempire, bookd
            </p>
          </body>
        </html>
        """
    )


def _resolve_authorized_email(creds: Any) -> str | None:
    """Hit Google's userinfo endpoint to get the email tied to the granted credentials."""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("email")
    except Exception:
        logger.exception("postal oauth: userinfo lookup failed")
        return None


# ---------- Helpers for downstream Postal Agent / gmail_multi.py ----------

def get_refresh_token(account_label: str) -> str:
    """Decrypt and return the refresh_token for an account. Used by gmail_multi.py."""
    rows = fetch_all(
        "SELECT refresh_token FROM postal_tokens WHERE account_label = %s AND status = 'active'",
        (account_label,),
    )
    if not rows:
        raise RuntimeError(f"no active postal token for account '{account_label}'")
    return decrypt_token(rows[0][0])


def list_connected_accounts() -> list[Dict[str, Any]]:
    """Returns [{account_label, email, status, last_reauth_at}] for all rows."""
    rows = fetch_all(
        "SELECT account_label, email, status, last_reauth_at FROM postal_tokens ORDER BY account_label",
        (),
    )
    return [
        {"account_label": r[0], "email": r[1], "status": r[2], "last_reauth_at": r[3]}
        for r in rows
    ]


def mark_needs_reauth(account_label: str, reason: str = "") -> None:
    """Called by gmail_multi.py when refresh fails (invalid_grant etc)."""
    execute_query(
        "UPDATE postal_tokens SET status = 'needs_reauth', updated_at = now() WHERE account_label = %s",
        (account_label,),
    )
    logger.warning(f"postal token marked needs_reauth: {account_label} reason={reason}")
