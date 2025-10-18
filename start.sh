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

export DEBUG_UI=${DEBUG_UI:-}

exec ./.venv/bin/python app.py

