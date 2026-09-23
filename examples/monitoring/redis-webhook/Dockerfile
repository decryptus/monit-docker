FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY examples/monitoring/redis-webhook/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY examples/monitoring/redis-webhook/bridge.py ./
COPY monit_docker ./monit_docker
RUN mkdir -p /var/lib/monit-docker && chown 65534:65534 /var/lib/monit-docker
ENV MONIT_DOCKER_AUDIT_FILE=/var/lib/monit-docker/redis-events.jsonl
USER 65534:65534
CMD ["python", "bridge.py"]
