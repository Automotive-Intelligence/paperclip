"""Paper & Purpose — re-mint the Shopify Admin API token.

The store's Admin API access token (shpat_...) granted via the Client
Credentials Grant expires every 24h. This script re-mints it from the
stable client_id / client_secret and rewrites the
SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE line in .env in place.

Run this before any Admin API write work if the last run was >24h ago,
or whenever a call returns HTTP 401 "Shopify rejected the admin token".

Required env (in .env):
    SHOPIFY_SHOP_PAPERANDPURPOSE
    SHOPIFY_CLIENT_ID_PAPERANDPURPOSE
    SHOPIFY_CLIENT_SECRET_PAPERANDPURPOSE

Usage:
    python scripts/pp_refresh_shopify_token.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ENV_PATH)
except ImportError:
    pass

SUFFIX = "PAPERANDPURPOSE"
TOKEN_VAR = f"SHOPIFY_ADMIN_TOKEN_{SUFFIX}"


def grant_token(shop: str, client_id: str, client_secret: str) -> tuple[str, str, int]:
    """Client Credentials Grant. Returns (token, scope, expires_in)."""
    url = f"https://{shop}.myshopify.com/admin/oauth/access_token"
    r = requests.post(
        url,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"ERROR: grant failed HTTP {r.status_code}: {r.text[:400]}")
    body = r.json()
    return body["access_token"], body.get("scope", ""), body.get("expires_in", 0)


def rewrite_env_token(env_path: Path, token: str) -> None:
    """Replace the SHOPIFY_ADMIN_TOKEN_* line in .env, in place."""
    lines = env_path.read_text().splitlines()
    out = []
    replaced = False
    for line in lines:
        if line.startswith(f"{TOKEN_VAR}="):
            out.append(f"{TOKEN_VAR}={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{TOKEN_VAR}={token}")
    env_path.write_text("\n".join(out) + "\n")


def main() -> None:
    shop = (os.environ.get(f"SHOPIFY_SHOP_{SUFFIX}") or "").strip()
    client_id = (os.environ.get(f"SHOPIFY_CLIENT_ID_{SUFFIX}") or "").strip()
    client_secret = (os.environ.get(f"SHOPIFY_CLIENT_SECRET_{SUFFIX}") or "").strip()

    missing = [
        name for name, val in [
            (f"SHOPIFY_SHOP_{SUFFIX}", shop),
            (f"SHOPIFY_CLIENT_ID_{SUFFIX}", client_id),
            (f"SHOPIFY_CLIENT_SECRET_{SUFFIX}", client_secret),
        ] if not val
    ]
    if missing:
        raise SystemExit(f"ERROR: missing env vars: {', '.join(missing)}")

    token, scope, expires_in = grant_token(shop, client_id, client_secret)
    rewrite_env_token(ENV_PATH, token)

    print(f"Re-minted Shopify token for {shop}.myshopify.com")
    print(f"  token:      {token[:12]}... (len {len(token)})")
    print(f"  scope:      {scope}")
    print(f"  expires_in: {expires_in}s (~{expires_in // 3600}h)")
    print(f"  written to: {ENV_PATH} ({TOKEN_VAR})")


if __name__ == "__main__":
    main()
