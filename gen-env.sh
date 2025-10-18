#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x ./.venv/bin/python ]]; then
  echo "Missing ./.venv/bin/python" >&2
  exit 1
fi

read -rp "Username [admin]: " INPUT_USER || true
USER_VAL=${INPUT_USER:-admin}

read -rsp "Password: " INPUT_PWD || true; echo
read -rsp "Confirm Password: " INPUT_PWD2 || true; echo
if [[ "${INPUT_PWD:-}" != "${INPUT_PWD2:-}" ]]; then
  echo "Passwords do not match" >&2
  exit 1
fi

read -rp "Host [0.0.0.0]: " INPUT_HOST || true
HOST_VAL=${INPUT_HOST:-0.0.0.0}
read -rp "Port [5327]: " INPUT_PORT || true
PORT_VAL=${INPUT_PORT:-5327}
read -rp "Enable DEBUG_UI (1/0) [1]: " INPUT_DEBUG || true
DEBUG_VAL=${INPUT_DEBUG:-1}

read -rp "Default base path [/] : " INPUT_DEFAULT_PATH || true
DEFAULT_PATH_VAL=${INPUT_DEFAULT_PATH:-/}

PWD_INPUT="${INPUT_PWD:-}" HASH_VAL="$(PWD_INPUT="${INPUT_PWD:-}" ./.venv/bin/python - <<'PY'
import os
from werkzeug.security import generate_password_hash
pwd = os.environ.get('PWD_INPUT','')
print(generate_password_hash(pwd))
PY
)"

TARGET_FILE=".env"
if [[ -f "$TARGET_FILE" ]]; then
  read -rp ".env exists. Overwrite? (y/N): " OVER || true
  case "${OVER:-}" in
    y|Y|yes|YES) :;;
    *) echo "Aborted"; exit 1;;
  esac
fi

printf "TREESIZE_USER=%q\n" "$USER_VAL" > "$TARGET_FILE"
printf "TREESIZE_PASS_HASH=%q\n" "$HASH_VAL" >> "$TARGET_FILE"
printf "TREESIZE_HOST=%q\n" "$HOST_VAL" >> "$TARGET_FILE"
printf "TREESIZE_PORT=%q\n" "$PORT_VAL" >> "$TARGET_FILE"
printf "DEBUG_UI=%q\n" "$DEBUG_VAL" >> "$TARGET_FILE"
printf "TREESIZE_DEFAULT_PATH=%q\n" "$DEFAULT_PATH_VAL" >> "$TARGET_FILE"

echo ".env written"

