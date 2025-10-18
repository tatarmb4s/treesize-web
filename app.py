import base64
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Flask, Response, jsonify, make_response, request
from werkzeug.security import check_password_hash

try:
    from systemd.journal import JournalHandler
    _HAS_JOURNAL = True
except Exception:
    _HAS_JOURNAL = False


class RateLimiter:
    """In-memory token bucket limiter keyed by client and route."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            last, level = self._buckets.get(key, (now, self.capacity))
            elapsed = max(0.0, now - last)
            level = min(self.capacity, level + elapsed * self.refill_per_second)
            if level < tokens:
                self._buckets[key] = (now, level)
                return False
            self._buckets[key] = (now, level - tokens)
            return True


def configure_logging() -> logging.Logger:
    """Configure journald or stderr logging for the app."""
    logger = logging.getLogger("treesize-web")
    logger.setLevel(logging.INFO)
    if _HAS_JOURNAL:
        handler: logging.Handler = JournalHandler()
    else:
        handler = logging.StreamHandler()
    fmt = logging.Formatter("%(levelname)s %(message)s")
    handler.setFormatter(fmt)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger


def b64_decode(data: str) -> bytes:
    """Decode base64 with missing padding tolerated."""
    padding = '=' * (-len(data) % 4)
    return base64.b64decode(data + padding)


def read_env_text(name: str, default: Optional[str] = None) -> str:
    """Read a required environment variable, with optional default."""
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_client_identity() -> str:
    """Return the remote IP address string."""
    return request.remote_addr or "unknown"


def parse_basic_auth(header: str) -> Optional[Tuple[str, str]]:
    """Parse an HTTP Basic Authorization header into username and password."""
    if not header.lower().startswith("basic "):
        return None
    try:
        raw = b64_decode(header.split(" ", 1)[1])
        decoded = raw.decode("utf-8", "strict")
        if ":" not in decoded:
            return None
        user, pwd = decoded.split(":", 1)
        return user, pwd
    except Exception:  # noqa: BLE001
        return None


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string comparison wrapper."""
    return secrets.compare_digest(a.encode(), b.encode())


def require_auth(app_logger: logging.Logger) -> Optional[Response]:
    """Validate HTTP Basic credentials from the request or return a 401 response."""
    auth_header = request.headers.get("Authorization", "")
    parsed = parse_basic_auth(auth_header)
    if not parsed:
        resp = make_response("Unauthorized", 401)
        resp.headers["WWW-Authenticate"] = "Basic realm=treesize-web"
        return resp
    user, pwd = parsed
    expected_user = read_env_text("TREESIZE_USER", "admin")
    pass_hash = read_env_text("TREESIZE_PASS_HASH", "")
    if not constant_time_equals(user, expected_user) or not check_password_hash(pass_hash, pwd):
        app_logger.warning("auth_failed user=%s ip=%s", user, get_client_identity())
        resp = make_response("Unauthorized", 401)
        resp.headers["WWW-Authenticate"] = "Basic realm=treesize-web"
        return resp
    return None


def generate_csrf_token() -> str:
    """Create a random CSRF token."""
    return secrets.token_urlsafe(32)


def get_csrf_from_request() -> Tuple[str, str]:
    """Read CSRF cookie and header values from the request."""
    cookie = request.cookies.get("ts_csrf", "")
    header = request.headers.get("X-CSRF-Token", "")
    return cookie, header


def verify_csrf() -> Optional[Response]:
    """Enforce CSRF by comparing cookie and header tokens."""
    cookie, header = get_csrf_from_request()
    if not cookie or not header or not constant_time_equals(cookie, header):
        return make_response(jsonify({"error": "CSRF token invalid"}), 403)
    return None


DENYLIST_BASE_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/lost+found",
)


def normalize_path(p: str) -> str:
    """Normalize and resolve a filesystem path."""
    return os.path.realpath(os.path.abspath(p))


def ensure_valid_base(base_path: str) -> str:
    """Validate a base directory against existence and denylist."""
    real = normalize_path(base_path)
    if not os.path.isdir(real):
        raise ValueError("Base path must be an existing directory")
    for prefix in DENYLIST_BASE_PREFIXES:
        if real == prefix or real.startswith(prefix + os.sep):
            raise ValueError("Base path is not allowed")
    return real


