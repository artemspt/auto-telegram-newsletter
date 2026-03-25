#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
SERVICE_NAME="telegrambot"
SERVICE_TEMPLATE="${ROOT_DIR}/deploy/telegrambot.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${ROOT_DIR}/.env"
RUN_USER="$(id -un)"
TMP_SERVICE="$(mktemp)"

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

cleanup() {
  rm -f "${TMP_SERVICE}"
}

trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Command not found: $1"
}

log "Starting Linux installation"
log "Project root: ${ROOT_DIR}"

require_command python3
require_command sed
require_command sudo
require_command systemctl

[[ -f "${SERVICE_TEMPLATE}" ]] || die "Service template not found: ${SERVICE_TEMPLATE}"
[[ -f "${ENV_FILE}" ]] || die "File .env not found. Copy .env.exemple to .env, fill in secrets, then run this script again."

if [[ -d "${VENV_DIR}" ]]; then
  log "Virtual environment already exists: ${VENV_DIR}"
else
  log "Creating virtual environment: ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

[[ -x "${PYTHON_BIN}" ]] || die "Python binary not found in virtual environment: ${PYTHON_BIN}"

log "Upgrading pip"
"${PYTHON_BIN}" -m pip install --upgrade pip

log "Installing dependencies"
"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements.txt"

log "Rendering systemd unit from template"
sed \
  -e "s|__WORKDIR__|${ROOT_DIR}|g" \
  -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  -e "s|__USER__|${RUN_USER}|g" \
  "${SERVICE_TEMPLATE}" > "${TMP_SERVICE}"

if sudo test -f "${SERVICE_DST}"; then
  log "Existing systemd unit found: ${SERVICE_DST}"
  if sudo cmp -s "${TMP_SERVICE}" "${SERVICE_DST}"; then
    log "Systemd unit is already up to date"
  else
    log "Updating systemd unit: ${SERVICE_DST}"
    sudo cp "${TMP_SERVICE}" "${SERVICE_DST}"
  fi
else
  log "Installing new systemd unit: ${SERVICE_DST}"
  sudo cp "${TMP_SERVICE}" "${SERVICE_DST}"
fi

log "Reloading systemd daemon"
sudo systemctl daemon-reload

if systemctl is-enabled "${SERVICE_NAME}" >/dev/null 2>&1; then
  log "Service already enabled: ${SERVICE_NAME}"
else
  log "Enabling service: ${SERVICE_NAME}"
  sudo systemctl enable "${SERVICE_NAME}"
fi

if systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
  log "Service is running, restarting: ${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
else
  log "Service is not running, starting: ${SERVICE_NAME}"
  sudo systemctl start "${SERVICE_NAME}"
fi

log "Installation completed"
log "Check status with: sudo systemctl status ${SERVICE_NAME}"
