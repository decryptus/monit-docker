#!/usr/bin/env bash
set -euo pipefail

export COMPOSE_PROJECT_NAME=monit-proxy-ci
export DOCKER_SOCKET_GID
DOCKER_SOCKET_GID=$(stat -c %g /var/run/docker.sock)
stack=(docker compose -f docker-compose.yml)
diff <("${stack[@]}" config) <("${stack[@]}" -f docker-compose.socket-proxy.yml config)
target=$(docker run -d alpine:3.20 sleep 600)
cleanup() {
    "${stack[@]}" down -v
    docker rm -f "$target"
}
trap cleanup EXIT
"${stack[@]}" up -d --build --wait --wait-timeout 180
agent=$("${stack[@]}" ps -q monit-docker)
proxy=$("${stack[@]}" ps -q socket-proxy)
docker exec "$agent" test ! -S /var/run/docker.sock
test -z "$(docker port "$proxy")"
docker exec "$agent" monit-docker --id "$target" stats --rsc cpu_percent --rsc starts_recent

# An exec created directly on a different container must never be attachable.
api=$(docker version --format '{{.Server.APIVersion}}')
foreign=$(curl -fsS --unix-socket /var/run/docker.sock -H 'Content-Type: application/json' \
    -d '{"Cmd":["true"]}' "http://localhost/v$api/containers/$agent/exec" | jq -er .Id)
check_policy() {
    docker exec -i "$agent" python - "$target" "$agent" "$foreign" "$1" <<'PY'
import sys
import docker
import requests

target, other, foreign, mode = sys.argv[1:]
client = docker.from_env()
url = 'http://socket-proxy:2375/v' + client.version()['ApiVersion']
def denied(method, path, body=None):
    response = requests.request(method, url + path, json=body, timeout=5)
    assert response.status_code == 403, (method, path, response.status_code)

denied('POST', '/containers/create', {})
denied('DELETE', '/containers/' + target)
denied('POST', '/containers/' + other + '/restart')
denied('POST', '/containers/' + other + '/exec', {'Cmd': ['true']})
denied('POST', '/containers/' + target + '/exec', {'Cmd': ['true'], 'Privileged': True})
denied('GET', '/exec/' + foreign + '/json')
denied('POST', '/exec/' + foreign + '/start', {})
denied('GET', '/containers/' + target + '/archive?path=/etc/passwd')
if mode != 'exec':
    denied('POST', '/containers/' + target + '/exec', {'Cmd': ['true']})
if mode != 'lifecycle':
    denied('POST', '/containers/' + target + '/restart')
PY
}
check_policy observe

export MONIT_PROXY_EXEC_TARGETS=$target
"${stack[@]}" up -d --force-recreate --no-deps socket-proxy
check_policy exec
docker exec "$agent" sh -ec 'printf "dir-groups:\n  data:\n    paths: [/tmp]\n" > /tmp/probe.yml'
probe_log=$(mktemp)
docker exec "$agent" monit-docker -c /tmp/probe.yml --id "$target" stats --rsc 'fs_mode[data]' --rsc 'disk_percent[data]' 2>"$probe_log" || { cat "$probe_log"; exit 1; }
if grep -q 'Exception ignored while finalizing' "$probe_log"; then cat "$probe_log"; exit 1; fi
rm -f "$probe_log"
docker exec "$agent" monit-docker --id "$target" monit --cmd '(touch /tmp/proxy-probe)'
docker exec "$target" test -f /tmp/proxy-probe

unset MONIT_PROXY_EXEC_TARGETS
export MONIT_PROXY_LIFECYCLE_TARGETS=$target
"${stack[@]}" up -d --force-recreate --no-deps socket-proxy
check_policy lifecycle
docker exec "$agent" monit-docker --id "$target" monit --cmd stop
test "$(docker inspect -f '{{.State.Running}}' "$target")" = false
docker exec "$agent" monit-docker --id "$target" monit --cmd start
test "$(docker inspect -f '{{.State.Running}}' "$target")" = true
