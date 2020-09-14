FROM alpine:latest

LABEL maintainer="docker@doowan.net"

RUN apk -Uuv add bash \
                 curl-dev \
                 gcc \
                 libffi-dev \
                 libressl-dev \
                 musl-dev \
                 python3 \
                 python3-dev \
                 py3-pip && \
    find /var/cache/apk/ -type f -delete

RUN pip3 install monit-docker

ADD docker-run.sh /run.sh

CMD ["/run.sh"]
