"""Single Function worker. Session secrets and Connect credentials stay in memory or its temp filesystem."""
import concurrent.futures
from collections import Counter
import gzip
import http.cookiejar
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request

from bench_config import Case, BURST_LIMIT_SECONDS, DRAIN_SECONDS, STEADY_SECONDS
from bench_evidence import phase_metrics
from mixed_recovery import recover
from worker import NoRedirect, auth_headers, request, safe_log, validate_origin


def opener(jar=None):
    handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def verify_access(origin, token, gate_key):
    client = opener()
    headers = auth_headers(token, gate_key) if token else {"X-SDU-Benchmark-Key": gate_key}
    checks = {
        "without_connect_token": request(client, origin, "/api/v1/auth/csrf", {"X-SDU-Benchmark-Key": gate_key})[0] if token else None,
        "without_gateway_key": request(client, origin, "/api/v1/auth/csrf", {"Authorization": f"Bearer {token}"} if token else {})[0],
        "without_student_session": request(client, origin, "/api/v1/me", headers)[0],
        "readiness_hidden": request(client, origin, "/actuator/health/readiness", headers)[0],
        "metrics_hidden": request(client, origin, "/actuator/prometheus", headers)[0],
        "administration_hidden": request(client, origin, "/api/v1/admin/benchmark-authorize", headers)[0],
    }
    passed = ((not token or checks["without_connect_token"] in (401, 403, 404)) and checks["without_gateway_key"] == 403
              and checks["without_student_session"] == 401
              and checks["readiness_hidden"] == checks["metrics_hidden"] == checks["administration_hidden"] == 404)
    return checks, passed


def verify_access_with_dns_retry(origin, token, gate_key):
    for retry in range(4):
        try:
            checks, allowed = verify_access(origin, token, gate_key)
            return {**checks, "preflight_dns_retries": retry}, allowed
        except (socket.gaierror, urllib.error.URLError) as error:
            if not isinstance(getattr(error, "reason", error), socket.gaierror):
                raise
            if retry == 3:
                return {"preflight_dns_retries": retry, "dns_resolution_failed": True}, False
            time.sleep(2 ** retry)


def session_smoke(origin, headers, password):
    jar = http.cookiejar.CookieJar()
    client = opener(jar)
    def session():
        return next((cookie.value for cookie in jar if cookie.name == "SESSION" and cookie.secure), None)
    status, body = request(client, origin, "/api/v1/auth/csrf", headers)
    before = session()
    if status != 200 or not before:
        return {"csrf": status, "passed": False}
    token = json.loads(body)
    data = urllib.parse.urlencode({"username": "cloud-bench-001", "password": password}).encode()
    form = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    denied, _ = request(client, origin, "/api/v1/auth/login", form, data)
    login, _ = request(client, origin, "/api/v1/auth/login", {**form, token["headerName"]: token["token"]}, data)
    me, _ = request(client, origin, "/api/v1/me", headers)
    stale, _ = request(opener(), origin, "/api/v1/me", {**headers, "Cookie": f"SESSION={before}"})
    rotated = session() is not None and session() != before
    return {"csrf": status, "csrf_denied": denied, "login": login, "authenticated": me, "stale_cookie": stale,
            "rotated": rotated, "passed": denied == 403 and login == me == 200 and stale == 401 and rotated}


