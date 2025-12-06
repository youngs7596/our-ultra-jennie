#!/bin/sh
set -e

# secrets.json에서 토큰 읽기
TOKEN=$(jq -r '.["cloudflare-tunnel-token"]' /config/secrets.json)

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "❌ cloudflare-tunnel-token not found in secrets.json"
    exit 1
fi

echo "✅ Cloudflare Tunnel 시작..."
exec cloudflared tunnel --no-autoupdate run --token "$TOKEN"

