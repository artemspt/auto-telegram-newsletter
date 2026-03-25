#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PLIST_TEMPLATE="${ROOT_DIR}/deploy/telegrambot.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
LABEL="com.telegrambot.broadcast"
PLIST_DST="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
ENV_FILE="${ROOT_DIR}/.env"
TMP_PLIST="$(mktemp)"

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
  rm -f "${TMP_PLIST}"
}

trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Command not found: $1"
}

log "Starting macOS installation"
log "Project root: ${ROOT_DIR}"

require_command python3
require_command sed
require_command launchctl

[[ -f "${PLIST_TEMPLATE}" ]] || die "LaunchAgent template not found: ${PLIST_TEMPLATE}"
[[ -f "${ENV_FILE}" ]] || die "File .env not found. Copy .env.exemple to .env, fill in secrets, then run this script again."

mkdir -p "${LAUNCH_AGENTS_DIR}"
mkdir -p "${ROOT_DIR}/logs"

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

log "Rendering LaunchAgent plist"
sed \
  -e "s|__LABEL__|${LABEL}|g" \
  -e "s|__WORKDIR__|${ROOT_DIR}|g" \
  -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  "${PLIST_TEMPLATE}" > "${TMP_PLIST}"

if [[ -f "${PLIST_DST}" ]]; then
  log "Existing LaunchAgent found: ${PLIST_DST}"
  if cmp -s "${TMP_PLIST}" "${PLIST_DST}"; then
    log "LaunchAgent plist is already up to date"
  else
    log "Updating LaunchAgent plist"
    cp "${TMP_PLIST}" "${PLIST_DST}"
  fi
else
  log "Installing new LaunchAgent plist"
  cp "${TMP_PLIST}" "${PLIST_DST}"
fi

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  log "LaunchAgent already loaded, unloading before reload"
  launchctl unload "${PLIST_DST}" >/dev/null 2>&1 || warn "Unable to unload existing LaunchAgent cleanly"
else
  log "LaunchAgent is not loaded yet"
fi

log "Loading LaunchAgent"
launchctl load "${PLIST_DST}"

log "Starting LaunchAgent"
launchctl kickstart -k "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || warn "kickstart returned non-zero status"

log "Installation completed"
log "LaunchAgent: ${LABEL}"
log "Plist: ${PLIST_DST}"