def prepare_actors(origin, headers, password, case):
    deadline = time.monotonic() + case.prep_seconds

    class PreparationFailure(Exception):
        def __init__(self, reason, retryable=False):
            self.reason = reason
            self.retryable = retryable

    def prepare_once(job):
        index, login = job
        jar = http.cookiejar.CookieJar()
        client = opener(jar)
        login_name = f"cloud-bench-{index:03}"
        student = f"80000000-0000-4000-8000-{index:012}"
        section = f"90000000-0000-4000-8000-{(index - 1) % 20 + 1:012}"
        instances = {}

        def call(label, path, request_headers, payload=None):
            if time.monotonic() >= deadline:
                raise PreparationFailure("deadline")
            try:
                def record_instance(response_headers):
                    instance = response_headers.get("X-SDU-Backend-Instance")
                    if instance in ("app1", "app2", "app3"):
                        instances[label] = instance
                status, body = request(client, origin, path, request_headers, payload,
                                       record_instance if case.instances is not None else None)
            except OSError as error:
                raise PreparationFailure(f"{label}_transport", True) from error
            if status != 200:
                raise PreparationFailure(f"{label}_http_{status}", status in (502, 503, 504))
            return body

        def csrf(label):
            value = json.loads(call(label, "/api/v1/auth/csrf", headers))
            if value.get("headerName") != "X-CSRF-TOKEN" or not isinstance(value.get("token"), str):
                raise PreparationFailure(f"{label}_contract")
            return value

        token = csrf("initial_csrf")
        if login:
            data = urllib.parse.urlencode({"username": login_name, "password": password}).encode()
            response = call("login", "/api/v1/auth/login", {**headers, token["headerName"]: token["token"],
                            "Content-Type": "application/x-www-form-urlencoded"}, data)
            if json.loads(response).get("studentId") != student:
                raise PreparationFailure("login_contract")
            token = csrf("authenticated_csrf")
            if case.instances is not None:
                if json.loads(call("authenticated_me", "/api/v1/me", headers)).get("studentId") != student:
                    raise PreparationFailure("cross_instance_identity_mismatch")
                if not instances.get("login") or not instances.get("authenticated_me"):
                    raise PreparationFailure("backend_instance_header_missing")
        cookie = next((item.value for item in jar if item.name == "SESSION" and item.secure), None)
        if not cookie:
            raise PreparationFailure("missing_secure_session")
        return {"cookie": cookie, "header": token["headerName"], "csrf": token["token"],
                "student": student, "section": section, "login": login_name,
                "cross_instance": bool(instances and instances.get("login") != instances.get("authenticated_me"))}

    def prepare(job):
        attempts = []
        for attempt in range(2):
            try:
                return prepare_once(job), attempts
            except PreparationFailure as error:
                attempts.append(error.reason)
                if attempt or not error.retryable:
                    break
                time.sleep(.25)
            except (ValueError, KeyError, TypeError, AttributeError):
                attempts.append("invalid_authentication_response")
                break
        return None, attempts

    anonymous = case.profile == "login-burst"
    jobs = [(index, not anonymous) for index in range(1, case.vus + 1)]
    if anonymous:
        jobs += [(index, True) for index in range(201, 206)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(prepare, jobs))
    actors = [actor for actor, _ in results]
    valid = all(actors) and len({item["cookie"] for item in actors}) == len(actors)
    counts = {"authenticated": sum(item is not None and login for item, (_, login) in zip(actors, jobs)),
              "anonymous": sum(item is not None and not login for item, (_, login) in zip(actors, jobs)),
              "expected": len(jobs),
              "attempt_errors": dict(Counter(reason for _, failures in results for reason in failures)),
              "recovered_actors": sum(actor is not None and bool(failures) for actor, failures in results)}
    if case.instances is not None:
        counts["cross_instance_sessions"] = sum(bool(actor and actor["cross_instance"]) for actor in actors)
        if case.instances > 1 and not counts["cross_instance_sessions"]:
            return None, None, counts
    if not valid:
        return None, None, counts
    return (actors[case.vus:] if anonymous else actors), actors[:case.vus], counts


def cgroup_value(name, field=None):
    root = Path("/sys/fs/cgroup")

    def read(path):
        return (root / path).read_text().strip()

    def statistic(path, key):
        return next(line.split()[1] for line in read(path).splitlines() if line.startswith(key + " "))

    try:
        value = statistic(name, field) if field else read(name)
        if name == "cpu.max":
            maximum, period = value.split()
            if maximum == "max" or maximum.isdecimal():
                return {"quota_usec": int(maximum) if maximum.isdecimal() else None, "period_usec": int(period)}
        return int(value)
    except (OSError, ValueError, StopIteration, IndexError):
        try:
            if name == "cpu.max":
                quota = int(read("cpu/cpu.cfs_quota_us"))
                return {"quota_usec": quota if quota > 0 else None, "period_usec": int(read("cpu/cpu.cfs_period_us"))}
            if name == "memory.max":
                return int(read("memory/memory.limit_in_bytes"))
            if name == "memory.peak":
                return int(read("memory/memory.max_usage_in_bytes"))
            if field == "usage_usec":
                return int(read("cpuacct/cpuacct.usage")) // 1000
            if field == "throttled_usec":
                return int(statistic("cpu/cpu.stat", "throttled_time")) // 1000
        except (OSError, ValueError, StopIteration, IndexError):
            return None


