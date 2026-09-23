FROM nginx:1.28-alpine

LABEL org.opencontainers.image.title="monit-docker-ui" \
      org.opencontainers.image.source="https://github.com/decryptus/monit-docker" \
      org.opencontainers.image.description="Optional lightweight monit-docker interface with an authenticated TLS proxy"

COPY static/ /usr/share/nginx/html/
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/proxy.conf /etc/nginx/monit-proxy.conf

EXPOSE 443
