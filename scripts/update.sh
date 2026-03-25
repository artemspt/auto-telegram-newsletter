#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
SERVICE_NAME="telegrambot"
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

log "Starting Linux update"
log "Project root: ${ROOT_DIR}"

require_command git
require_command python3
require_command sudo
require_command systemctl

[[ -d "${ROOT_DIR}/.git" ]] || die "This script must be run inside a git checkout"
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

if sudo systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
  if systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
    log "Restarting active service: ${SERVICE_NAME}"
    sudo systemctl restart "${SERVICE_NAME}"
  else
    warn "Service exists but is not running, starting: ${SERVICE_NAME}"
    sudo systemctl start "${SERVICE_NAME}"
  fi
else
  die "Service ${SERVICE_NAME} is not installed. Run scripts/install_service.sh first."
fi

log "Update completed"
log "Check status with: sudo systemctl status ${SERVICE_NAME}"
