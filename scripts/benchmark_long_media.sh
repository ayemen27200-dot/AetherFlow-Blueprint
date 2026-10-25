#!/usr/bin/env bash
# Reproducible long-media split benchmark (D2 performance targets).
#
# Two input modes:
#   * synthetic (default): 30-minute and two-hour media built by
#     stream-concatenating the committed 5-second fixture (no re-encode, no
#     private media). The synthetic file repeats one 5-second GOP pattern, so
#     its keyframe layout, bitrate and codec are those of the fixture — it is
#     an indicative workload, NOT proof for arbitrary production media.
#   * representative (VPS acceptance): provide real files through
#     BENCH_30M_INPUT and BENCH_2H_INPUT. They are used as-is (copied, never
#     re-encoded) and their codec/bitrate/resolution are recorded, so the
#     evidence states exactly what was measured.
#
# Run on the TARGET VPS to populate the acceptance evidence; numbers produced
# anywhere else are labelled with their machine and never close the criterion.
#
# Usage:
#   FFMPEG=/path/to/ffmpeg bash scripts/benchmark_long_media.sh
#   BENCH_30M_INPUT=/srv/media/real30.mp4 BENCH_2H_INPUT=/srv/media/real2h.mp4 \
#     FFMPEG=/usr/bin/ffmpeg FFPROBE=/usr/bin/ffprobe bash scripts/benchmark_long_media.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
FFMPEG="${FFMPEG:-$(command -v ffmpeg || true)}"
[[ -n "$FFMPEG" ]] || { echo "FFMPEG binary not found" >&2; exit 2; }
FFPROBE="${FFPROBE:-$(command -v ffprobe || true)}"
AETHERFLOW="$REPO_ROOT/.venv/bin/aetherflow"
[[ -x "$AETHERFLOW" ]] || AETHERFLOW="$(command -v aetherflow)"

BENCH="$REPO_ROOT/data/bench"
# Guard: only the script's own dedicated directory is ever removed.
[[ "$BENCH" == "$REPO_ROOT/data/bench" ]] || { echo "unsafe bench path" >&2; exit 2; }
SRC="$BENCH/src"
OUT="$BENCH/out"
FIXTURE="$REPO_ROOT/tests/fixtures/sample_5s.mp4"
SEGMENT_SECONDS=300
# Reviewer requirement: three full-path runs per duration; overridable for
# quick smoke checks, never for acceptance evidence.
REPEATS="${BENCH_REPEATS:-3}"
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

media_info() {
  # One-line codec/geometry/bitrate summary; ffprobe when present, otherwise
  # the FFmpeg banner, so recording never depends on an extra binary.
  local file="$1"
  if [[ -n "$FFPROBE" ]]; then
    "$FFPROBE" -v error -of json \
      -show_entries format=duration,bit_rate:stream=codec_type,codec_name,width,height,bit_rate \
      "$file" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
video = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), {})
audio = next((s for s in doc.get("streams", []) if s.get("codec_type") == "audio"), {})
fmt = doc.get("format", {})
print(f"video={video.get(\"codec_name\", \"?\")} "
      f"{video.get(\"width\", \"?\")}x{video.get(\"height\", \"?\")} "
      f"stream_bitrate={video.get(\"bit_rate\", \"?\")} "
      f"audio={audio.get(\"codec_name\", \"none\")} "
      f"container_bitrate={fmt.get(\"bit_rate\", \"?\")} "
      f"duration={fmt.get(\"duration\", \"?\")}")'
  else
    # FFmpeg exits 1 when invoked with no output argument; the banner parse
    # is intentional, so neutralise the status under pipefail.
    "$FFMPEG" -hide_banner -nostdin -i "$file" 2>&1 \
      | grep -E 'Duration|Video:' | tr '\n' ' ' | sed 's/  */ /g' || true
  fi
}

specs() {
  echo "- date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- host: $(uname -a)"
  echo "- cpu: $(nproc) cores, $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs || echo unknown)"
  echo "- memory: $(awk '/MemTotal/ {print $2" KiB"}' /proc/meminfo)"
  echo "- disk: $(df -P "$BENCH" | tail -1 | awk '{print $2" KiB total, "$4" KiB free"}')"
  echo "- ffmpeg: $("$FFMPEG" -hide_banner -version | head -1)"
  echo "- ffprobe: ${FFPROBE:-not available (banner fallback used)}"
  echo "- code_head: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "- code_dirty: $(test -n "$(git status --porcelain -- executors agent 2>/dev/null)" && echo yes || echo no)"
  echo "- verify_concurrency_env: ${AETHERFLOW_VERIFY_CONCURRENCY:-<default>}"
  echo "- repeats_per_duration: $REPEATS"
}

summarise() {
  # Emits "median min" for the whitespace-separated seconds passed as args.
  python3 -c '
import statistics, sys
values = [float(v) for v in sys.argv[1:]]
print(f"{statistics.median(values):.2f} {min(values):.2f}")
' "$@"
}

