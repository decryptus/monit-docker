#!/usr/bin/env bash
# Guided local installation using the repository's pinned Compose files.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() { echo 'Usage: ./install.sh [--ui | --agent-only]'; }

case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --ui) ui=yes ;;
    --agent-only) ui=no ;;
    '') ui=ask ;;
    *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 2; }

command -v docker >/dev/null || fail 'Install Docker Engine and the Docker Compose plugin.'
command -v curl >/dev/null || fail 'Install curl to verify the local endpoints.'
docker compose version >/dev/null || fail 'Install the Docker Compose v2 plugin.'
help=$(docker compose up --help)
[[ $help == *--wait-timeout* ]] || fail 'Update Docker Compose to a version with up --wait-timeout.'
if [[ -n ${DOCKER_CONTEXT:-} || -z ${DOCKER_HOST:-} ]]; then
    endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
else
    endpoint=$DOCKER_HOST
fi
[[ $endpoint == unix:///* ]] || fail 'Select a local Unix-socket Docker context first.'
export DOCKER_SOCKET_PATH=${DOCKER_SOCKET_PATH:-${endpoint#unix://}}
[[ $DOCKER_SOCKET_PATH == /* && -S $DOCKER_SOCKET_PATH ]] || fail 'Set DOCKER_SOCKET_PATH to an existing local Docker socket.'
engine=(docker --host "unix://$DOCKER_SOCKET_PATH")
platform=$("${engine[@]}" info --format '{{.OSType}}/{{.Architecture}}') || fail 'Check that Docker is running and your account can access its socket.'
case "$platform" in linux/x86_64|linux/amd64) ;; *) fail "Published images require Linux amd64 (found $platform).";; esac

if [[ $ui == ask ]]; then
    read -r -p 'Install the local web dashboard too? [Y/n] ' answer || fail 'Choose --ui or --agent-only.'
    case "$answer" in ''|[Yy]|[Yy][Ee][Ss]|[Oo]|[Oo][Uu][Ii]) ui=yes ;; [Nn]|[Nn][Oo]|[Nn][Oo][Nn]) ui=no ;; *) fail 'Answer yes or no.' ;; esac
fi
compose=("${engine[@]}" compose -f docker-compose.yml)
if [[ $ui == yes ]]; then
    export UI_BIND=127.0.0.1
    compose+=(-f docker-compose.ui.yml)
    secrets=examples/ui/secrets.local
    if [[ ! -e $secrets ]]; then
        command -v python3 >/dev/null && command -v openssl >/dev/null || fail 'Install Python 3 and OpenSSL to prepare dashboard credentials.'
        python3 examples/ui/prepare.py --self-signed
    fi
    for name in htpasswd tls.crt tls.key proxy-action.conf; do
        [[ -s $secrets/$name ]] || fail "Missing $secrets/$name. Repair the existing credentials before retrying."
    done
    command -v openssl >/dev/null || fail 'Install OpenSSL to verify the dashboard certificate.'
    openssl x509 -in "$secrets/tls.crt" -checkend 0 -noout >/dev/null || fail "Renew $secrets/tls.crt before starting the dashboard."
fi

"${compose[@]}" config --quiet
echo 'Starting local monitoring with the root Compose configuration.'
"${compose[@]}" up -d --wait --wait-timeout 180
binding=$("${compose[@]}" port monit-docker 9808)
curl --noproxy '*' --fail --silent --show-error --max-time 10 "http://$binding/readyz" >/dev/null
count=$("${compose[@]}" exec -T monit-docker python -c '
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:9808/v1/status", timeout=5) as response:
    status = json.load(response)
if not status["ready"]:
    raise SystemExit("Collection is not ready. Check docker compose logs monit-docker.")
print(len(status["containers"]))')
printf '%s containers detected. Local API: http://%s/v1/status\n' "$count" "$binding"
if [[ $ui == yes ]]; then
    binding=$("${compose[@]}" port ui 443)
    port=${binding##*:}
    url="https://localhost:$port"
    # Nginx can start listening just after Compose reports the container running.
    code=$(curl --noproxy '*' --silent --show-error --retry 8 --retry-connrefused --retry-delay 1 \
        --connect-timeout 2 --max-time 5 --cacert "$secrets/tls.crt" -o /dev/null -w '%{http_code}' "$url/")
    [[ $code == 401 ]] || fail "Expected a dashboard login challenge at $url (HTTP $code)."
    printf 'Dashboard: %s (sign in with your dashboard credentials).\n' "$url"
    if [[ -n ${SSH_CONNECTION:-} ]]; then
        printf 'On your workstation: ssh -N -L %s:127.0.0.1:%s USER@SERVER\nThen open %s on your workstation.\n' "$port" "$port" "$url"
    fi
fi
