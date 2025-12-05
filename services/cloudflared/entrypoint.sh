#!/bin/sh
set -eu

SECRET_FILE=${SECRET_FILE:-/app/config/secrets.json}

if [ ! -f "$SECRET_FILE" ]; then
  echo "❌ secrets.json가 $SECRET_FILE 경로에 존재하지 않습니다."
  exit 1
fi

TOKEN=$(python3 -c 'import json,os,sys; path=os.environ.get("SECRET_FILE","/app/config/secrets.json"); data=json.load(open(path)); print(data.get("cloudflare-tunnel-token",""))' 2>/dev/null || true)

if [ -z "$TOKEN" ]; then
  echo "❌ secrets.json에 cloudflare-tunnel-token 키가 없습니다."
  exit 1
fi

echo "✅ Cloudflare tunnel token 로드 완료. Tunnel 시작..."
exec cloudflared tunnel --no-autoupdate run --token "$TOKEN"