def warmup_gate(case, payload):
    metrics = phase_metrics(payload, case.warmup_seconds, case.ramp_elapsed)
    recovery = payload.get("recovery") or {}
    checks = {
        "k6_completed": payload.get("exit") == 0,
        "full_target_window": metrics["duration_seconds"] == case.warmup_seconds
        and metrics["window_started_ms"] is not None and metrics["window_ended_ms"] is not None,
        "target_vus": metrics["observed_vus_peak"] == case.vus,
        "classified_requests": bool(metrics["requests"]),
        "zero_unexpected": metrics["errors"] == metrics["all_unexpected"] == 0,
        "zero_interrupted": metrics["interrupted_iterations"] == 0 and (metrics["dropped_iterations"] or 0) == 0,
        "latency_slo": metrics["p95_ms"] is not None and metrics["p95_ms"] < 1000
        and metrics["p99_ms"] is not None and metrics["p99_ms"] <= 2000,
        "paced_iterations": (metrics["offered_iterations"] or 0) >= .95 * case.vus * case.warmup_seconds / 1.25,
        "recovery_accounted_for": payload.get("ledger") is not None
        and recovery.get("pending") is not None and recovery.get("pending") == recovery.get("resolved"),
    }
    levels = []
    if case.diagnostic_only:
        samples = ((payload.get("summary") or {}).get("metrics") or {})
        for level, offset in ((1500, 36), (2000, 63)):
            def value(metric, name):
                return samples.get(f"cloud_ladder_{metric}{{level:{level}}}", {}).get("values", {}).get(name)
            duration = metrics["k6_runtime_seconds"]
            requests, errors = value("requests", "count"), value("errors", "count")
            p95, p99, iterations = value("latency", "p(95)"), value("latency", "p(99)"), value("iterations", "count")
            complete = duration is not None and duration >= offset + 15
            passed = (complete and bool(requests) and errors == 0 and p95 is not None and p95 < 1000
                      and p99 is not None and p99 <= 2000 and (iterations or 0) >= .95 * level * 15 / 1.25)
            levels.append({"vus": level, "complete": complete, "requests": requests, "errors": errors,
                           "p95_ms": p95, "p99_ms": p99, "offered_iterations": iterations, "passed": bool(passed)})
        checks["lower_plateaus"] = all(level["passed"] for level in levels)
    gate = {"passed": all(checks.values()), "checks": checks, "levels": levels,
            "window_seconds": metrics["duration_seconds"], "unexpected": metrics["errors"],
            "unexpected_including_ramp_and_drain": metrics["all_unexpected"]}
    if case.tomcat_threads is not None and case.section_read_experiment is None and case.jdbc_security_pool is None:
        # Preserve the qualification result while allowing a bounded observation of a pace-only miss.
        gate["diagnostic_measurement_allowed"] = (
            not checks["paced_iterations"] and all(value for key, value in checks.items() if key != "paced_iterations")
            and (metrics["offered_iterations"] or 0) >= .90 * case.vus * case.warmup_seconds / 1.25)
    return gate


