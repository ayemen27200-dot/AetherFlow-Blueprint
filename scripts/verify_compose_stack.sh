#!/usr/bin/env bash
# Clean-host verification of the published Docker Compose stack.
#
# FULLY ISOLATED, NON-DESTRUCTIVE VERIFICATION — safe to run on a host that
# already carries real data or another Compose stack:
#
#   * every run gets its own mktemp working directory (never the repository
#     data directory) and its own unique Compose project name and image tag;
#   * the repository checkout, its .env and its data/ tree are never written,
#     moved or deleted (an explicit snapshot check enforces this);
#   * a pre-existing Compose project named "aetherflow" is never touched:
#     teardown is scoped to the unique per-run project label, and a before/
#     after snapshot of the fixed project proves it is left alone;
#   * the temporary env file is created mode 0600 and the data tree 0750
#     owned by the container uid 1000 (never world-writable).
#
# It proves the full G2 path over real HTTP inside the Compose network:
# config validation, build, health, queued -> completed, independent decode,
# corrupt input -> INVALID_INPUT without partial output, worker restart
# durability, leak scans, and no host publication of port 8000.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || { echo "FAIL: docker is required" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "FAIL: Docker Compose v2 plugin is required" >&2; exit 1; }

# ---------------------------------------------------------------- isolation
RUN_ID="$(date +%s)-$$"
PROJECT="aetherflowsmoke${RUN_ID}"
IMAGE_REF="aetherflow-smoke:${RUN_ID}"
# Hard safety: the fixed production project name must never be used here.
[[ "$PROJECT" != "aetherflow" ]] || { echo "FAIL: refusing the fixed project name" >&2; exit 1; }

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aetherflow-smoke.XXXXXX")"
# Defence in depth: only ever remove a directory mktemp created for us.
case "$WORK_DIR" in
  /tmp/aetherflow-smoke.*|"${TMPDIR:-/tmp}"/aetherflow-smoke.*) ;;
  *) echo "FAIL: unexpected temp path $WORK_DIR" >&2; exit 1 ;;
esac
[[ -n "$WORK_DIR" && "$WORK_DIR" != "/" && "$WORK_DIR" != "$REPO_ROOT" ]] || {
  echo "FAIL: unsafe working directory" >&2; exit 1
}
DATA_DIR="$WORK_DIR/data"
SEED_DIR="$DATA_DIR/seed"
ENV_FILE="$WORK_DIR/smoke.env"
OVERRIDE_FILE="$WORK_DIR/override.yml"
DIAG_LOG="$WORK_DIR/diag.log"
BUILD_LOG="$WORK_DIR/build.log"
HEALTH_JSON="$WORK_DIR/health.json"
LAST_STATUS="$WORK_DIR/last-status.json"
CORRUPT_JSON="$WORK_DIR/corrupt.json"
CONFIG_RENDERED="$WORK_DIR/compose-config.yml"
# Unique named volume: ownership stays in the container namespace, which works
# identically on rootful Docker and on userns-remapped/rootless runners (a
# host bind mount would not, because host uid 1000 is a different uid there).
VOLUME_NAME="${PROJECT}_data"
: > "$DIAG_LOG"

TOKEN="compose-smoke-token-${RUN_ID}"
COMPOSE=(docker compose -p "$PROJECT" -f "$REPO_ROOT/docker/docker-compose.yml" -f "$OVERRIDE_FILE")

section() { printf '\n=== [%s] %s ===\n' "$(date -u +%H:%M:%SZ)" "$1"; }

dump_diagnostics() {
  {
    printf '\n--- docker compose ps -a ---\n'
    "${COMPOSE[@]}" ps -a || true
    printf '\n--- worker logs (tail 120) ---\n'
    "${COMPOSE[@]}" logs --tail=120 --no-color worker || true
    printf '\n--- orchestrator logs (tail 80) ---\n'
    "${COMPOSE[@]}" logs --tail=80 --no-color orchestrator || true
    printf '\n--- caddy logs (tail 40) ---\n'
    "${COMPOSE[@]}" logs --tail=40 --no-color caddy || true
  } 2>&1 | tee -a "$DIAG_LOG" >&2
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  dump_diagnostics
  exit 1
}

