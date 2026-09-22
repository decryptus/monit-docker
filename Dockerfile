FROM alpine:latest

LABEL maintainer="docker@doowan.net"

RUN apk -Uuv add bash \
                 cargo \
                 curl-dev \
                 gcc \
                 libffi-dev \
                 musl-dev \
                 python3 \
                 python3-dev \
                 py3-pip && \
    find /var/cache/apk/ -type f -delete

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python3 -m venv "${VIRTUAL_ENV}"

WORKDIR /opt/monit-docker-src
COPY requirements.txt setup.py setup.yml README.md ./
COPY bin/ ./bin/
# setup.py imports yaml while preparing package metadata.
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-build-isolation .

ADD docker-run.sh /run.sh

CMD ["/run.sh"]
