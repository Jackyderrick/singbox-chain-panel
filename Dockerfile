FROM python:3.11-slim

ARG SING_BOX_VERSION=1.14.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl iproute2 tar xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) sb_arch="amd64" ;; \
      arm64) sb_arch="arm64" ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}.tar.gz"; \
    tar -xzf /tmp/sing-box.tar.gz -C /tmp; \
    mv "/tmp/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}/sing-box" /usr/local/bin/sing-box; \
    chmod +x /usr/local/bin/sing-box; \
    rm -rf /tmp/sing-box*

WORKDIR /app
COPY panel.py /app/panel.py

ENV PANEL_HOST=0.0.0.0 \
    PANEL_PORT=8080 \
    SINGBOX_MANAGE_MODE=process \
    SINGBOX_BIN=/usr/local/bin/sing-box \
    SINGBOX_LOG_PATH=/opt/singbox-panel/sing-box.log

EXPOSE 8080 443

CMD ["python3", "/app/panel.py"]