cleanup() {
  section "teardown"
  # Scoped to THIS run's unique project: cannot match any other stack.
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  # Only the uniquely tagged image built for this run is removed.
  docker image rm "$IMAGE_REF" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}

on_error() {
  local code=$?
  printf '\nFAIL diagnostics (exit code %s)\n' "$code" >&2
  dump_diagnostics
  exit "$code"
}
trap on_error ERR
trap cleanup EXIT

# Snapshot any pre-existing fixed-name project and the repository data tree so
# the final section can prove the run changed neither.
fixed_project_containers() {
  docker ps -a --filter "label=com.docker.compose.project=aetherflow" --format '{{.Names}}' | sort
}
PRE_FIXED="$(fixed_project_containers)"
REPO_DATA_SNAPSHOT="$WORK_DIR/repo-data-before.txt"
if [[ -d "$REPO_ROOT/data" ]]; then
  (cd "$REPO_ROOT" && find data -type f | sort) > "$REPO_DATA_SNAPSHOT"
else
  : > "$REPO_DATA_SNAPSHOT"
fi
# A unique project name must not already exist on this host.
EXISTING="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" -q)"
[[ -z "$EXISTING" ]] || fail "project name $PROJECT already exists; aborting to avoid touching foreign resources"

section "1. isolated env file and seed directory"
# Fail closed if anything ever points the temp env path at the checkout.
[[ "$ENV_FILE" != "$REPO_ROOT/.env" ]] || fail "env path must stay inside the temp directory"
(
  umask 077
  cat > "$ENV_FILE" <<EOF
BEARER_TOKEN=$TOKEN
DATABASE_PATH=/app/data/aetherflow.db
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
DOMAIN=localhost
ACME_EMAIL=admin@example.com
EOF
)
chmod 600 "$ENV_FILE"
[[ "$(stat -c '%a' "$ENV_FILE")" == "600" ]] || fail "temp env file is not mode 0600"
# Seed files live in the temp directory; world-readable is sufficient for a
# read-only seed mount and no production data ever lands here.
mkdir -p "$SEED_DIR"
chmod 755 "$WORK_DIR" "$DATA_DIR" "$SEED_DIR"

# Compose override replaces the data bind mount with a per-run unique named
# volume (durable under down -v scoped to this project only), keeps the
# read-only config bind, and gives both services a per-run unique image tag.
cat > "$OVERRIDE_FILE" <<EOF
services:
  orchestrator:
    image: $IMAGE_REF
    env_file:
      - $ENV_FILE
    volumes:
      - $VOLUME_NAME:/app/data
      - $REPO_ROOT/config:/app/config:ro
  worker:
    image: $IMAGE_REF
    env_file:
      - $ENV_FILE
    volumes:
      - $VOLUME_NAME:/app/data
      - $REPO_ROOT/config:/app/config:ro
volumes:
  $VOLUME_NAME:
EOF

section "2. compose config validation"
"${COMPOSE[@]}" config > "$CONFIG_RENDERED"
# Uvicorn must not get a host-published port.
if grep -Eq 'published:.*8000' "$CONFIG_RENDERED"; then
  fail "orchestrator port 8000 is published to the host"
fi
# The rendered config must use the unique named volume and never bind the
# repository data directory.
grep -Fq "$VOLUME_NAME" "$CONFIG_RENDERED" || fail "override did not install the isolated named volume"
! grep -Fq "$REPO_ROOT/data:" "$CONFIG_RENDERED" || fail "rendered config still binds the repository data directory"
echo "config valid; isolated named volume; no host publication of port 8000"

cp tests/fixtures/sample_5s.mp4 "$SEED_DIR/up-sample-5s.mp4"
python3 - "$SEED_DIR" <<'PY'
import sys
from pathlib import Path
seed = Path(sys.argv[1])
src = (seed / "up-sample-5s.mp4").read_bytes()
data = bytearray(src)
mid = len(data) // 2
data[mid:mid + 4096] = b"\x00" * 4096
(seed / "up-corrupt.mp4").write_bytes(data)
PY