prepare_input() {
  # $1 = label, $2 = concat entries, $3 = optional real-file env var name
  # Echoes "kind|path" for the chosen input.
  local label="$1" entries="$2" env_name="$3" real_path
  local output="$SRC/media-${label}.mp4"
  real_path="${!env_name:-}"
  if [[ -n "$real_path" ]]; then
    [[ -f "$real_path" ]] || { echo "ERROR: $env_name points at missing file $real_path" >&2; exit 2; }
    cp "$real_path" "$output"
    printf 'representative|%s' "$output"
  else
    echo "Building synthetic $label input ($entries x 5s)..." >&2
    build_long_file "$entries" "$output"
    printf 'synthetic|%s' "$output"
  fi
}

run_case() {
  # $1 = label, $2 = nominal seconds, $3 = concat entries, $4 = ceiling, $5 = real-file env var
  local label="$1" seconds="$2" entries="$3" ceiling="$4" env_name="$5"
  local case_out="$OUT/$label" raw_out="$OUT/raw-$label"
  mkdir -p "$case_out" "$raw_out"
  local prepared kind input size duration parts split_only info run_n job_out result
  local -a walls=()
  prepared="$(prepare_input "$label" "$entries" "$env_name")"
  kind="${prepared%%|*}"
  input="${prepared#*|}"
  size=$(stat -c%s "$input")
  info="$(media_info "$input")"
  echo "[$label] source=$kind; $info" >&2
  echo "Running pure stream-copy split timing for $label..." >&2
  split_only=$(elapsed "\"$FFMPEG\" -hide_banner -nostdin -y -i \"$input\" -map 0 -c copy \
    -f segment -segment_time $SEGMENT_SECONDS -reset_timestamps 1 \
    \"$raw_out/part_%03d.mp4\" >/dev/null 2>&1")
  for ((run_n = 1; run_n <= REPEATS; run_n++)); do
    job_out="$case_out/run-$run_n"
    mkdir -p "$job_out"
    result="$job_out/result.json"
    echo "[$label] full executor run $run_n/$REPEATS (with decode verification)..." >&2
    walls+=("$(elapsed "\"$AETHERFLOW\" split \"$input\" --allowed-base \"$SRC\" \
      --output-dir \"$job_out\" --segment-seconds $SEGMENT_SECONDS --ffmpeg \"$FFMPEG\" \
      > \"$result\"")")
  done
  # All repeats use the same input, so structural stats come from the last run.
  parts=$(find "$job_out" -name 'part_*.mp4' | wc -l)
  duration=$(python3 -c 'import json; print(json.load(open("'"$result"'"))["outputs_duration_sec"])')
  local summary median min_wall verdict
  summary="$(summarise "${walls[@]}")"
  median="${summary%% *}"
  min_wall="${summary##* }"
  verdict=$(awk -v m="$median" -v c="$ceiling" 'BEGIN { print (m < c) ? "PASS" : "OPEN" }')
  printf '| %s | %s | %s | %s | %s | %s | %s |' \
    "$label" "$kind" "$seconds" "$size" "$parts" "$duration" "$split_only"
  local wall
  for wall in "${walls[@]}"; do
    printf ' %s |' "$wall"
  done
  printf ' %s | %s | %s | %s |\n' "$median" "$min_wall" "$ceiling" "$verdict"
  echo "  - ${label} media characteristics: ${info}"
  echo "  - ${label} full wall per run (s): ${walls[*]}; median=${median}, min=${min_wall}"
}

{
  echo "# Long-media benchmark run (machine-generated, do not edit by hand)"
  echo
  specs
  echo
  echo "Wall time is the full executor path including mandatory full decode"
  echo "verification of the input and every output part (exit 0 plus an empty"
  echo "error stream); split-only times the raw FFmpeg stream-copy command"
  echo "without verification. Each duration runs $REPEATS full-path repeats;"
  echo "the verdict compares the median wall against the target ceiling, and"
  echo "every per-run value is shown so spread is reviewable. Synthetic inputs"
  echo "repeat the committed 5-second fixture's GOP/bitrate/codec and are not"
  echo "representative of all production media; representative real files can"
  echo "be supplied via BENCH_30M_INPUT and BENCH_2H_INPUT."
  echo
  {
    printf '| Input | Source | Nominal duration (s) | File size (bytes) | Parts | Output duration (s) | Split-only (s) |'
    local_i=1
    while ((local_i <= REPEATS)); do
      printf ' Full wall run %s (s) |' "$local_i"
      local_i=$((local_i + 1))
    done
    printf ' Median wall (s) | Min wall (s) | Target ceiling (s) | Verdict |\n'
    printf '|---|---|---:|---:|---:|---:|---:|'
    local_i=1
    while ((local_i <= REPEATS)); do
      printf '%s' '---:|'
      local_i=$((local_i + 1))
    done
    printf '%s\n' '---:|---:|---:|---|'
  }
  run_case "30m" 1800 360 15 "BENCH_30M_INPUT"
  run_case "2h" 7200 1440 60 "BENCH_2H_INPUT"
} | tee "$BENCH/RESULTS.md"

echo
echo "Results written to $BENCH/RESULTS.md"