def run_benchmark(origin, token, profile, vus, warmup_seconds, pool_size=10, transport="connect-token",
                  arrival_rate=None, spread_start=False, ramp_seconds=0, prefill_pool=False, reconcile=False, ramp_start_vus=50,
                  hot_mixed=False, backend_instances=None, database_path=None, catalog_path=None, resource_profile=None,
                  private_db_sidecar=False, http_experiment=None, diagnostic_only=False, session_storage=None,
                  sidecar_cpu_cores=None, jvm_threads=None, joined_academic_reads=False, hardware=None, tomcat_threads=None,
                  section_read_experiment=None, jdbc_security_pool=None, single_phase=None, prepared_actors=None, defer_recovery=False):
    if single_phase not in (None, "warmup", "measure") or (prepared_actors is not None and single_phase != "measure"):
        raise ValueError("Invalid isolated phase")
    case = Case(profile, vus, warmup_seconds, pool_size, transport, arrival_rate, spread_start, ramp_seconds, prefill_pool,
                diagnose_mixed=reconcile, ramp_start_vus=ramp_start_vus, hot_mixed=hot_mixed,
                backend_instances=backend_instances, database_path=database_path, catalog_path=catalog_path,
                resource_profile=resource_profile, split_database=resource_profile is not None,
                private_db_sidecar=private_db_sidecar, http_experiment=http_experiment, diagnostic_only=diagnostic_only,
                session_storage=session_storage, sidecar_cpu_cores=sidecar_cpu_cores, jvm_threads=jvm_threads,
                joined_academic_reads=joined_academic_reads, hardware=hardware, tomcat_threads=tomcat_threads,
                section_read_experiment=section_read_experiment, jdbc_security_pool=jdbc_security_pool)
    if bool(token) != (transport == "connect-token"):
        raise ValueError("Transport authentication is inconsistent")
    origin = validate_origin(origin)
    gate_key = os.environ["SDU_GATE_KEY"]
    password = os.environ["SDU_STUDENT_PASSWORD"]
    cgroup_start = cgroup_value("cpu.stat", "usage_usec")
    throttled_start = cgroup_value("cpu.stat", "throttled_usec")
    security, allowed = verify_access_with_dns_retry(origin, token, gate_key) if case.session_storage else verify_access(origin, token, gate_key)
    output = {"security": security, "region": os.environ.get("MODAL_REGION"), "stage": "access-validation"}
    if not allowed or output["region"] != "us-east":
        return output
    if case.private_scaled:
        quota = cgroup_value("cpu.max") or {}
        memory = cgroup_value("memory.max")
        output["resources"] = {"generator_cpu_max": quota, "generator_memory_max_bytes": memory}
        if (not quota.get("period_usec") or quota.get("quota_usec") != quota["period_usec"] * case.generator_cpu
                or memory != case.generator_cpu * 1024**3):
            output["security"]["generator_quota_matches"] = False
            return output
        output["security"]["generator_quota_matches"] = True
    started = time.monotonic()
    headers = auth_headers(token, gate_key) if token else {"X-SDU-Benchmark-Key": gate_key}
    if case.session_storage:
        try:
            output["session_smoke"] = session_smoke(origin, headers, password)
        except (OSError, ValueError, KeyError):
            output["session_smoke"] = {"passed": False}
        if not output["session_smoke"]["passed"]:
            output["stage"] = "session-smoke"
            return output
    if prepared_actors is None:
        warm_actors, timed_actors, counts = prepare_actors(origin, headers, password, case)
    else:
        if len(prepared_actors) != vus:
            raise ValueError("Incomplete prepared actors")
        warm_actors = timed_actors = prepared_actors
        counts = {"authenticated": vus, "expected": vus, "reused_prepared_sessions": True}
    output.update(stage="session-preparation", preparation=counts, preparation_seconds=round(time.monotonic() - started, 3))
    if warm_actors is None:
        return output
    redact = [origin, token, gate_key, password]
    for actor in warm_actors + timed_actors:
        redact.extend((actor["cookie"], actor["csrf"]))

    with tempfile.TemporaryDirectory(prefix="sdu-phase8c-") as directory:
        work = Path(directory)
        binary = work / "k6"
        shutil.copyfile("/opt/sdu/k6", binary)
        binary.chmod(0o700)
        for phase, actors, bound in (
            ("warmup", warm_actors[:case.report()["warmup_vus"]], case.ramp_elapsed + case.warmup_seconds + DRAIN_SECONDS),
            ("measure", timed_actors, STEADY_SECONDS + DRAIN_SECONDS if case.timed else BURST_LIMIT_SECONDS),
        ):
            if single_phase is not None and single_phase != phase:
                continue
            path = work / f"{phase}-sessions.json"
            path.touch(mode=0o600)
            path.write_text(json.dumps(actors))
            summary = work / f"{phase}-summary.json"
            env = {"PATH": "/usr/bin:/bin", "K6_NO_USAGE_REPORT": "true", "SDU_SESSIONS": str(path),
                   "SDU_SUMMARY": str(summary), "SDU_ORIGIN": origin, "SDU_CONNECT": token, "SDU_GATE_KEY": gate_key,
                   "SDU_STUDENT_PASSWORD": password if phase == "measure" and profile == "login-burst" else "",
                   "SDU_PHASE": phase, "SDU_PROFILE": json.dumps({**case.report(), "reconcile": reconcile})}
            output["stage"] = phase
            log = work / f"{phase}.log"
            before = resource.getrusage(resource.RUSAGE_CHILDREN)
            phase_started = time.monotonic()
            with log.open("wb") as sink:
                try:
                    result = subprocess.run([str(binary), "run", "--quiet", "--no-usage-report", "/opt/sdu/benchmark.js"],
                                            env=env, cwd=work, stdout=sink, stderr=subprocess.STDOUT, timeout=bound + 5)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = None
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
            with log.open("rb") as source:
                source.seek(max(log.stat().st_size - 24000, 0))
                text = safe_log(source.read().decode(errors="replace"), *redact)
            output[phase] = {"exit": code, "summary": json.loads(summary.read_bytes())
                             if summary.exists() and summary.stat().st_size < 256 * 1024 else None, "log": text,
                             "generator_cpu_seconds": round(cpu, 4),
                             "generator_cpu_cores": round(cpu / (time.monotonic() - phase_started), 4)}
            ledger_limit_mib = 96 if case.private_scaled else 40
            if reconcile and log.stat().st_size <= ledger_limit_mib * 1024 * 1024:
                with log.open("rb") as source:
                    rows = [line.split(b'msg="', 1)[1].split(b'"', 1)[0]
                            for line in source if b'msg="SDU_START ' in line or b'msg="SDU_RESULT ' in line]
                output[phase]["ledger"] = gzip.compress(b"\n".join(rows), compresslevel=3)
                if defer_recovery:
                    payload, recovery = None, {"deferred_until_projection_drain":True}
                else:
                    payload, recovery = recover(output[phase]["ledger"], actors, origin, headers, opener, phase,
                        **({'max_journeys':2500, 'recovery_seconds':60} if single_phase is not None else {}))
                output[phase].update(recovery=recovery, recovery_ledger=payload)
            path.unlink()
            if phase == "warmup" and case.private_scaled:
                output["warmup_gate"] = warmup_gate(case, output[phase])
                if not (output["warmup_gate"]["passed"] or output["warmup_gate"].get("diagnostic_measurement_allowed")):
                    output["stage"] = "warmup-gate"
                    break
                if case.diagnostic_only:
                    output["stage"] = "diagnostic-complete"
                    break
            if code != 0:
                break

    k6 = resource.getrusage(resource.RUSAGE_CHILDREN)
    wrapper = resource.getrusage(resource.RUSAGE_SELF)
    cgroup_end = cgroup_value("cpu.stat", "usage_usec")
    memory = cgroup_value("memory.peak")
    throttled_end = cgroup_value("cpu.stat", "throttled_usec")
    output["resources"] = {
        "k6_cpu_seconds": round(k6.ru_utime + k6.ru_stime, 5),
        "k6_peak_rss_mib": round(k6.ru_maxrss / 1024, 3),
        "generator_worker_cpu_seconds": round(wrapper.ru_utime + wrapper.ru_stime, 5),
        "generator_worker_peak_rss_mib": round(wrapper.ru_maxrss / 1024, 3),
        "generator_cpu_seconds": (cgroup_end - cgroup_start) / 1e6
        if cgroup_start is not None and cgroup_end is not None and cgroup_end >= cgroup_start else None,
        "generator_peak_memory_mib": round(memory / 2**20, 3) if memory is not None else None,
        "generator_throttled_seconds": (throttled_end - throttled_start) / 1e6
        if throttled_start is not None and throttled_end is not None and throttled_end >= throttled_start else None,
        "generator_cpu_max": cgroup_value("cpu.max"),
        "generator_memory_max_bytes": cgroup_value("memory.max"),
    }
    if single_phase == "warmup" or defer_recovery:
        # Private Function return only. Caller must strip before saving any report/artifact.
        output["_prepared_actors"] = timed_actors
    return output
