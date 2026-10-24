#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT_DIR/tests/fixtures/sample_5s.mp4"
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is required" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i 'testsrc=duration=5:size=320x240:rate=30' \
  -f lavfi -i 'sine=frequency=440:duration=5' \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart \
  "$OUT"
echo "Generated $OUT"
