#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

set +u
set -a
[ -f ./.env ] && . ./.env || true
[ -f ./.env.local ] && . ./.env.local || true
set +a
set -u

if [[ ! -x ./.venv/bin/python ]]; then
  echo "Python venv not found at ./.venv. Please create it and install requirements." >&2
  exit 1
fi

export TREESIZE_USER=${TREESIZE_USER:-admin}
if [[ -z "${TREESIZE_PASS_HASH:-}" ]]; then
  TREESIZE_PASS_HASH="$(./.venv/bin/python - <<'PY'
from werkzeug.security import generate_password_hash;print(generate_password_hash('change_me_now'))
PY
)"
  export TREESIZE_PASS_HASH
fi

export TREESIZE_HOST=${TREESIZE_HOST:-0.0.0.0}
export TREESIZE_PORT=${TREESIZE_PORT:-5327}
export DEBUG_UI=${DEBUG_UI:-1}

exec ./.venv/bin/python app.py

