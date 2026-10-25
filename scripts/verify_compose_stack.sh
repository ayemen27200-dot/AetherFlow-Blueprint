#!/usr/bin/env bash
# Clean-host verification of the published Docker Compose stack.
#
# Runs on any host with Docker v2 (the CI compose-smoke job and the target
# VPS). It performs an isolated, throwaway installation under ./data and
# proves the full G2 path over real HTTP:
#
#   1. `docker compose config` validates the published YAML.
#   2. Clean data directory seeded with a valid and a corrupt upload object.
#   3. The API and the worker run as separate containers sharing one SQLite
#      file; Uvicorn is never published to the host (Caddy owns 80/443).
#   4. queued -> completed over HTTP, output independently decoded, corrupt
#      input -> failed/INVALID_INPUT, worker restart durability, and path /
#      secret leak scans.
#
# The script tears the stack down on exit and removes the temporary .env it
# creates. It never touches a pre-existing .env file.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
COMPOSE=(docker compose -f docker/docker-compose.yml)
DATA_DIR="$REPO_ROOT/data"
TOKEN="compose-smoke-token-$(date +%s)"
CREATED_ENV=0

section() { printf '\n=== [%s] %s ===\n' "$(date -u +%H:%M:%SZ)" "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

cleanup() {
  section "teardown"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  if [[ "$CREATED_ENV" -eq 1 ]]; then rm -f "$REPO_ROOT/.env"; fi
}

on_error() {
  local code=$?
  printf '\nFAIL diagnostics (exit code %s)\n' "$code" >&2
  "${COMPOSE[@]}" ps -a || true
  "${COMPOSE[@]}" logs --tail=120 --no-color || true
  exit "$code"
}
trap on_error ERR
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || fail "docker is required to run this verification"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin is required"

section "1. isolated env file and data directory"
if [[ -f "$REPO_ROOT/.env" ]]; then
  fail "a real .env already exists; refusing to overwrite it on a clean-host check"
fi
cat > "$REPO_ROOT/.env" <<EOF
BEARER_TOKEN=$TOKEN
DATABASE_PATH=/app/data/aetherflow.db
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
DOMAIN=localhost
ACME_EMAIL=admin@example.com
EOF
CREATED_ENV=1
rm -rf "$DATA_DIR"

section "2. compose config validation"
"${COMPOSE[@]}" config >/tmp/aetherflow-compose-config.yml
# Uvicorn must not get a host-published port.
if grep -Eq 'published:.*8000' /tmp/aetherflow-compose-config.yml; then
  fail "orchestrator port 8000 is published to the host"
fi
echo "config valid; no host publication of port 8000"
mkdir -p "$DATA_DIR/upload_store" "$DATA_DIR/outputs"
# The containers run as the non-root uid 1000 baked into the image, while a
# clean host creates this directory as the invoking user. Make the shared
# data tree writable by the container uid; production hosts should prefer
# `chown -R 1000:1000 data` with normal 0755 modes instead of 0777.
chmod 0777 "$DATA_DIR" "$DATA_DIR/upload_store" "$DATA_DIR/outputs"
cp tests/fixtures/sample_5s.mp4 "$DATA_DIR/upload_store/up-sample-5s.mp4"
python3 - <<'PY'
from pathlib import Path
src = Path("data/upload_store/up-sample-5s.mp4").read_bytes()
data = bytearray(src)
mid = len(data) // 2
data[mid:mid + 4096] = b"\x00" * 4096
Path("data/upload_store/up-corrupt.mp4").write_bytes(data)
PY

section "3. build and start the published stack"
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d

section "4. wait for API health (inside the compose network)"
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T orchestrator \
      curl --fail --silent http://127.0.0.1:8000/health >/tmp/health.json 2>/dev/null; then
    break
  fi
  sleep 2
done
cat /tmp/health.json; echo
grep -q '"database": *"ok"' /tmp/health.json || fail "health endpoint did not report a live database"

api_call() {
  # $1 = method, $2 = path, optional $3 = JSON body
  if [[ "$1" == "POST" ]]; then
    "${COMPOSE[@]}" exec -T orchestrator curl -sS -X "$1" \
      -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
      -d "$3" "http://127.0.0.1:8000$2"
  else
    "${COMPOSE[@]}" exec -T orchestrator curl -sS -H "Authorization: Bearer $TOKEN" \
      "http://127.0.0.1:8000$2"
  fi
}
job_id() { python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }
job_status() { python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'; }
wait_terminal() {
  local id="$1"
  for _ in $(seq 1 60); do
    local state
    state="$(api_call GET "/jobs/$id")"
    local status
    status="$(printf '%s' "$state" | job_status)"
    if [[ "$status" == "completed" || "$status" == "failed" ]]; then
      printf '%s' "$state" | tee /tmp/last-status
      return 0
    fi
    sleep 1
  done
  fail "job $id did not reach a terminal state"
}

section "5. happy path: queued -> completed over real HTTP"
CREATE="$(api_call POST /jobs '{"type":"split","file":"up-sample-5s.mp4","priority":5}')"
echo "$CREATE" | grep -q '"status": *"queued"' || fail "job was not created as queued"
JOB1="$(printf '%s' "$CREATE" | job_id)"
[[ "$(wait_terminal "$JOB1")" == "completed" ]] || fail "valid job did not complete"
DETAIL="$(api_call GET "/jobs/$JOB1")"
echo "$DETAIL"
# No absolute filesystem path may appear in any public representation.
if printf '%s' "$DETAIL" | grep -Eq '"/|/app/|/home/|\\\\'; then
  fail "absolute or escaped path leaked into the job response"
fi
REF="$(printf '%s' "$DETAIL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["output_paths"][0])')"
[[ "$REF" == */part_*.mp4 || "$REF" == *"/part_000.mp4" ]] || fail "unexpected output reference $REF"

section "6. independent decode of the real output inside the worker container"
"${COMPOSE[@]}" exec -T worker ffmpeg -hide_banner -nostdin -v error -xerror \
  -i "/app/data/outputs/$REF" -f null -
echo "output decoded cleanly"

section "7. corrupt input fails with a safe code and leaves no partial output"
JOB2="$(api_call POST /jobs '{"type":"split","file":"up-corrupt.mp4"}' | job_id)"
[[ "$(wait_terminal "$JOB2")" == "failed" ]] || fail "corrupt job was not marked failed"
api_call GET "/jobs/$JOB2" | tee /tmp/corrupt-job.json
grep -q '"error_type": *"INVALID_INPUT"' /tmp/corrupt-job.json || fail "expected INVALID_INPUT"
[[ ! -e "$DATA_DIR/outputs/$JOB2" ]] || fail "partial output survived a failed job"

section "8. worker restart durability"
"${COMPOSE[@]}" restart worker >/dev/null
JOB3="$(api_call POST /jobs '{"type":"split","file":"up-sample-5s.mp4","priority":9"}' | job_id)"
[[ "$(wait_terminal "$JOB3")" == "completed" ]] || fail "job after worker restart did not complete"

section "9. secret and path leak scans across logs and responses"
LOGS="$("${COMPOSE[@]}" logs --no-color)"
if printf '%s' "$LOGS" | grep -Fq "$TOKEN"; then
  fail "the bearer token appeared in container logs"
fi
ALL_JOBS="$(api_call GET "/jobs?limit=10")"
if printf '%s' "$ALL_JOBS" | grep -Eq '/app/|/home/|/var/'; then
  fail "absolute server path leaked through the list endpoint"
fi
# Unauthenticated and malformed-id behaviour over real HTTP.
"${COMPOSE[@]}" exec -T orchestrator curl -sS -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8000/jobs | grep -qx 401 || fail "missing 401 without a token"
api_call POST /jobs '{"type":"split","file":"../etc/passwd"}' | grep -q 'upload id' \
  || fail "path-like upload id was not rejected"

section "10. port publication check on the live containers"
if docker ps --format '{{.Names}} {{.Ports}}' | grep -Eq 'orchestrator.*0\.0\.0\.0|orchestrator.*:::'; then
  fail "orchestrator container published a host port"
fi
docker ps --format '{{.Names}} {{.Ports}}'

printf '\nPASS: compose stack verification finished successfully\n'
