#!/bin/bash

CRONTABS_PATH="/var/spool/cron/crontabs"
MONIT_DOCKER_ROOT="${MONIT_DOCKER_ROOT:-"/opt/monit-docker"}"
MONIT_DOCKER_CONFFILE="${MONIT_DOCKER_CONFFILE:-"${MONIT_DOCKER_ROOT}/monit-docker.yml"}"

mkdir -p "${MONIT_DOCKER_ROOT}"

WORKDIR "${MONIT_DOCKER_ROOT}"

if [[ ! -f "${MONIT_DOCKER_CONFFILE}" ]] && [[ ! -z "${MONIT_DOCKER_CONFIG}" ]];
then
    echo -e "${MONIT_DOCKER_CONFIG}" > "${MONIT_DOCKER_CONFFILE}"
fi

if [[ -f "${CRONTABS_PATH}/root" ]];
then
    if [[ ! -f "${MONIT_DOCKER_ROOT}/root-crontabs" ]];
    then
        cp -a "${CRONTABS_PATH}/root" "${MONIT_DOCKER_ROOT}/root-crontabs"
    fi
    rm -f "${CRONTABS_PATH}/root"
fi

if [[ -f "${MONIT_DOCKER_ROOT}/root-crontabs" ]];
then
    cp -a "${MONIT_DOCKER_ROOT}/root-crontabs" "${CRONTABS_PATH}/root"
fi

[[ ! -z "${MONIT_DOCKER_CRONS}" ]] && echo -e "${MONIT_DOCKER_CRONS}\n" >> "${CRONTABS_PATH}/root"

exec /usr/sbin/crond -f -L /dev/stdout -d 0
