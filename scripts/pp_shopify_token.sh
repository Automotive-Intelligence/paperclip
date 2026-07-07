#!/bin/zsh
# Mint a fresh 24h Shopify Admin API token for Paper & Purpose
# via OAuth client_credentials grant.
# Usage: TOKEN=$(/Users/michaelrodriguez/paperclip/scripts/pp_shopify_token.sh)
set -e
ENV_FILE="${PP_ENV:-/Users/michaelrodriguez/paperclip/.env}"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
SHOP="${SHOPIFY_SHOP_PAPERANDPURPOSE:?missing SHOPIFY_SHOP_PAPERANDPURPOSE}"
CID="${SHOPIFY_CLIENT_ID_PAPERANDPURPOSE:?missing client id}"
CSEC="${SHOPIFY_CLIENT_SECRET_PAPERANDPURPOSE:?missing client secret}"
resp=$(curl -fsS -X POST "https://${SHOP}.myshopify.com/admin/oauth/access_token" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"${CID}\",\"client_secret\":\"${CSEC}\",\"grant_type\":\"client_credentials\"}")
echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
