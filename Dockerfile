# Pinned to the Alpine image used by the agent's last successful build.
FROM alpine@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS runtime-base

RUN apk add --no-cache bash ca-certificates libcurl libffi libmagic libstdc++ python3

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

FROM runtime-base AS build

RUN apk add --no-cache cargo curl-dev gcc libffi-dev musl-dev python3-dev py3-pip
RUN python3 -m venv "${VIRTUAL_ENV}"

WORKDIR /opt/monit-docker-src
COPY requirements.txt setup.py setup.yml pyproject.toml README.md ./
COPY bin/ ./bin/
COPY monit_docker/ ./monit_docker/
# setup.py imports yaml while preparing package metadata.
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-build-isolation .

FROM runtime-base

LABEL maintainer="docker@doowan.net" \
      org.opencontainers.image.title="monit-docker" \
      org.opencontainers.image.source="https://github.com/decryptus/monit-docker"

COPY --from=build /opt/venv /opt/venv
# Alpine's ctypes resolves crypt through libc.so. musl exports crypt itself,
# but the unversioned libc.so link is normally supplied by musl-dev.
RUN mkdir -p /usr/local/lib && \
    ln -s /lib/ld-musl-*.so.1 /usr/local/lib/libc.so && \
    python -c 'from legacycrypt import crypt; assert crypt("test", "$6$salt$").startswith("$6$salt$")'

COPY docker-run.sh /run.sh

CMD ["/run.sh"]
