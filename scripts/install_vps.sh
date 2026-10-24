#!/usr/bin/env bash
set -Eeuo pipefail

# Installs Docker Engine from Docker's official Ubuntu repository, initializes .env,
# then builds and starts the Compose scaffold. Run as a normal user with sudo.
if [[ "${EUID}" -eq 0 ]]; then
  echo "ERROR: run as a normal user with sudo available; do not create root-owned .env files." >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  echo "ERROR: sudo is required for OS package installation." >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo "ERROR: cannot determine operating system." >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "ERROR: this bootstrap currently supports Ubuntu only (tested by the operator on target host)." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture)"
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ -z "$CODENAME" ]]; then
  echo "ERROR: could not determine Ubuntu codename." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg ffmpeg rsync openssh-client python3
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' "$ARCH" "$CODENAME" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

cd "$ROOT_DIR"
python3 scripts/init_wizard.py
mkdir -p data
chmod 750 data

# Use sudo for Docker unless the current account already has daemon access.
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
fi
"${DOCKER[@]}" compose --project-directory "$ROOT_DIR" -f "$ROOT_DIR/docker/docker-compose.yml" config --quiet
"${DOCKER[@]}" compose --project-directory "$ROOT_DIR" -f "$ROOT_DIR/docker/docker-compose.yml" up -d --build

echo
printf '%s\n' \
  "Compose scaffold started." \
  "Before production: edit $ROOT_DIR/.env, set a real DOMAIN and ACME_EMAIL, point DNS to this VPS, and review firewall ports 80/443." \
  "The API's job endpoints and processing engines are intentionally incomplete until D2-D5 are implemented."
