FROM nginx:1.29-alpine

ARG UI_VERSION

LABEL org.opencontainers.image.title="monit-docker-ui" \
      org.opencontainers.image.source="https://github.com/decryptus/monit-docker" \
      org.opencontainers.image.description="Optional lightweight monit-docker interface with an authenticated TLS proxy"

COPY static/ /usr/share/nginx/html/
RUN printf '%s' "$UI_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
    && sed -i "s/name=\"monit-ui-version\" content=\"\"/name=\"monit-ui-version\" content=\"$UI_VERSION\"/" /usr/share/nginx/html/index.html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/proxy.conf /etc/nginx/monit-proxy.conf
COPY nginx/audit.conf /etc/nginx/monit-audit.conf

COPY nginx/ai.conf /etc/nginx/monit-ai.conf

COPY nginx/sqlite-proxy.conf /etc/nginx/monit-sqlite-proxy.conf

EXPOSE 443
