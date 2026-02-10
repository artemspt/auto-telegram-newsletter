#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
SERVICE_DST="/etc/systemd/system/telegrambot.service"
RUN_USER="$(id -un)"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements.txt"

sed \
  -e "s|__WORKDIR__|${ROOT_DIR}|g" \
  -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  -e "s|__USER__|${RUN_USER}|g" \
  "${ROOT_DIR}/deploy/telegrambot.service" | sudo tee "${SERVICE_DST}" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable telegrambot
sudo systemctl restart telegrambot

echo "Service installed and started: telegrambot"
