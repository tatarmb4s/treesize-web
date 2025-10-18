## TreeSize Web - Dev quickstart

### Setup
1. Create venv and install requirements:
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### Configuration via .env
1. Copy example to working file:
```bash
cp env.example .env
```
2. Edit `.env` to set `TREESIZE_USER`, `TREESIZE_PASS_HASH`, host/port, and optional `TREESIZE_DEFAULT_PATH` (default is `/`).
3. `dev.sh` will auto-load `.env` and `.env.local`.

Or generate interactively:
```bash
./gen-env.sh
```

### Run in development (no systemd)
```bash
./dev.sh
```
This sets `DEBUG_UI=1` to relax CSP and expose a small debug panel.

Manual alternative:
```bash
export TREESIZE_USER=admin
export TREESIZE_PASS_HASH="$(./.venv/bin/python - <<'PY'
from werkzeug.security import generate_password_hash;print(generate_password_hash('change_me_now'))
PY
)"
export TREESIZE_HOST=0.0.0.0 TREESIZE_PORT=5327 DEBUG_UI=1 TREESIZE_DEFAULT_PATH=/
./.venv/bin/python app.py
```

### Auth and CSRF
- Basic Auth via `TREESIZE_USER` / `TREESIZE_PASS_HASH`.
- CSRF cookie `ts_csrf` must match `X-CSRF-Token` header.
- `GET /api/csrf-debug` returns `{ cookieToken, headerRequired, user }` when authenticated.

### Endpoints
- `POST /api/scan` `{ basePath, sort, order }` → array of entries.
- `POST /api/delete` `{ basePath, paths[] }` → `{ results[] }`.
- `GET /api/list-dir?path=/path` → `{ entries[] }`.

### Emergency lite mode
Append `?lite=1` to `/` to load a minimal inline page that ignores external assets; production CSP uses nonces.

### Troubleshooting
- 403 CSRF: Ensure `X-CSRF-Token` matches cookie `ts_csrf`.
- 401 Unauthorized: Verify Basic Auth creds.
- CSP violations in dev: ensure `DEBUG_UI=1` or use `?lite=1`.

