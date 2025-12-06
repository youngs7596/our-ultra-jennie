# Cloudflare Tunnel with secrets.json support
FROM alpine:3.19

# 의존성 및 cloudflared 설치
RUN apk add --no-cache jq curl ca-certificates && \
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared

# secrets.json에서 토큰 읽어서 실행
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

