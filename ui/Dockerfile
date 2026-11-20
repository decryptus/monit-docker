FROM nginx:1.28-alpine

LABEL org.opencontainers.image.title="monit-docker-ui" \
      org.opencontainers.image.source="https://github.com/decryptus/monit-docker" \
      org.opencontainers.image.description="Optional lightweight monit-docker interface with an authenticated TLS proxy"

COPY static/ /usr/share/nginx/html/
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/proxy.conf /etc/nginx/monit-proxy.conf
COPY nginx/audit.conf /etc/nginx/monit-audit.conf

COPY nginx/ai.conf /etc/nginx/monit-ai.conf

COPY nginx/sqlite-proxy.conf /etc/nginx/monit-sqlite-proxy.conf

EXPOSE 443
