                                                                                                                                                                                    #!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"

cd "${ROOT_DIR}"
git pull --ff-only

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements.txt"

sudo systemctl restart telegrambot

echo "Updated and restarted: telegrambot"
