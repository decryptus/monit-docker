FROM alpine:latest

LABEL maintainer="docker@doowan.net"

RUN apk -Uuv add bash \
                 gcc \
                 libffi-dev \
                 libressl-dev \
                 musl-dev \
                 python \
                 python-dev \
                 py-pip && \
    find /var/cache/apk/ -type f -delete

RUN pip install monit-docker

ADD docker-run.sh /run.sh

CMD ["/run.sh"]