def is_within_base(base: str, target: str) -> bool:
    """Return True if target is inside base after realpath resolution."""
    try:
        base_real = normalize_path(base)
        target_real = normalize_path(target)
        common = os.path.commonpath([base_real, target_real])
        return common == base_real
    except Exception:  # noqa: BLE001
        return False


def format_ts(ts: float) -> str:
    """Format a POSIX timestamp to ISO 8601 string."""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def safe_stat(path: str) -> Optional[os.stat_result]:
    """Return lstat result or None on error."""
    try:
        return os.lstat(path)
    except Exception:  # noqa: BLE001
        return None


def compute_entry_size(path: str) -> Tuple[int, int, int, int, bool]:
    """Compute recursive sizes and counts for a file tree without following symlinks."""
    size = 0
    allocated = 0
    files = 0
    dirs = 0
    inaccessible = False
    stack: List[str] = [path]
    while stack:
        current = stack.pop()
        st = safe_stat(current)
        if st is None:
            inaccessible = True
            continue
        if os.path.islink(current):
            files += 1
            size += getattr(st, "st_size", 0)
            allocated += getattr(st, "st_blocks", 0) * 512
            continue
        if not os.path.isdir(current):
            size += getattr(st, "st_size", 0)
            allocated += getattr(st, "st_blocks", 0) * 512
            files += 1
            continue
        dirs += 1
        try:
            with os.scandir(current) as it:
                for entry in it:
                    stack.append(entry.path)
        except Exception:
            inaccessible = True
    return size, allocated, files, dirs, inaccessible


def list_immediate_children(base_path: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        with os.scandir(base_path) as it:
            for entry in it:
                st = safe_stat(entry.path)
                if st is None:
                    items.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "isDir": False,
                            "sizeBytes": 0,
                            "allocatedBytes": 0,
                            "numFiles": 0,
                            "numDirs": 0,
                            "modified": 0.0,
                            "inaccessible": True,
                        }
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    size, allocated, files, dirs, bad = compute_entry_size(entry.path)
                    items.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "isDir": True,
                            "sizeBytes": int(size),
                            "allocatedBytes": int(allocated),
                            "numFiles": int(files),
                            "numDirs": max(0, int(dirs) - 1),
                            "modified": getattr(st, "st_mtime", 0.0),
                            "inaccessible": bad,
                        }
                    )
                else:
                    size = getattr(st, "st_size", 0)
                    allocated = getattr(st, "st_blocks", 0) * 512
                    items.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "isDir": False,
                            "sizeBytes": int(size),
                            "allocatedBytes": int(allocated),
                            "numFiles": 1,
                            "numDirs": 0,
                            "modified": getattr(st, "st_mtime", 0.0),
                            "inaccessible": False,
                        }
                    )
    except PermissionError:
        return items
    return items


def sort_items(items: List[Dict[str, Any]], key: str, order: str) -> List[Dict[str, Any]]:
    """Sort entries by the requested key and order."""
    reverse = order == "desc"
    if key == "size":
        return sorted(items, key=lambda x: (x.get("sizeBytes", 0), x.get("name", "")), reverse=reverse)
    if key == "allocated":
        return sorted(items, key=lambda x: (x.get("allocatedBytes", 0), x.get("name", "")), reverse=reverse)
    if key == "mtime":
        return sorted(items, key=lambda x: (x.get("modified", 0.0), x.get("name", "")), reverse=reverse)
    return sorted(items, key=lambda x: x.get("name", "").lower(), reverse=reverse)


