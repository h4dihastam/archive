#!/usr/bin/env bash
set -euo pipefail

REQ_FILE="${1:-requirements.txt}"

# Ignore possibly broken global pip proxy/index config.
export PIP_CONFIG_FILE=/dev/null
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

INDEXES=(
  "https://pypi.org/simple"
  "https://pypi.python.org/simple"
)

for idx in "${INDEXES[@]}"; do
  echo "[install_deps] trying index: ${idx}"
  if pip install --retries 2 --timeout 30 --index-url "${idx}" -r "${REQ_FILE}"; then
    echo "[install_deps] success"
    exit 0
  fi
  echo "[install_deps] failed with index ${idx}, trying next..."
done

echo "[install_deps] dependency installation failed on all known indexes."
echo "[install_deps] if your network is restricted, configure an internal mirror:"
echo "  pip install -r ${REQ_FILE} --index-url <YOUR_INTERNAL_PYPI_MIRROR>"
exit 1
