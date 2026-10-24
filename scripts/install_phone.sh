#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Run inside Termux, from the AetherFlow repository checkout.
if [[ -z "${PREFIX:-}" || ! -d "$PREFIX" || ! -x "$(command -v pkg || true)" ]]; then
  echo "ERROR: run this script inside Termux on Android." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pkg update -y
pkg install -y python git ffmpeg openssh rsync clang cmake make pkg-config rust
python -m pip install --upgrade pip
python -m pip install -e ".[phone]"
python scripts/init_wizard.py

mkdir -p "$HOME/.config/aetherflow"
chmod 700 "$HOME/.config/aetherflow"
echo
printf '%s\n' \
  "Phone prerequisites and Python scaffold installed." \
  "This script does not install/download whisper.cpp binaries or model weights: pin and verify them first." \
  "The API/job executor is still a scaffold; see D3_CORE_ENGINE and D2_MVP acceptance criteria."
