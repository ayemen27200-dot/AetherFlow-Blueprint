#!/usr/bin/env bash
# Reproducible long-media split benchmark (D2 performance targets).
#
# Builds synthetic 30-minute and two-hour media by stream-concatenating the
# committed 5-second fixture (no re-encode, no private media), then runs the
# real `aetherflow split` executor and records wall time, output counts and
# machine specs. Everything lives under the git-ignored ./data directory.
#
# Run on the TARGET VPS to populate the acceptance evidence; running it
# elsewhere produces indicative numbers only, never a substitute for the VPS.
#
# Usage: FFMPEG=/path/to/ffmpeg bash scripts/benchmark_long_media.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
FFMPEG="${FFMPEG:-$(command -v ffmpeg || true)}"
[[ -n "$FFMPEG" ]] || { echo "FFMPEG binary not found" >&2; exit 2; }
AETHERFLOW="$REPO_ROOT/.venv/bin/aetherflow"
[[ -x "$AETHERFLOW" ]] || AETHERFLOW="$(command -v aetherflow)"

BENCH="$REPO_ROOT/data/bench"
SRC="$BENCH/src"
OUT="$BENCH/out"
FIXTURE="$REPO_ROOT/tests/fixtures/sample_5s.mp4"
SEGMENT_SECONDS=300
rm -rf "$BENCH"
mkdir -p "$SRC" "$OUT"

elapsed() {
  # $1 = command string; prints wall seconds with millisecond precision.
  local start end
  start=$(date +%s.%N)
  bash -c "$1"
  end=$(date +%s.%N)
  awk -v s="$start" -v e="$end" 'BEGIN { printf "%.2f", e - s }'
}

build_long_file() {
  # $1 = entry count, $2 = output path
  local count="$1" output="$2" list
  list="$(mktemp)"
  for ((i = 0; i < count; i++)); do
    printf "file '%s'\n" "$FIXTURE" >> "$list"
  done
  "$FFMPEG" -hide_banner -loglevel error -f concat -safe 0 -i "$list" \
    -c copy "$output"
  rm -f "$list"
}

specs() {
  echo "- date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- host: $(uname -a)"
  echo "- cpu: $(nproc) cores, $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs || echo unknown)"
  echo "- memory: $(awk '/MemTotal/ {print $2" KiB"}' /proc/meminfo)"
  echo "- disk: $(df -P "$BENCH" | tail -1 | awk '{print $2" KiB total, "$4" KiB free"}')"
  echo "- ffmpeg: $("$FFMPEG" -hide_banner -version | head -1)"
}

run_case() {
  # $1 = label, $2 = expected seconds, $3 = entry count, $4 = target ceiling
  local label="$1" seconds="$2" entries="$3" ceiling="$4"
  local input="$SRC/media-${label}.mp4" job_out="$OUT/$label" raw_out="$OUT/raw-$label"
  mkdir -p "$job_out" "$raw_out"
  echo "Building $label input ($entries x 5s)..."
  build_long_file "$entries" "$input"
  local size duration parts wall split_only
  size=$(stat -c%s "$input")
  echo "Running pure stream-copy split timing for $label..."
  split_only=$(elapsed "\"$FFMPEG\" -hide_banner -nostdin -y -i \"$input\" -map 0 -c copy \
    -f segment -segment_time $SEGMENT_SECONDS -reset_timestamps 1 \
    \"$raw_out/part_%03d.mp4\" >/dev/null 2>&1")
  echo "Running full executor benchmark (with decode verification) for $label..."
  wall=$(elapsed "\"$AETHERFLOW\" split \"$input\" --allowed-base \"$SRC\" \
    --output-dir \"$job_out\" --segment-seconds $SEGMENT_SECONDS --ffmpeg \"$FFMPEG\" \
    > \"$job_out/result.json\"")
  parts=$(find "$job_out" -name 'part_*.mp4' | wc -l)
  duration=$(python3 -c 'import json; print(json.load(open("'"$job_out/result.json"'"))["outputs_duration_sec"])')
  printf '| %s | %s | %s | %s | %s | %s | %s | %s |\n' \
    "$label" "$seconds" "$size" "$parts" "$duration" "$split_only" "$wall" "$ceiling"
}

{
  echo "# Long-media benchmark run (machine-generated, do not edit by hand)"
  echo
  specs
  echo
  echo "Wall time is the full executor path including mandatory decode"
  echo "verification of the input and every output part; split-only times the"
  echo "raw FFmpeg stream-copy command without verification."
  echo
  echo "| Input | Nominal duration (s) | File size (bytes) | Parts | Output duration (s) | Split-only (s) | Full wall (s) | Target ceiling (s) |"
  echo "|---|---:|---:|---:|---:|---:|---:|---:|"
  run_case "30m" 1800 360 15
  run_case "2h" 7200 1440 60
} | tee "$BENCH/RESULTS.md"

echo
echo "Results written to $BENCH/RESULTS.md"