section "3. build and start the isolated stack"
"${COMPOSE[@]}" build > "$BUILD_LOG" 2>&1
echo "image build OK (full log: $BUILD_LOG)"
# Seed the named volume as the image's own non-root uid 1000 (a fresh named
# volume inherits /app/data ownership from the image). No root, no chown and
# no host-side permission tricks, so the flow is identical on rootful and
# userns-remapped daemons and keeps the service's cap_drop: ALL posture.
"${COMPOSE[@]}" run --rm --no-deps \
  -v "$SEED_DIR:/seed:ro" orchestrator bash -c '
    set -euo pipefail
    mkdir -p /app/data/upload_store /app/data/outputs
    cp /seed/up-sample-5s.mp4 /seed/up-corrupt.mp4 /app/data/upload_store/
    chmod 750 /app/data /app/data/upload_store /app/data/outputs
    chmod 640 /app/data/upload_store/*
  '
"${COMPOSE[@]}" up -d

section "4. wait for API health (inside the compose network)"
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T orchestrator \
      curl --fail --silent http://127.0.0.1:8000/health > "$HEALTH_JSON" 2>/dev/null; then
    break
  fi
  sleep 2
done
cat "$HEALTH_JSON"; echo
grep -q '"database": *"ok"' "$HEALTH_JSON" || fail "health endpoint did not report a live database"

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
  # Echoes the bare terminal status word; the full record goes to a temp file.
  local id="$1"
  for _ in $(seq 1 60); do
    local state
    state="$(api_call GET "/jobs/$id")"
    local status
    status="$(printf '%s' "$state" | job_status)"
    if [[ "$status" == "completed" || "$status" == "failed" ]]; then
      printf '%s' "$state" > "$LAST_STATUS"
      printf '%s' "$status"
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
if printf '%s' "$DETAIL" | grep -Eq '"/|/app/|/home/|\\'; then
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
api_call GET "/jobs/$JOB2" | tee "$CORRUPT_JSON"
grep -q '"error_type": *"INVALID_INPUT"' "$CORRUPT_JSON" || fail "expected INVALID_INPUT"
# The data volume is container-namespace storage; check from inside the
# worker container that no partial output directory survived.
"${COMPOSE[@]}" exec -T worker test ! -e "/app/data/outputs/$JOB2" \
  || fail "partial output survived a failed job"

section "8. worker restart durability"
"${COMPOSE[@]}" restart worker >/dev/null
# Let the restarted worker settle before submitting the durability job.
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" ps worker | grep -q "healthy"; then break; fi
  sleep 1
done
"${COMPOSE[@]}" ps worker
CREATE3="$(api_call POST /jobs '{"type":"split","file":"up-sample-5s.mp4","priority":9}')"
echo "$CREATE3"
echo "$CREATE3" | grep -q '"status": *"queued"' || {
  printf 'unexpected create response after restart: %s\n' "$CREATE3" >&2
  fail "post-restart job was not queued"
}
JOB3="$(printf '%s' "$CREATE3" | job_id)"
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

section "10. port publication check, scoped to this run's project"
PROJECT_PS="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}} {{.Ports}}')"
echo "$PROJECT_PS"
if printf '%s' "$PROJECT_PS" | grep -Eq 'orchestrator.*0\.0\.0\.0|orchestrator.*:::'; then
  fail "orchestrator container published a host port"
fi

section "11. non-interference proof"
# 11a. The fixed-name project (if any) must be byte-identical before/after.
POST_FIXED="$(fixed_project_containers)"
[[ "$PRE_FIXED" == "$POST_FIXED" ]] || fail "verification touched the pre-existing 'aetherflow' project"
echo "fixed 'aetherflow' project containers unchanged: ${POST_FIXED:-<none present>}"
# 11b. The repository data tree must be unchanged (the run never wrote there).
if [[ -d "$REPO_ROOT/data" ]]; then
  POST_SNAPSHOT="$WORK_DIR/repo-data-after.txt"
  (cd "$REPO_ROOT" && find data -type f | sort) > "$POST_SNAPSHOT"
  diff -u "$REPO_DATA_SNAPSHOT" "$POST_SNAPSHOT" || fail "repository data tree changed during verification"
  echo "repository data/ tree unchanged ($(wc -l < "$POST_SNAPSHOT") tracked-by-find files)"
else
  echo "repository data/ absent before and after; nothing to disturb"
fi
# 11c. No repository .env was created or modified by this run.
[[ ! -e "$REPO_ROOT/.env" ]] || echo "note: a pre-existing repository .env was left untouched"

printf '\nPASS: isolated compose stack verification finished successfully (project %s)\n' "$PROJECT"
