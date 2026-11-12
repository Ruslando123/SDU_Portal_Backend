"""Run one fixed, authenticated Modal smoke. The app and disk are ephemeral."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tarfile
import time

import modal

from prepare import ASSETS, JAR, JRE_SHA, K6_SHA, ROOT, sha
from worker import safe_log, validate_origin

HERE = Path(__file__).resolve().parent
RUNS = ROOT / "infrastructure/.local/modal/runs"
PG_IMAGE = "postgres:18.6-bookworm@sha256:02fb3245e038f33536cf13bc6c4e59f21d07b0cea3c1fe1c07c6e8aa8f083bcc"
PY_IMAGE = "python:3.12-slim-bookworm@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4"
REGION = "us-east"
SB_CPU, SB_MIB, SB_SECONDS = 1.5, 2048, 240
K6_CPU, K6_MIB, K6_SECONDS, K6_STARTUP, K6_IDLE = 0.5, 512, 70, 45, 5
RATE_KEYS = ("cpu_hour_cost", "mem_gib_hour_cost", "cpu_hour_cost_sandbox", "mem_gib_hour_cost_sandbox", "egress_gib_cost")
VIOLATIONS = ("capacity_or_ledger", "duplicate_section", "duplicate_offering", "credit", "required_meeting",
              "nonterminal_outcome", "missing_enrollment_audit", "missing_withdrawal_audit")
EXPECTED = {"active": 0, "withdrawn": 10, "students": 10, "section_counter": 0,
            "enrolled_outcomes": 10, "withdrawn_outcomes": 10, "audit_events": 20}


def now():
    return datetime.now(timezone.utc).isoformat()


def cost_plan(rates):
    rates = {key: Decimal(str(rates[key])) for key in RATE_KEYS}
    if any(not value.is_finite() or value <= 0 for value in rates.values()):
        raise ValueError("Modal pricing returned an invalid rate")

    def cap(cpu, mib, seconds, cpu_rate, mem_rate):
        return (Decimal(str(cpu)) * cpu_rate + Decimal(mib) / 1024 * mem_rate) * seconds / 3600 * Decimal("1.75")

    backend = cap(SB_CPU, SB_MIB, SB_SECONDS, rates["cpu_hour_cost_sandbox"], rates["mem_gib_hour_cost_sandbox"])
    generator = cap(K6_CPU, K6_MIB, K6_SECONDS + K6_STARTUP + K6_IDLE,
                    rates["cpu_hour_cost"], rates["mem_gib_hour_cost"])
    egress = rates["egress_gib_cost"]  # One GiB allowance for the fixed sub-MiB smoke and its artifacts.
    return {"rates": {k: str(v) for k, v in rates.items()}, "regional_multiplier": 1.75,
            "sandbox_compute_usd": float(backend), "generator_compute_usd": float(generator),
            "compute_usd": float(backend + generator), "egress_allowance_usd": float(egress),
            "estimate_usd": float(backend + generator + egress), "actual_usd": None}


def cli_json(*args):
    result = subprocess.run([sys.executable, "-m", "modal", *args, "--json"], capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


def assets_valid():
    for path in (JAR, ASSETS / "jre.tar.gz", ASSETS / "k6.tar.gz", ASSETS / "k6", ASSETS / "gateway"):
        if not path.is_file():
            raise RuntimeError("Run prepare.py before requesting cloud resources")
    if sha(ASSETS / "jre.tar.gz") != JRE_SHA or sha(ASSETS / "k6.tar.gz") != K6_SHA:
        raise RuntimeError("Prepared archive checksum mismatch")
    with tarfile.open(ASSETS / "k6.tar.gz") as archive:
        with archive.extractfile("k6-v2.3.0-linux-amd64/k6") as binary:
            if hashlib.file_digest(binary, "sha256").hexdigest() != sha(ASSETS / "k6"):
                raise RuntimeError("Prepared k6 executable checksum mismatch")


def images(backend_base=None):
    backend = backend_base if backend_base is not None else modal.Image.from_registry(PG_IMAGE)
    for source, target in (
        (ASSETS / "jre.tar.gz", "jre.tar.gz"), (ASSETS / "gateway", "gateway"), (JAR, "backend.jar"),
        (HERE / "start-sandbox.sh", "start.sh"), (HERE / "seed.sql", "seed.sql"),
        (ROOT / "infrastructure/synthetic-demo.sql", "demo.sql"),
        (ROOT / "backend/verification/phase5-invariants.sql", "invariants.sql"),
    ):
        backend = backend.add_local_file(source, f"/opt/sdu/{target}")
    generator = modal.Image.from_registry(PY_IMAGE)
    for source, target in (
        (ASSETS / "k6", "k6"), (HERE / "worker.py", "worker.py"), (HERE / "cloud-smoke.js", "smoke.js"),
        (ROOT / "infrastructure/k6/lib/api.js", "lib/api.js"),
    ):
        generator = generator.add_local_file(source, f"/opt/sdu/{target}")
    return backend, generator


def save_json(path, document):
    staging = path.with_suffix(".tmp")
    with staging.open("w") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")
    staging.replace(path)


def execute(sandbox, *args, timeout=15, secret=None):
    process = sandbox.exec(*args, timeout=timeout, secrets=[secret] if secret else None)
    stdout = process.stdout.read()
    stderr = process.stderr.read()
    return process.wait(), stdout, stderr


def query(sandbox, statement, secret):
    status, output, _ = execute(sandbox, "bash", "-c",
        'PGPASSWORD="$DB_MIGRATION_PASSWORD" exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U benchmark_migrator -d portal_cloud_smoke "$@"',
        "cloud-invariants", *statement, secret=secret)
    if status != 0:
        raise RuntimeError("Independent PostgreSQL assertion query failed")
    return output.strip()


def sql_verification(sandbox, secret):
    rows = query(sandbox, ["-f", "/opt/sdu/invariants.sql"], secret).splitlines()
    if len(rows) < len(VIOLATIONS):
        raise RuntimeError("Incomplete PostgreSQL invariant results")
    violations = dict(zip(VIOLATIONS, (int(row) for row in rows[:len(VIOLATIONS)]), strict=True))
    values = json.loads(query(sandbox, ["-c", """SELECT json_build_object(
        'active',(SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'),
        'withdrawn',(SELECT count(*) FROM enrollment.enrollments WHERE status='WITHDRAWN'),
        'students',(SELECT count(DISTINCT student_id) FROM enrollment.enrollments),
        'section_counter',(SELECT enrolled_count FROM sections.course_sections WHERE section_code='DEMO'),
        'enrolled_outcomes',(SELECT count(*) FROM operations.idempotency_records WHERE response_code='ENROLLED'),
        'withdrawn_outcomes',(SELECT count(*) FROM operations.idempotency_records WHERE response_code='WITHDRAWN'),
        'audit_events',(SELECT count(*) FROM audit.audit_events));"""], secret))
    database = json.loads(query(sandbox, ["-c", """SELECT json_build_object(
        'server_version',current_setting('server_version'),'listen_addresses',current_setting('listen_addresses'),
        'fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),
        'full_page_writes',current_setting('full_page_writes'),
        'flyway_versions',(SELECT string_agg(version,',' ORDER BY installed_rank) FROM flyway_schema_history WHERE success));"""], secret))
    durable = all(database[key] == "on" for key in ("fsync", "synchronous_commit", "full_page_writes"))
    passed = (not any(violations.values()) and values == EXPECTED and durable and database["listen_addresses"] == "127.0.0.1"
              and database["flyway_versions"] == "1,2,3,4,5,6,7,8")
    return {"passed": passed, "violations": violations, "observed": values, "expected": EXPECTED, "database": database}


def metrics_from(result):
    summary = result.get("summary") or {}
    def value(metric, statistic):
        return summary.get("metrics", {}).get(metric, {}).get("values", {}).get(statistic)
    return {"vus": 10, "duration_seconds": summary.get("state", {}).get("testRunDurationMs", 0) / 1000 or None,
            "requests": value("http_reqs", "count"), "rps": value("http_reqs", "rate"),
            "p95_ms": value("http_req_duration", "p(95)"), "p99_ms": value("http_req_duration", "p(99)"),
            "error_rate": value("cloud_unexpected", "rate"), "errors": value("cloud_unexpected", "passes"),
            "completed_journeys": value("cloud_journeys", "count"), "login_successes": result.get("login_successes")}


def collect_logs(sandbox, directory, redactions):
    for label, path in (("backend", "/tmp/sdu-cloud/backend.log"),
                        ("postgres", "/tmp/sdu-cloud/pg/server.log"),
                        ("postgres-init", "/tmp/sdu-cloud/postgres-init.log"),
                        ("seed", "/tmp/sdu-cloud/seed.log"), ("gateway", "/tmp/sdu-cloud/gateway.log")):
        try:
            status, output, _ = execute(sandbox, "tail", "-c", "20000", path, timeout=5)
            if status == 0:
                (directory / f"{label}.log").write_text(safe_log(output, *redactions))
        except Exception:
            continue
    try:
        status, raw, _ = execute(sandbox, "bash", "-c",
            'for f in /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.peak; do if [[ -r "$f" ]]; then printf "%s\n" "$f"; cat "$f"; fi; done',
            timeout=5)
        if status == 0 and raw:
            (directory / "sandbox-cgroup.txt").write_text(raw)
            parts = raw.splitlines()
            memory = next((parts[i+1] for i, v in enumerate(parts[:-1]) if v.endswith("/memory.peak")), "")
            cpu = next((v.split()[1] for v in parts if v.startswith("usage_usec ")), "")
            return {"sandbox_cpu_seconds": int(cpu) / 1e6 if cpu.isdecimal() else None,
                    "sandbox_peak_memory_mib": round(int(memory) / 2**20, 3) if memory.isdecimal() else None}
    except Exception:
        pass
    return {"sandbox_cpu_seconds": None, "sandbox_peak_memory_mib": None}


def verify_cleanup(app_id):
    for _ in range(5):
        try:
            sandboxes = [sb for sb in modal.Sandbox.list(app_id=app_id) if sb.poll() is None]
            containers = cli_json("container", "list", "--app-id", app_id)
            apps = cli_json("app", "list")
            state = next((row["state"] for row in apps if row["app_id"] == app_id), None)
            if not sandboxes and not containers and state == "stopped":
                return {"verified": True, "active_sandboxes": 0, "active_containers": 0, "app_state": state}
        except Exception:
            pass
        time.sleep(2)
    return {"verified": False, "active_sandboxes": None, "active_containers": None}


def run(cost):
    assets_valid()
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if cli_json("container", "list"):
            raise RuntimeError("Selected Modal environment is not idle. Refusing an overlapping smoke")
        return run_once(cost)


def run_once(cost):
    run_id = "modal-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    directory = RUNS / run_id
    directory.mkdir(mode=0o700)
    report_path = directory / "report.json"
    database_values = {key: secrets.token_urlsafe(32) for key in
                       ("DB_MIGRATION_PASSWORD", "DB_RUNTIME_PASSWORD", "PORTAL_METRICS_PASSWORD")}
    worker_values = {key: secrets.token_urlsafe(32) for key in ("SDU_GATE_KEY", "SDU_STUDENT_PASSWORD")}
    redactions = [*database_values.values(), *worker_values.values()]
    database_secret = modal.Secret.from_dict(database_values)
    worker_secret = modal.Secret.from_dict(worker_values)
    backend, generator = images()
    app = modal.App("sdu-phase8b-smoke", tags={"sdu_run": run_id}, include_source=False)

    @app.function(image=generator, secrets=[worker_secret], region=REGION, routing_region=REGION,
                  cpu=(K6_CPU, K6_CPU), memory=(K6_MIB, K6_MIB), timeout=K6_SECONDS, startup_timeout=K6_STARTUP,
                  min_containers=0, max_containers=1, buffer_containers=0, scaledown_window=K6_IDLE, retries=0,
                  restrict_modal_access=True, single_use_containers=True, serialized=True, include_source=False)
    def generate(origin, token):
        import runpy
        return runpy.run_path("/opt/sdu/worker.py")["run_smoke"](origin, token)

    report = {"version": 1, "id": run_id, "status": "running", "stage": "creating-app", "started_at": now(),
              "region": REGION, "vus": 10, "metrics": None, "cost": cost, "cleanup": None,
              "resources": {"sandbox_cpu_limit_cores": SB_CPU, "sandbox_memory_limit_mib": SB_MIB,
                            "generator_cpu_limit_cores": K6_CPU, "generator_memory_limit_mib": K6_MIB},
              "artifact": {"jar_sha256": sha(JAR), "postgres_image": PG_IMAGE, "generator_image": PY_IMAGE}}
    save_json(report_path, report)
    print(f"{run_id} provisioning in {REGION}. Estimated with egress allowance USD {cost['estimate_usd']:.4f}.", flush=True)
    sandbox = None
    app_id = None
    try:
        with app.run():
            app_id = app.app_id
            report.update(stage="creating-sandbox", app_id=app_id)
            save_json(report_path, report)
            try:
                sandbox = modal.Sandbox.create("bash", "/opt/sdu/start.sh", app=app, name="sdu-backend-postgres",
                    image=backend, secrets=[database_secret, worker_secret], region=REGION,
                    cpu=(SB_CPU, SB_CPU), memory=(SB_MIB, SB_MIB), timeout=SB_SECONDS,
                    env={"PGDATA": "/tmp/sdu-cloud/pg"}, volumes={}, tags={"sdu_run": run_id})
                report.update(stage="readiness", sandbox_id=sandbox.object_id)
                save_json(report_path, report)
                status, _, _ = execute(sandbox, "bash", "-c",
                    "for ((i=0;i<85;i++)); do "
                    "[[ ! -f /tmp/sdu-cloud/failed ]] || exit 2; "
                    "[[ ! -f /tmp/sdu-cloud/ready ]] || exec /tmp/sdu-cloud/gateway probe; sleep 1; done; exit 1",
                    timeout=95)
                if status != 0:
                    raise RuntimeError(f"Backend readiness failed with code {status}")
                status, placement, _ = execute(sandbox, "bash", "-c", 'printenv MODAL_REGION')
                report["sandbox_observed_region"] = placement.strip() if status == 0 and placement.strip() else None
                credentials = sandbox.create_connect_token(user_metadata={"purpose": "sdu-cloud-smoke"}, port=8080)
                redactions.extend([credentials.token, credentials.url.rstrip("/")])
                origin = validate_origin(credentials.url)
                report["stage"] = "generator"
                save_json(report_path, report)
                outcome = generate.remote(origin, credentials.token)
                report.update(stage=outcome["stage"], security=outcome.get("security"),
                              generator_observed_region=outcome.get("region"),
                              login_seconds=outcome.get("login_seconds"), login_failures=outcome.get("login_failures"),
                              preparation_error=outcome.get("preparation_error"), k6_exit=outcome.get("k6_exit"),
                              metrics=metrics_from(outcome))
                for name, key in (("raw.json.gz", "raw"), ("k6-console.log", "k6_log")):
                    if outcome.get(key) is not None:
                        payload = outcome[key]
                        (directory / name).write_bytes(payload if isinstance(payload, bytes) else payload.encode())
                if outcome.get("summary"):
                    save_json(directory / "k6-summary.json", outcome["summary"])
                report["resources"].update(k6_cpu_seconds=outcome.get("k6_cpu_seconds"),
                                           k6_peak_rss_mib=outcome.get("k6_peak_rss_mib"))
                save_json(report_path, report)
                checks = sql_verification(sandbox, database_secret)
                save_json(directory / "invariants.json", checks)
                report["sql_invariants_passed"] = checks["passed"]
                metrics = report["metrics"]
                passed = (outcome.get("k6_exit") == 0 and checks["passed"] and metrics["requests"] == 50
                          and metrics["errors"] == 0 and metrics["completed_journeys"] == 10
                          and metrics["login_successes"] == 10 and metrics["duration_seconds"] is not None
                          and 0 < metrics["duration_seconds"] <= 30
                          and all(metrics[key] is not None for key in ("rps", "p95_ms", "p99_ms")))
                report.update(status="passed" if passed else "failed")
                if passed:
                    report["stage"] = "complete"
            finally:
                if sandbox is not None:
                    try:
                        report["resources"].update(collect_logs(sandbox, directory, redactions))
                    finally:
                        try:
                            report["sandbox_exit"] = sandbox.terminate(wait=True)
                        except Exception as error:
                            report["sandbox_termination_error"] = type(error).__name__
    except BaseException as error:
        report["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        report["failure"] = type(error).__name__
        (directory / "operator-private.log").write_text(safe_log(str(error), *redactions))
    finally:
        app_id = app_id or app.app_id
        if app_id:
            report["app_id"] = app_id
            report["cleanup"] = verify_cleanup(app_id)
            if not report["cleanup"]["verified"]:
                try:
                    app.stop()
                except Exception:
                    pass
                report["cleanup"] = verify_cleanup(app_id)
            if not report["cleanup"]["verified"]:
                report.update(status="failed", cleanup_warning="Manual inspection and app stop are required")
        report["finished_at"] = now()
        save_json(report_path, report)
        print(f"{run_id} {report['status']} at {report['stage']}. Evidence: {directory}", flush=True)
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--confirm", action="store_true", help="Explicitly authorize this single fixed cloud smoke")
    parser.add_argument("--max-usd", type=Decimal, default=Decimal("1.00"))
    args = parser.parse_args()
    if not args.max_usd.is_finite() or not Decimal(0) < args.max_usd <= Decimal("1.00"):
        parser.error("A positive cost limit of at most USD 1.00 is required")
    if args.action == "run" and not args.confirm:
        parser.error("Review the plan and use --confirm for one smoke")
    if modal.__version__ != "1.6.3" or sys.version_info[:2] != (3, 12):
        parser.error("Use Python 3.12 and the pinned Modal SDK 1.6.3")
    try:
        active = cli_json("container", "list")
        cost = cost_plan(modal.Workspace.from_context().billing.rates())
    except Exception as error:
        sys.exit(f"Modal inventory or billing preflight failed ({type(error).__name__}). Verify authorization with modal app list or modal setup.")
    print(json.dumps({"sdk": modal.__version__, "region": REGION,
                      "active_containers": len(active), "cost": cost}, indent=2), flush=True)
    if Decimal(str(cost["estimate_usd"])) > args.max_usd:
        sys.exit("Estimated expense exceeds the authorized limit")
    if args.action == "run":
        def interrupt(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupt)
        if not run(cost):
            sys.exit(1)


if __name__ == "__main__":
    main()
