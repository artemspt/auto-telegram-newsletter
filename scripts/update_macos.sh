#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
LABEL="com.telegrambot.broadcast"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
ENV_FILE="${ROOT_DIR}/.env"

log() {
  printf '[INFO] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

die() {
  printf '[ERROR] %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Command not found: $1"
}

log "Starting macOS update"
log "Project root: ${ROOT_DIR}"

require_command git
require_command python3
require_command launchctl

[[ -d "${ROOT_DIR}/.git" ]] || die "This script must be run inside a git checkout"
[[ -f "${PLIST_DST}" ]] || die "LaunchAgent is not installed. Run scripts/install_service_macos.sh first."
[[ -f "${ENV_FILE}" ]] || die "File .env not found. Copy .env.exemple to .env, fill in secrets, then run this script again."

cd "${ROOT_DIR}"
log "Pulling latest changes"
git pull --ff-only

if [[ -d "${VENV_DIR}" ]]; then
  log "Virtual environment already exists: ${VENV_DIR}"
else
  log "Virtual environment is missing, creating: ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

[[ -x "${PYTHON_BIN}" ]] || die "Python binary not found in virtual environment: ${PYTHON_BIN}"

log "Installing dependencies"
"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements.txt"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  log "Restarting loaded LaunchAgent: ${LABEL}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}"
else
  warn "LaunchAgent exists but is not loaded, loading now"
  launchctl load "${PLIST_DST}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || warn "kickstart returned non-zero status"
fi

log "Update completed"
log "LaunchAgent: ${LABEL}"
