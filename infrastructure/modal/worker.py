"""The only Modal Function input is one trusted Sandbox Connect origin and token."""
import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_origin(origin):
    parts = urllib.parse.urlsplit(origin)
    hostname = parts.hostname or ""
    if (parts.scheme != "https" or not hostname.endswith((".modal.host", ".modal.run"))
            or parts.netloc != hostname or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Unexpected Sandbox Connect origin")
    return origin.rstrip("/")


def request(client, origin, path, headers, data=None, on_response=None):
    try:
        with client.open(urllib.request.Request(origin + path, data=data, headers=headers), timeout=5) as response:
            if on_response is not None:
                on_response(response.headers)
            return response.status, response.read(65_536)
    except urllib.error.HTTPError as failure:
        with failure:
            return failure.code, b""


def auth_headers(token, gate_key):
    return {"Authorization": f"Bearer {token}", "X-SDU-Benchmark-Key": gate_key}


def safe_log(text, *values):
    for value in values:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text[:24_000]


def run_smoke(origin, token):
    origin = validate_origin(origin)
    gate_key = os.environ["SDU_GATE_KEY"]
    password = os.environ["SDU_STUDENT_PASSWORD"]
    headers = auth_headers(token, gate_key)
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    security = {
        "without_connect_token": request(client, origin, "/api/v1/auth/csrf", {"X-SDU-Benchmark-Key": gate_key})[0],
        "without_gateway_key": request(client, origin, "/api/v1/auth/csrf", {"Authorization": f"Bearer {token}"})[0],
        "without_student_session": request(client, origin, "/api/v1/me", headers)[0],
        "readiness_hidden": request(client, origin, "/actuator/health/readiness", headers)[0],
        "metrics_hidden": request(client, origin, "/actuator/prometheus", headers)[0],
    }
    output = {"security": security, "region": os.environ.get("MODAL_REGION"), "stage": "access-validation"}
    if (security["without_connect_token"] not in {401, 403, 404}
            or security["without_gateway_key"] != 403 or security["without_student_session"] != 401
            or security["readiness_hidden"] != 404 or security["metrics_hidden"] != 404):
        return output

    def login(index):
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                              urllib.request.HTTPCookieProcessor(jar))
        status, body = request(browser, origin, "/api/v1/auth/csrf", headers)
        if status != 200:
            return None, f"initial_csrf_{status}"
        csrf = json.loads(body)
        data = urllib.parse.urlencode({"username": f"cloud-smoke-{index:02}", "password": password}).encode()
        status, _ = request(browser, origin, "/api/v1/auth/login", {**headers, csrf["headerName"]: csrf["token"],
                            "Content-Type": "application/x-www-form-urlencoded"}, data)
        if status != 200:
            return None, f"login_{status}"
        status, body = request(browser, origin, "/api/v1/auth/csrf", headers)
        if status != 200:
            return None, f"refresh_csrf_{status}"
        refreshed = json.loads(body)
        cookie = next((value for value in jar if value.name == "SESSION" and value.secure), None)
        if cookie is None:
            return None, "secure_cookie_missing"
        return {"cookie": cookie.value, "header": refreshed["headerName"], "csrf": refreshed["token"]}, None

    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            prepared = list(pool.map(login, range(1, 11)))
    except (OSError, ValueError, KeyError, TypeError):
        return {**output, "stage": "session-preparation", "preparation_error": "transport_or_response_validation"}
    sessions = [session for session, _ in prepared if session]
    output.update(stage="session-preparation", login_successes=len(sessions),
                  login_seconds=round(time.monotonic() - started, 3),
                  login_failures=[failure for _, failure in prepared if failure])
    if len(sessions) != 10 or len({session["cookie"] for session in sessions}) != 10:
        return output

    with tempfile.TemporaryDirectory(prefix="sdu-modal-") as directory:
        work = Path(directory)
        path = work / "sessions.json"
        path.write_text(json.dumps(sessions))
        path.chmod(0o600)
        summary = work / "summary.json"
        raw = work / "raw.json.gz"
        binary = work / "k6"
        # Mounted files are read-only and Modal can normalize their execute mode.
        binary.write_bytes(Path("/opt/sdu/k6").read_bytes())
        binary.chmod(0o700)
        env = {"PATH": "/usr/bin:/bin", "K6_NO_USAGE_REPORT": "true",
               "SDU_SESSIONS": str(path), "SDU_SUMMARY": str(summary), "SDU_ORIGIN": origin,
               "SDU_CONNECT": token, "SDU_GATE_KEY": gate_key}
        try:
            result = subprocess.run([str(binary), "run", "--quiet", "--no-usage-report", "--out", f"json={raw}",
                                     "/opt/sdu/smoke.js"], env=env, cwd=directory, capture_output=True, timeout=36)
        except subprocess.TimeoutExpired:
            return {**output, "stage": "k6-timeout"}
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        sanitized = safe_log((result.stdout + result.stderr).decode(errors="replace"), origin, token, gate_key, password,
                             *(value for session in sessions for value in (session["cookie"], session["csrf"])))
        if raw.exists() and raw.stat().st_size > 512 * 1024:
            return {**output, "stage": "artifact-limit"}
        return {**output, "stage": "k6-finished", "k6_exit": result.returncode,
                "summary": json.loads(summary.read_bytes()) if summary.exists() else None,
                "raw": raw.read_bytes() if raw.exists() else None, "k6_log": sanitized,
                "k6_cpu_seconds": round(usage.ru_utime + usage.ru_stime, 5),
                "k6_peak_rss_mib": round(usage.ru_maxrss / 1024, 3)}