def create_app() -> Flask:
    """Create and configure the Flask application instance."""
    app = Flask(__name__)
    app_logger = configure_logging()
    limiter = RateLimiter(capacity=40, refill_per_second=10.0)
    debug_ui = os.environ.get("DEBUG_UI", "").lower() in {"1", "true", "yes", "on"}

    @app.after_request
    def set_security_headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault("Cache-Control", "no-store")
        if request.headers.get("X-Forwarded-Proto", "http") == "https":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.route("/", methods=["GET"]) 
    def index() -> Response:
        unauth = require_auth(app_logger)
        if unauth is not None:
            return unauth
        lite = request.args.get("lite", "") == "1"
        nonce = secrets.token_urlsafe(16)
        csrf = generate_csrf_token()
        if lite:
            html = """
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>TreeSize Web</title>
    <style nonce=\"%%NONCE%%\">body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,'Helvetica Neue',Arial,'Noto Sans','Liberation Sans',sans-serif;margin:0;background:#f7f7f8;color:#111}header{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff;border-bottom:1px solid #e5e7eb;position:sticky;top:0;z-index:1}input[type=text]{width:520px;max-width:70vw;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px}button{padding:8px 12px;border:1px solid #0ea5e9;background:#0ea5e9;color:#fff;border-radius:6px;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}main{padding:12px 16px}table{width:100%;border-collapse:collapse;background:#fff}th,td{padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:left;font-size:14px}th.sortable{cursor:pointer;user-select:none}.bar{height:10px;background:#e5e7eb;border-radius:4px;position:relative}.bar>span{position:absolute;left:0;top:0;bottom:0;background:#10b981;border-radius:4px}.muted{color:#6b7280;font-size:12px}#errorBanner{display:none;background:#fee2e2;color:#991b1b;padding:8px 12px;border-bottom:1px solid #fecaca}</style>
  </head>
  <body>
    <div id=\"errorBanner\"></div>
    <header>
      <label for=\"base\">Base path</label>
      <input id=\"base\" type=\"text\" placeholder=\"/home/debber\"/>
      <button id=\"scanBtn\">Scan</button>
      <span id=\"status\" class=\"muted\"></span>
    </header>
    <main>
      <table id=\"results\">
        <thead>
          <tr>
            <th></th>
            <th class=\"sortable\" data-key=\"name\">Name</th>
            <th>Type</th>
            <th class=\"sortable\" data-key=\"size\">Size</th>
            <th class=\"sortable\" data-key=\"allocated\">Allocated</th>
            <th>Files</th>
            <th>Folders</th>
            <th>% of parent</th>
            <th class=\"sortable\" data-key=\"mtime\">Modified</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </main>
    <script nonce=\"%%NONCE%%\">const csrf=%%CSRF%%;document.cookie=`ts_csrf=${csrf}; Path=/; SameSite=Strict`;let sortKey='size';let sortOrder='desc';const baseEl=document.getElementById('base');const scanBtn=document.getElementById('scanBtn');const tbody=document.querySelector('#results tbody');const statusEl=document.getElementById('status');const errorBanner=document.getElementById('errorBanner');function setError(m){if(!m){errorBanner.style.display='none';errorBanner.textContent='';return}errorBanner.textContent=m;errorBanner.style.display='block'}document.querySelectorAll('th.sortable').forEach(th=>{th.addEventListener('click',()=>{const key=th.dataset.key;if(sortKey===key){sortOrder=sortOrder==='asc'?'desc':'asc'}else{sortKey=key;sortOrder='desc'}scan()})});scanBtn.addEventListener('click',scan);async function scan(){const basePath=baseEl.value.trim();if(!basePath){setError('Enter a base path.');return}scanBtn.disabled=true;statusEl.textContent='Scanning…';tbody.innerHTML='';setError('');try{const res=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({basePath,sort:sortKey,order:sortOrder})});if(!res.ok){const t=await res.text();throw new Error(t)}const data=await res.json();render(data);statusEl.textContent=`${data.length} items`}catch(e){statusEl.textContent='Error';setError(String(e))}finally{scanBtn.disabled=false}}function render(rows){const max=Math.max(1,...rows.map(r=>r.sizeBytes));tbody.innerHTML=rows.map(r=>`<tr><td><input type="checkbox" data-path="${r.path}"></td><td>${r.name}</td><td>${r.isDir?'Folder':'File'}</td><td>${formatBytes(r.sizeBytes)}</td><td>${formatBytes(r.allocatedBytes)}</td><td>${r.numFiles}</td><td>${r.numDirs}</td><td><div class="bar"><span style="width:${(r.sizeBytes/max*100).toFixed(2)}%"></span></div></td><td>${r.modifiedIso}</td><td><button data-action="delete" data-path="${r.path}">Delete</button></td></tr>`).join('');tbody.querySelectorAll('button[data-action=delete]').forEach(btn=>btn.addEventListener('click',()=>del([btn.dataset.path])))}function formatBytes(n){const u=['B','KB','MB','GB','TB','PB'];let i=0;let v=n;while(v>=1024&&i<u.length-1){v/=1024;i++}return`${v.toFixed(1)} ${u[i]}`}async function del(paths){const basePath=baseEl.value.trim();if(!basePath){setError('Enter base path first');return}if(!confirm(`Delete ${paths.length} item(s)?`))return;try{const res=await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({basePath,paths})});if(!res.ok){const t=await res.text();throw new Error(t)}await scan()}catch(e){setError(String(e))}}</script>
  </body>
</html>
"""
            html = html.replace("%%NONCE%%", nonce).replace("%%CSRF%%", json.dumps(csrf))
            resp = make_response(html)
            resp.set_cookie("ts_csrf", csrf, secure=False, httponly=False, samesite="Strict")
            resp.headers["Content-Security-Policy"] = (
                f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            )
            return resp
        html = f"""
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>TreeSize Web</title>
    {('<meta name=\\"debug-ui\\" content=\\"1\\">' if debug_ui else '')}
    <link rel=\"stylesheet\" href=\"/static/app.css\"> 
  </head>
  <body>
    <div id=\"errorBanner\"></div>
    <header>
      <label for=\"base\">Base path</label>
      <div id=\"breadcrumb\"></div>
      <input id=\"base\" type=\"text\" placeholder=\"/home/debber\" list=\"pathSuggestions\"/>
      <datalist id=\"pathSuggestions\"></datalist>
      <button id=\"scanBtn\">Scan</button>
      <span id=\"status\" class=\"muted\"></span>
    </header>
    <main>
      <table id=\"results\">
        <thead>
          <tr>
            <th></th>
            <th class=\"sortable\" data-key=\"name\">Name</th>
            <th>Type</th>
            <th class=\"sortable\" data-key=\"size\">Size</th>
            <th class=\"sortable\" data-key=\"allocated\">Allocated</th>
            <th>Files</th>
            <th>Folders</th>
            <th>% of parent</th>
            <th class=\"sortable\" data-key=\"mtime\">Modified</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <section id=\"debugPanel\" style=\"display:none\"></section>
    </main>
    <script src=\"/static/app.js\"></script>
  </body>
</html>
"""
        resp = make_response(html)
        resp.set_cookie("ts_csrf", csrf, secure=False, httponly=False, samesite="Strict")
        if debug_ui:
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            )
        else:
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            )
        return resp

    @app.route("/api/scan", methods=["POST"]) 
    def api_scan() -> Response:
        unauth = require_auth(app_logger)
        if unauth is not None:
            return unauth
        csrf_err = verify_csrf()
        if csrf_err is not None:
            return csrf_err
        key = f"scan:{get_client_identity()}"
        if not limiter.allow(key):
            return make_response(jsonify({"error": "Too many requests"}), 429)
        try:
            payload = request.get_json(force=True)
        except Exception:  # noqa: BLE001
            return make_response(jsonify({"error": "Invalid JSON"}), 400)
        base_path = str(payload.get("basePath", ""))
        sort_key = str(payload.get("sort", "size"))
        sort_order = str(payload.get("order", "desc"))
        try:
            real_base = ensure_valid_base(base_path)
        except Exception as exc:
            return make_response(jsonify({"error": str(exc)}), 400)
        if not is_within_base(real_base, real_base):
            return make_response(jsonify({"error": "Invalid base path"}), 400)
        items = list_immediate_children(real_base)
        total = sum(max(0, int(i.get("sizeBytes", 0))) for i in items) or 1
        enriched: List[Dict[str, Any]] = []
        for it in items:
            enriched.append(
                {
                    **it,
                    "percentOfParent": round(100.0 * max(0, int(it.get("sizeBytes", 0))) / total, 2),
                    "modifiedIso": format_ts(float(it.get("modified", 0.0))),
                }
            )
        ordered = sort_items(enriched, sort_key, sort_order)
        return make_response(jsonify(ordered))

    @app.route("/api/list-dir", methods=["GET"]) 
    def api_list_dir() -> Response:
        unauth = require_auth(app_logger)
        if unauth is not None:
            return unauth
        path = request.args.get("path", "/")
        try:
            real = normalize_path(path)
        except Exception:
            return make_response(jsonify({"error": "invalid path"}), 400)
        entries: List[Dict[str, Any]] = []
        try:
            with os.scandir(real) as it:
                for entry in it:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    accessible = True
                    try:
                        _ = entry.stat(follow_symlinks=False)
                    except Exception:
                        accessible = False
                    entries.append({
                        "name": entry.name,
                        "path": entry.path,
                        "type": "dir" if is_dir else "file",
                        "accessible": bool(accessible),
                    })
        except PermissionError:
            return make_response(jsonify({"error": "permission denied"}), 403)
        except FileNotFoundError:
            return make_response(jsonify({"error": "not found"}), 404)
        except NotADirectoryError:
            return make_response(jsonify({"error": "not a directory"}), 400)
        return make_response(jsonify({"entries": entries}))

    def attempt_delete_local(path: str) -> Optional[str]:
        """Attempt deletion without sudo, returning an error string on failure."""
        try:
            st = safe_stat(path)
            if st is None:
                return None
            if os.path.islink(path):
                os.unlink(path)
                return None
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
                    for name in files:
                        try:
                            p = os.path.join(root, name)
                            if os.path.islink(p):
                                os.unlink(p)
                            else:
                                os.unlink(p)
                        except Exception as e:
                            return str(e)
                    for name in dirs:
                        p = os.path.join(root, name)
                        try:
                            if os.path.islink(p):
                                os.unlink(p)
                            else:
                                os.rmdir(p)
                        except Exception as e:
                            return str(e)
                os.rmdir(path)
                return None
            os.unlink(path)
            return None
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return str(e)

    @app.route("/api/delete", methods=["POST"]) 
    def api_delete() -> Response:
        unauth = require_auth(app_logger)
        if unauth is not None:
            return unauth
        csrf_err = verify_csrf()
        if csrf_err is not None:
            return csrf_err
        key = f"delete:{get_client_identity()}"
        if not limiter.allow(key):
            return make_response(jsonify({"error": "Too many requests"}), 429)
        try:
            payload = request.get_json(force=True)
        except Exception:  # noqa: BLE001
            return make_response(jsonify({"error": "Invalid JSON"}), 400)
        base_path = str(payload.get("basePath", ""))
        paths = payload.get("paths", [])
        if not isinstance(paths, list) or not paths:
            return make_response(jsonify({"error": "paths must be a non-empty array"}), 400)
        try:
            real_base = ensure_valid_base(base_path)
        except Exception as exc:
            return make_response(jsonify({"error": str(exc)}), 400)
        results: List[Dict[str, Any]] = []
        for raw in paths:
            target = normalize_path(str(raw))
            if not is_within_base(real_base, target):
                results.append({"path": raw, "ok": False, "error": "outside base"})
                continue
            err = attempt_delete_local(target)
            if err is None:
                app_logger.info("deleted_local path=%s", target)
                results.append({"path": raw, "ok": True, "via": "local"})
                continue
            helper = "/usr/local/bin/treesize-delete-helper"
            try:
                import subprocess

                completed = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        helper,
                        "--base",
                        real_base,
                        "--path",
                        target,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                ok = completed.returncode == 0
                if ok:
                    app_logger.info("deleted_sudo path=%s", target)
                    results.append({"path": raw, "ok": True, "via": "sudo"})
                else:
                    results.append(
                        {
                            "path": raw,
                            "ok": False,
                            "error": completed.stderr.strip() or completed.stdout.strip() or "helper_failed",
                        }
                    )
            except Exception as exc:
                results.append({"path": raw, "ok": False, "error": str(exc)})
        return make_response(jsonify({"results": results}))

    @app.route("/api/csrf-debug", methods=["GET"]) 
    def api_csrf_debug() -> Response:
        unauth = require_auth(app_logger)
        if unauth is not None:
            return unauth
        cookie = request.cookies.get("ts_csrf", "")
        auth_header = request.headers.get("Authorization", "")
        parsed = parse_basic_auth(auth_header)
        user = parsed[0] if parsed else ""
        return make_response(jsonify({"cookieToken": cookie, "headerRequired": True, "user": user}))

    return app


def main() -> None:
    """Run the development server."""
    app = create_app()
    host = os.environ.get("TREESIZE_HOST", "0.0.0.0")
    port_str = os.environ.get("TREESIZE_PORT", "5327")
    try:
        port = int(port_str)
    except Exception:  # noqa: BLE001
        port = 5327
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()


