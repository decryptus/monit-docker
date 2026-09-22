#!/bin/sh
# Start the optional monitoring stack with a persistent, locally generated password.
set -eu
cd "$(dirname "$0")"

notifications=false
redis=false
for option in "$@"; do
    case "$option" in
        --notifications) notifications=true ;;
        --redis) notifications=true; redis=true ;;
        *) printf '%s\n' 'Usage: sh start.sh [--notifications] [--redis]' >&2; exit 2 ;;
    esac
done

docker compose version >/dev/null
umask 077
if [ ! -e .env ]; then
    password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
    [ "${#password}" -eq 48 ] || { printf '%s\n' 'Unable to generate a password.' >&2; exit 1; }
    # Refuse to overwrite a file created by another invocation.
    (set -C; printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$password" > .env)
    unset password
    printf '%s\n' 'Created .env with a random Grafana admin password (permissions 0600).'
fi

set -- -f compose.yaml
if [ "$notifications" = true ]; then
    set -- "$@" -f compose.notifications.yaml
fi
if [ "$redis" = true ]; then
    set -- "$@" -f compose.redis.yaml
fi
docker compose --env-file .env "$@" up -d --wait --wait-timeout 240
printf '%s\n' 'Monitoring stack started. Default Grafana URL: http://127.0.0.1:3000'
printf '%s\n' 'Log in as admin using GRAFANA_ADMIN_PASSWORD from .env. See docs/compose.md for details.'
