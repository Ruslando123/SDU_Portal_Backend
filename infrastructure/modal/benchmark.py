"""Plan or explicitly run bounded Phase 8C cases in fresh Modal sandboxes."""
import argparse
from collections import Counter
from dataclasses import replace
from decimal import Decimal
import fcntl
import gzip
import hashlib
import json
import pickle
import secrets
import signal
import tarfile
import tempfile
from contextlib import ExitStack, nullcontext
from statistics import median
import sys
from datetime import datetime, timedelta, timezone

import modal

from bench_config import (BASE_POOLS, SCALED_POOLS, Case, LEVELS, OPEN_RATES, PROFILES, WARMUP_SECONDS, STEADY_SECONDS, DB_CPU, DB_MIB,
                          MIXED_OPEN_RATES, REDIS_SIDECAR_CPU_LIMITS,
                          K6_MIB, K6_STARTUP, K6_IDLE, RESERVE_USD, RESOURCE_PROFILES, RESOURCE_DB_CPU, RESOURCE_DB_MIB,
                          RESOURCE_EDGE_CPU, RESOURCE_EDGE_MIB, SESSION_LEVELS, budget, first_suite)
from bench_evidence import chart_samples, checkouts_per_api_request, phase_metrics, read_samples, resource_summary, client_failures, join_traces
from evidence_transport import (MAX_WORKER_RESULT, read_worker_artifact,
                                read_worker_manifest, retry_read, transient_read_failure, wait_for_worker)
from smoke import (ASSETS, HERE, JAR, PG_IMAGE, PY_IMAGE, REGION, RUNS, VIOLATIONS, assets_valid, cli_json, collect_logs,
                   execute, images, now, query, save_json, sha, verify_cleanup)
from worker import safe_log, validate_origin
from mixed_evidence import database_ledger, business_results, failure_boundary, reconcile
from pooler_evidence import pooler_summary
from replica_evidence import replica_summary
import split_topology
import resource_topology
import private_db

APP_NAME = "sdu-phase8c-benchmark"
PROJECT_LIMIT_USD = Decimal("25.00")
WORKER_RESULT_CHUNK = 256 * 1024
PRE_LADDER_WORKLOAD_SHA = "91d9fff69340ab2246bd1a52345064e341c3991caf314689bf10c10f9792a582"
QUALIFIED_SESSION_BASELINE_JAR = "39ffe51faa9558239167af7dd227aac3e361a11fe5e1f5f03d02b4331ffdbc42"
POOLER = HERE.parent / "scaling" / "pgbouncer"
POOLER_ASSET = ASSETS / "pgbouncer-linux-amd64.tar.gz"
POOLED = ("session", "transaction")
BALANCER = HERE.parent / "scaling" / "load-balancing"
BALANCER_ASSET = ASSETS / "haproxy-linux-amd64"
REPLICATION = HERE.parent / "scaling" / "replication"
SQL = """SELECT json_build_object(
    'active',(SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'),
    'withdrawn',(SELECT count(*) FROM enrollment.enrollments WHERE status='WITHDRAWN'),
    'students',(SELECT count(*) FROM identity.test_accounts WHERE login_name LIKE 'cloud-bench-%'),
    'mixed_seats',(SELECT sum(capacity) FROM sections.course_sections WHERE section_code LIKE 'BENCH%'),
    'hot_counter',(SELECT enrolled_count FROM sections.course_sections WHERE section_code='DEMO'),
    'enrolled_outcomes',(SELECT count(*) FROM operations.idempotency_records WHERE response_code='ENROLLED'),
    'withdrawn_outcomes',(SELECT count(*) FROM operations.idempotency_records WHERE response_code='WITHDRAWN'),
    'full_outcomes',(SELECT count(*) FROM operations.idempotency_records WHERE response_code='SECTION_FULL'),
    'outcomes',(SELECT count(*) FROM operations.idempotency_records),
    'audit_events',(SELECT count(*) FROM audit.audit_events),
    'authenticated_sessions',(SELECT count(DISTINCT principal_name) FROM identity.spring_session WHERE principal_name IS NOT NULL),
    'durable',fsync_enabled AND commit_enabled AND full_pages AND loopback,
    'flyway_versions',(SELECT string_agg(version,',' ORDER BY installed_rank) FROM flyway_schema_history WHERE success))
    FROM (SELECT current_setting('fsync')='on' AS fsync_enabled, current_setting('synchronous_commit')='on' AS commit_enabled,
    current_setting('full_page_writes')='on' AS full_pages, current_setting('listen_addresses')='127.0.0.1' AS loopback) settings;"""


def sql_checks(sandbox, secret, case, warm, measured):
    raw = query(sandbox, ["-f", "/opt/sdu/invariants.sql"], secret).splitlines()
    violations = dict(zip(VIOLATIONS, (int(row) for row in raw[:len(VIOLATIONS)]), strict=True))
    statement = SQL
    if case.private_db_sidecar:
        statement = statement.replace("current_setting('listen_addresses')='127.0.0.1'",
                                      "current_setting('listen_addresses') ~ '^127[.]0[.]0[.]1,[0-9.]+$'")
    observed = json.loads(query(sandbox, ["-c", statement], secret))
    expected = {"students": case.students, "mixed_seats": 20 * case.section_capacity, "active": 0, "withdrawn": 0, "hot_counter": 0,
                "enrolled_outcomes": 0, "withdrawn_outcomes": 0, "full_outcomes": 0,
                "authenticated_sessions": 0 if case.session_storage == "redis" else case.vus,
                "durable": True, "flyway_versions": "1,2,3,4,5,6,7,8"}
    if case.profile == "mixed":
        enrolled = (warm["enrollments"] or 0) + (measured["enrollments"] or 0)
        withdrawn = (warm["withdrawals"] or 0) + (measured["withdrawals"] or 0)
        expected.update(enrolled_outcomes=enrolled, withdrawn_outcomes=withdrawn, withdrawn=withdrawn)
        matched = enrolled == withdrawn and bool(warm["enrollments"]) and (case.diagnostic_only or bool(measured["enrollments"]))
    elif case.profile == "hot-section":
        allocated = min(case.vus, 30)
        denied = case.vus - allocated
        expected.update(active=allocated, hot_counter=allocated, enrolled_outcomes=allocated, full_outcomes=denied)
        matched = measured["enrollments"] == allocated and (measured["section_full"] or 0) == denied
    elif case.profile == "login-burst":
        logins = measured["login_successes"] or 0
        expected["authenticated_sessions"] = 5 + logins
        matched = logins > 0 and logins + (measured["outcomes"]["rate_limited"] or 0) == case.vus
    else:
        matched = True
    expected["audit_events"] = expected["outcomes"] = sum(expected[key] for key in
        ("enrolled_outcomes", "withdrawn_outcomes", "full_outcomes"))
    return {"observed": observed, "expected": expected, "violations": violations,
            "passed": matched and not any(violations.values()) and observed == expected}


def load_passed(case, warm, measured, worker):
    if case.private_scaled and not (worker.get("warmup_gate") or {}).get("passed"):
        return False
    if case.diagnostic_only:
        return generator_headroom(case, worker, phase="warmup")
    for phase, metric in (("warmup", warm), ("measure", measured)):
        if ((worker.get(phase) or {}).get("exit") != 0 or not metric["rps"]
                or metric["all_unexpected"] != 0 or metric["errors"] != 0
                or metric["window_started_ms"] is None or metric["window_ended_ms"] is None
                or metric["interrupted_iterations"] != 0 or (metric["dropped_iterations"] or 0) != 0
                or any(metric[key] is None for key in ("p50_ms", "p95_ms", "p99_ms"))):
            return False
    if case.arrival_rate:
        if not generator_headroom(case, worker):
            return False
        scheduled = case.arrival_rate * STEADY_SECONDS
        return (measured["offered_iterations"] == scheduled
                and measured["started_iterations"] == measured["completed_journeys"]
                and measured["requests"] >= scheduled * 0.99
                and measured["p95_ms"] <= 1000 and measured["p99_ms"] <= 2000)
    if case.vus > 200 or case.reconcile:
        if not generator_headroom(case, worker):
            return False
        for window, seconds in ((warm, case.warmup_seconds), (measured, STEADY_SECONDS)):
            if (window["observed_vus_peak"] != case.vus or window["offered_iterations"] is None
                    or window["offered_iterations"] < 0.95 * case.vus * seconds / 1.25
                    or window["p95_ms"] > 1000 or (case.private_scaled and window["p95_ms"] == 1000)
                    or window["p99_ms"] > 2000):
                return False
    return case.closed or measured["completed_journeys"] == case.vus


def generator_headroom(case, worker, phase="measure"):
    average = (worker.get(phase) or {}).get("generator_cpu_cores")
    resources = worker.get("resources") or {}
    rss = resources.get("k6_peak_rss_mib")
    worker_rss = resources.get("generator_worker_peak_rss_mib")
    cores = case.generator_cpu * 2
    if case.private_scaled:
        quota = resources.get("generator_cpu_max") or {}
        if quota.get("quota_usec") and quota.get("period_usec"):
            cores = min(cores, quota["quota_usec"] / quota["period_usec"])
    return (average is not None and average < cores * 0.85
            and rss is not None and worker_rss is not None
            and rss + worker_rss < K6_MIB * case.generator_cpu * 0.85)


def images_for_benchmark(split=False, diagnostic=False, database_path=None, balanced=False, catalog_path=None,
                         resource_profile=None, session_comparison=False):
    packages = ["python3", "python3-websockets"]
    if resource_profile:
        packages.append("ca-certificates")
    if session_comparison:
        packages.append("redis-server")
    database = modal.Image.from_registry(PG_IMAGE).apt_install(*packages) if split else None
    backend, generator = images(database)
    if split:
        backend = backend.add_local_file(HERE / "db-bridge.py", "/opt/sdu/db-bridge.py")
    elif diagnostic:
        for source in split_topology.DIAGNOSTIC_FILES:
            backend = backend.add_local_file(HERE / source, f"/opt/sdu/{source}")
    for source in ("bench-seed.sql", "sample-sandbox.sh"):
        backend = backend.add_local_file(HERE / source, f"/opt/sdu/{source}")
    if session_comparison:
        backend = backend.add_local_file(HERE / "start-redis-session.sh", "/opt/sdu/start-redis-session.sh")
    if database_path in POOLED:
        for source, target in ((POOLER_ASSET, "pgbouncer.tar.gz"), (POOLER / "pgbouncer.ini", "pgbouncer.ini"),
                               (POOLER / "start.sh", "start-pgbouncer.sh"), (POOLER / "sample.sh", "sample-pgbouncer.sh")):
            backend = backend.add_local_file(source, f"/opt/sdu/{target}")
    if balanced:
        for source, target in ((BALANCER_ASSET, "haproxy"), (BALANCER / "haproxy.cfg", "haproxy.cfg"),
                               (BALANCER / "start.sh", "start-balanced.sh"), (BALANCER / "sample.sh", "sample-balanced.sh")):
            backend = backend.add_local_file(source, f"/opt/sdu/{target}")
    if catalog_path is not None:
        backend = backend.add_local_file(REPLICATION / "sample.sh", "/opt/sdu/sample-catalog.sh")
        if catalog_path == "replica":
            backend = backend.add_local_file(REPLICATION / "start.sh", "/opt/sdu/start-catalog-replica.sh")
    for source in ("bench_config.py", "benchmark_worker.py", "mixed_recovery.py", "mixed_evidence.py", "bench_evidence.py",
                   "evidence_transport.py"):
        generator = generator.add_local_file(HERE / source, f"/opt/sdu/{source}")
    generator = generator.add_local_file(HERE / "cloud-benchmark.js", "/opt/sdu/benchmark.js")
    return backend, generator, database


def new_report(case, cost, suite_id, sequence):
    run_id = "modal-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    path = RUNS / run_id
    path.mkdir(mode=0o700)
    workload = case.report()
    limits = resource_limits(case)
    artifact = {name: sha(path) for name, path in (
        ("jar_sha256", JAR), ("gateway_sha256", ASSETS / "gateway"), ("k6_sha256", ASSETS / "k6"),
        ("workload_sha256", HERE / "cloud-benchmark.js"), ("fixture_sha256", HERE / "bench-seed.sql"),
        ("sampler_sha256", HERE / "sample-sandbox.sh"), ("worker_sha256", HERE / "benchmark_worker.py"),
        ("bootstrap_sha256", HERE / "start-sandbox.sh"), ("config_sha256", HERE / "bench_config.py"),
        ("auth_helper_sha256", HERE / "worker.py"), ("k6_helper_sha256", HERE.parent / "k6/lib/api.js"),
        ("runner_sha256", HERE / "benchmark.py"), ("evidence_sha256", HERE / "bench_evidence.py"),
        ("recovery_sha256", HERE / "mixed_recovery.py"), ("ledger_sha256", HERE / "mixed_evidence.py"))}
    artifact.update(postgres_image=PG_IMAGE, generator_image=PY_IMAGE)
    if case.database_path in POOLED:
        artifact.update(pgbouncer_bundle_sha256=sha(POOLER_ASSET), pgbouncer_config_sha256=sha(POOLER / "pgbouncer.ini"),
                        pgbouncer_start_sha256=sha(POOLER / "start.sh"), pgbouncer_sampler_sha256=sha(POOLER / "sample.sh"))
    if case.backend_instances is not None:
        artifact.update(haproxy_sha256=sha(BALANCER_ASSET), haproxy_config_sha256=sha(BALANCER / "haproxy.cfg"),
                        balanced_start_sha256=sha(BALANCER / "start.sh"), balanced_sampler_sha256=sha(BALANCER / "sample.sh"))
    if case.hardware in ("ha-one", "ha-two"):
        artifact.update(balanced_start_sha256=sha(BALANCER / "start.sh"), balanced_sampler_sha256=sha(BALANCER / "sample.sh"),
                        haproxy_config_sha256=sha(BALANCER / "haproxy.cfg"))
    if case.catalog_path is not None:
        artifact.update(catalog_sampler_sha256=sha(REPLICATION / "sample.sh"),
                        replica_start_sha256=sha(REPLICATION / "start.sh"))
    if case.resource_profile:
        artifact.update(haproxy_sha256=sha(BALANCER_ASSET), resource_edge_sha256=sha(HERE / "resource-edge.sh"),
                        resource_topology_sha256=sha(HERE / "resource_topology.py"))
    if case.reconcile:
        files = split_topology.DB_FILES if case.split_database else split_topology.DIAGNOSTIC_FILES
        artifact.update({f"{name.replace('-', '_').replace('.', '_')}_sha256": sha(HERE / name)
                         for name in files})
    report = {"version": 2, "kind": "phase8c", "id": run_id, "status": "running", "stage": "creating-app",
                  "region": REGION, "vus": case.vus, "started_at": now(), "suite_id": suite_id, "sequence": sequence,
                  "workload": workload, "resources": limits, "artifact": artifact, "metrics": None, "cleanup": None,
                  "cost": {"estimate_usd": float(cost["per_run_estimate_usd"]), "actual_usd": None}}
    if case.database_path is not None:
        report["architecture"] = {"phase": 9, "database_path": case.database_path,
                                  "backend_instances": case.backend_instances or 1,
                                  "load_balancer": "HAProxy" if case.backend_instances is not None else None,
                                  "resource_profile": "scale-out" if case.scale_out else "fixed",
                                  "catalog_path": case.catalog_path,
                                  "database_instances": 2 if case.catalog_path == "replica" else 1,
                                  "pooler_version": "1.26.1" if case.database_path in POOLED else None,
                                  "pooler_server_limit": 16 if case.database_path in POOLED else None}
    if case.resource_profile:
        report["architecture"] = {"phase": 9, "database_path": "direct", "backend_instances": case.instances,
                                  "load_balancer": "HAProxy", "resource_profile": "isolated-" + case.resource_profile,
                                  "database_instances": 1, "aggregate_hikari_limit": case.pool_size}
    if case.private_db_sidecar:
        report["architecture"]["resource_profile"] = "private-sidecar"
        artifact["jdbc_route_probe_sha256"] = sha(HERE / "JdbcRouteProbe.java")
        if case.private_scaled:
            report["architecture"]["http_upstream_reuse"] = "always"
        if case.private_loopback:
            report["architecture"]["http_upstream_path"] = "direct-verified-tls" if case.http_experiment == "direct" else "loopback-verified-tls"
        if case.http_experiment:
            artifact["http_sampler_sha256"] = sha(HERE / "sample-http.py")
            if case.http_experiment == "direct":
                report["architecture"]["load_balancer"] = None
                report["architecture"].pop("http_upstream_reuse", None)
    if case.session_storage:
        report["architecture"]["session_storage"] = case.session_storage
        report["architecture"]["redis_process_loopback"] = case.session_storage == "redis"
    if case.hardware in ("ha-one", "ha-two"):
        report["architecture"].update(backend_instances=case.app_processes, load_balancer="HAProxy")
        artifact["redis_bootstrap_sha256"] = sha(HERE / "start-redis-session.sh")
        artifact["evidence_transport_sha256"] = sha(HERE / "evidence_transport.py")
    return path, report


def receive_worker_result(chunks):
    payload = bytearray()
    for chunk in chunks:
        if not isinstance(chunk, bytes) or not 0 < len(chunk) <= WORKER_RESULT_CHUNK:
            raise ValueError("Invalid generator result chunk")
        if len(payload) + len(chunk) > MAX_WORKER_RESULT:
            raise ValueError("Generator result exceeded its evidence limit")
        payload.extend(chunk)
    # This is the output of our own serialized Modal function, on the same trusted
    # channel Modal already uses to deserialize each chunk.
    return pickle.loads(payload)


def resource_limits(case):
    limits = {"sandbox_cpu_limit_cores": case.sandbox_cpu, "sandbox_memory_limit_mib": case.sandbox_memory,
            "sandbox_timeout_seconds": case.sandbox_seconds, "generator_timeout_seconds": case.generator_seconds,
            "generator_cpu_limit_cores": case.generator_cpu, "generator_memory_limit_mib": K6_MIB * case.generator_cpu}
    if case.split_database:
        limits.update(database_cpu_limit_cores=RESOURCE_DB_CPU if case.resource_profile else DB_CPU,
                      database_memory_limit_mib=RESOURCE_DB_MIB if case.resource_profile else DB_MIB,
                      database_timeout_seconds=case.database_seconds)
    if case.resource_profile:
        limits.update(edge_cpu_limit_cores=RESOURCE_EDGE_CPU, edge_memory_limit_mib=RESOURCE_EDGE_MIB,
                      backend_sandbox_count=case.instances, backend_jvm_max_heap_mib=case.sandbox_memory // 2,
                      backend_pool_size=case.pool_size // case.instances)
    if case.private_db_sidecar:
        for key in ("database_cpu_limit_cores", "database_memory_limit_mib", "database_timeout_seconds"):
            limits.pop(key)
        limits.update(sandbox_cpu_limit_cores=case.sidecar_cpu_limit,
                      sandbox_memory_limit_mib=case.sidecar_memory_limit)
    if case.private_loopback:
        limits.pop("edge_cpu_limit_cores")
        limits.pop("edge_memory_limit_mib")
    if case.hardware in ("ha-one", "ha-two"):
        limits.update(backend_jvm_max_heap_mib=4096 // case.app_processes, backend_pool_size=case.pool_size // case.app_processes)
    if case.tomcat_threads is not None:
        limits["tomcat_max_threads"] = case.tomcat_threads
    if case.jdbc_security_pool is not None:
        limits.update(backend_pool_size=case.pool_size - case.jdbc_security_pool, security_pool_size=case.jdbc_security_pool,
                      aggregate_hikari_limit=case.pool_size)
    return limits


def wait_ready(sandbox):
    command = (
        "for ((i=0;i<85;i++)); do "
        "[[ ! -f /tmp/sdu-cloud/failed ]] || exit 2; "
        "[[ ! -f /tmp/sdu-cloud/ready ]] || exec /tmp/sdu-cloud/gateway probe; sleep 1; done; exit 1"
    )
    status, _, _ = execute(sandbox, "bash", "-c", command, timeout=95)
    if status != 0:
        raise RuntimeError(f"Backend readiness failed with code {status}")
    status, region, _ = execute(sandbox, "printenv", "MODAL_REGION")
    if status != 0 or region.strip() != REGION:
        raise RuntimeError("Sandbox region did not match the fixed benchmark region")


def verify_evidence_transport(sandbox):
    destination = "/tmp/sdu-cloud/evidence-transfer-probe.bin"
    status, expected, _ = execute(sandbox, "bash", "-ec",
        'umask 077; head -c 262144 /dev/urandom > "$1"; sha256sum "$1" | cut -d" " -f1',
        "sdu-evidence-transfer-probe", destination, timeout=10)
    if status:
        raise RuntimeError("Evidence transport probe failed to create its fixture")
    data = retry_read(lambda: sandbox.filesystem.read_bytes(destination))
    if len(data) != 262144 or hashlib.sha256(data).hexdigest() != expected.strip():
        raise RuntimeError("Modal filesystem evidence transport did not preserve the fixture")
    execute(sandbox, "rm", "-f", destination, timeout=5)


def record_outcome(path, report, case, outcome, sandbox, db_secret, database=None, nodes=()):
    report.update(stage=outcome["stage"], security=outcome.get("security"), preparation=outcome.get("preparation"),
                  preparation_seconds=outcome.get("preparation_seconds"), generator_observed_region=outcome.get("region"))
    if outcome.get("warmup_gate") is not None:
        report["warmup_gate"] = outcome["warmup_gate"]
    if outcome.get("session_smoke") is not None:
        report["session_smoke"] = outcome["session_smoke"]
    report["resources"].update(outcome.get("resources", {}))
    for phase in ("warmup", "measure"):
        payload = outcome.get(phase) or {}
        report["resources"][f"{phase}_k6_cpu_cores"] = payload.get("generator_cpu_cores")
        report["resources"][f"{phase}_k6_cpu_seconds"] = payload.get("generator_cpu_seconds")
        if payload.get("summary") is not None:
            save_json(path / f"{phase}-k6-summary.json", payload["summary"])
        if payload.get("log"):
            (path / f"{phase}-k6.log").write_text(payload["log"])
        if payload.get("ledger") is not None:
            (path / f"{phase}-attempts.tsv.gz").write_bytes(payload["ledger"])
        if payload.get("recovery") is not None:
            report.setdefault("recovery", {})[phase] = payload["recovery"]
        if payload.get("recovery_ledger") is not None:
            (path / f"{phase}-recovery.tsv.gz").write_bytes(payload["recovery_ledger"])
    warm = phase_metrics(outcome.get("warmup"), case.warmup_seconds, case.ramp_elapsed)
    measured = phase_metrics(outcome.get("measure"), STEADY_SECONDS if case.timed else None)
    if case.arrival_rate:
        measured["scheduled_iterations"] = case.arrival_rate * STEADY_SECONDS
        if (measured["offered_iterations"] == measured["scheduled_iterations"]
                and measured["started_iterations"] == measured["completed_journeys"]
                and measured["dropped_iterations"] is None):
            measured["dropped_iterations"] = 0
    report.update(warmup_metrics=warm, metrics=measured)
    save_json(path / "report.json", report)
    if case.http_experiment:
        try:
            report["http_telemetry"] = resource_topology.collect_http_samples(sandbox, path,
                compressed=bool(case.session_storage),
                window=measured if measured["window_started_ms"] is not None else warm)
        except Exception as error:
            report["http_telemetry"] = {"capture_error": type(error).__name__}
    if outcome.get("warmup") or outcome.get("measure"):
        db_host = database or sandbox
        app_host = nodes[0] if nodes else sandbox
        if case.session_storage:
            try:
                text, status = resource_topology.backend_samples(app_host), 0
            except Exception as error:
                report["backend_samples_error"] = type(error).__name__
                text, status = "", 1
        else:
            status, text, _ = execute(app_host, "cat", "/tmp/sdu-cloud/samples.tsv", timeout=12)
        if status == 0:
            (path / "samples.tsv").write_text(text)
            if case.session_storage:
                (path / "samples-ui.tsv").write_text(chart_samples(text))
            window = measured if measured["window_started_ms"] is not None else warm
            report["resource_window"] = "measure" if window is measured else "warmup"
            samples = read_samples(text)
            report["resources"].update(resource_summary(samples, {
                **window, "window_started_ms": window["scenario_started_ms"], "window_ended_ms": window["observed_window_ended_ms"]
            }))
            if case.session_read_mode:
                hold = resource_summary(samples, {"window_started_ms": warm["window_started_ms"],
                                                  "window_ended_ms": warm["window_ended_ms"]})
                report["session_jdbc_target_hold"] = {key: hold[key] for key in (
                    "session_jdbc", "sample_span_seconds", "hikari_active_peak", "hikari_pending_peak",
                    "hikari_acquisition_mean_ms", "hikari_usage_mean_ms", "transaction_mean_ms")}
            if case.jdbc_security_pool is not None:
                jdbc_window = resource_summary(samples, window)
                report["resources"]["jdbc_target_window"] = jdbc_window
                archive = path / "jvm-evidence-1.tar.gz"
                if archive.is_file():
                    try:
                        with tarfile.open(archive) as jvm:
                            with jvm.extractfile("requests.tsv") as request_counters:
                                rows = [tuple(map(int, line.split())) for line in request_counters]
                        jdbc_window["checkouts_per_api_request"] = checkouts_per_api_request(
                            samples, window, [(epoch, virtual + platform) for epoch, _, _, virtual, platform in rows])
                    except (OSError, KeyError, ValueError, tarfile.TarError):
                        jdbc_window["checkouts_per_api_request"] = None
            if case.reconcile and window is measured:
                report["warmup_transaction_breakdown"] = resource_summary(samples, {
                    "window_started_ms": warm["scenario_started_ms"], "window_ended_ms": warm["observed_window_ended_ms"]
                })["transaction_breakdown"]
        if nodes and (status == 0 or not case.session_storage):
            window = measured if measured["window_started_ms"] is not None else warm
            try:
                report["resources"]["backend_nodes"] = resource_topology.node_measurements(
                    nodes, path, window, first_samples=text if status == 0 else None)
            except Exception as error:
                if not case.session_storage:
                    raise
                report["backend_nodes_error"] = type(error).__name__
        if case.reconcile:
            split_topology.drain(db_host, db_secret)
            actual_delay = int(query(db_host, ["-c", "SELECT setting FROM pg_settings WHERE name='commit_delay'"], db_secret))
            if actual_delay != case.commit_delay_us:
                raise RuntimeError("PostgreSQL commit_delay differs from the benchmark plan")
            report["resources"]["database_commit_delay_us"] = actual_delay
            if case.database_path in POOLED:
                status, raw, _ = execute(sandbox, "cat", "/tmp/sdu-cloud/pgbouncer-samples.tsv")
                if status == 0:
                    (path / "pgbouncer-samples.tsv").write_text(raw)
                    window = measured if measured["window_started_ms"] is not None else warm
                    report["resources"]["pgbouncer"] = pooler_summary(raw, window["scenario_started_ms"],
                                                                      window["observed_window_ended_ms"])
            if case.catalog_path is not None:
                status, raw, _ = execute(sandbox, "cat", "/tmp/sdu-cloud/catalog-samples.tsv")
                if status == 0:
                    (path / "catalog-samples.tsv").write_text(raw)
                    window = measured if measured["window_started_ms"] is not None else warm
                    report["resources"]["replication"] = replica_summary(
                        raw, window["scenario_started_ms"], window["observed_window_ended_ms"], case.catalog_path == "replica")
            status, raw, _ = execute(app_host, "cat", "/tmp/sdu-cloud/db-roundtrip.log")
            if status == 0:
                rtts = [float(line.split()[1]) for line in raw.splitlines() if line.startswith("Time: ")]
                if rtts:
                    report["resources"].update(database_idle_roundtrip_median_ms=median(rtts),
                                              database_idle_roundtrip_samples=len(rtts), database_idle_roundtrip_max_ms=max(rtts))
        sql = sql_checks(db_host, db_secret, case, warm, measured)
        save_json(path / "invariants.json", sql)
        report["sql_invariants_passed"] = sql["passed"]
        if case.reconcile:
            report["sql_structural_passed"] = not any(sql["violations"].values())
            if case.session_storage:
                session_sql = int(query(db_host, ["-c", """SELECT coalesce(sum(calls), 0)
                    FROM sdu_diagnostics.pg_stat_statements WHERE toplevel
                    AND userid = (SELECT oid FROM pg_roles WHERE rolname='portal_runtime')
                    AND query ILIKE '%identity.spring_session%';"""], db_secret))
                report["session_evidence"] = {"postgres_session_sql_statements_full_run": session_sql}
            scaled_export = case.private_scaled and case.vus >= 1500
            limit_mib = 24 if scaled_export else 12 if case.private_scaled else 8
            row_limit = 240_000 if scaled_export else 120_000
            database_rows = split_topology.export_operations(db_host, db_secret, database_ledger(row_limit), limit_mib=limit_mib)
            row_count = database_rows.count("\n") + bool(database_rows and not database_rows.endswith("\n"))
            if row_count > row_limit:
                raise RuntimeError("PostgreSQL evidence exceeded its bounded export")
            (path / "database-operations.tsv.gz").write_bytes(gzip.compress(database_rows.encode(), compresslevel=1))
            active_phases = [outcome[key] for key in ("warmup", "measure") if outcome.get(key)]
            if any(phase.get("ledger") is None for phase in active_phases):
                raise RuntimeError("Generator operation ledger is missing or exceeded its capture bound")
            ledgers = [payload for phase in active_phases for payload in (phase["ledger"], phase.get("recovery_ledger")) if payload is not None]
            reconciliation = reconcile(ledgers, database_rows)
            summary = reconciliation["summary"]
            summary["expected_unexpected"] = (warm["all_unexpected"] or 0) + (measured["all_unexpected"] or 0)
            summary["timed_unexpected"] = sum(summary["unexpected_by_phase"].get(key, 0) for key in ("warmup", "measure"))
            summary["durable_verified"] = not any(summary[k] for k in ("unobserved_durable", "inconsistent"))
            summary["passed"] = (summary["timed_unexpected"] == summary["expected_unexpected"]
                                 and summary["durable_verified"] and not summary["missing_mutation_responses"])
            observed = sql["observed"]
            summary["final_state_verified"] = (summary["durable_verified"] and report["sql_structural_passed"] and observed["active"] == 0
                and observed["enrolled_outcomes"] == observed["withdrawn_outcomes"] == observed["withdrawn"]
                and observed["outcomes"] == observed["audit_events"] == summary["durable_operations"])
            report["reconciliation"] = summary
            report["business_results"] = business_results(reconciliation, warm, measured)
            save_json(path / "operation-reconciliation.json", reconciliation)
            capture_trace(path, report, outcome, sandbox, reconciliation["unexpected"])
            if case.private_scaled:
                hops = resource_topology.http_hop_evidence(sandbox, app_host, reconciliation["unexpected"],
                    loopback=case.private_loopback, through_haproxy=case.http_experiment != "direct")
                save_json(path / "http-hop-evidence.json", hops)
                report["diagnostics"]["http_hops"] = {key: value for key, value in hops.items()
                    if key not in ("haproxy", "node_gateway", "node_upstream")}
            try:
                report["resources"].update(split_topology.postgres_evidence(db_host, db_secret, path,
                                            measured if measured["window_started_ms"] is not None else warm,
                                            compressed=bool(case.session_storage)))
            except Exception as error:
                report["postgres_capture_error"] = type(error).__name__
        else:
            try:
                capture_trace(path, report, outcome, sandbox)
            except Exception as error:
                report["trace_capture_error"] = type(error).__name__
        diagnostic_passed = not case.reconcile or (report["reconciliation"]["passed"] and report["resources"].get("postgres_samples")
                            and not any(row["pending"] for row in report.get("recovery", {}).values()))
        if case.database_path in POOLED:
            diagnostic_passed = diagnostic_passed and (report["resources"].get("pgbouncer") or {}).get("samples", 0) >= 2
        if case.backend_instances is not None:
            diagnostic_passed = diagnostic_passed and report["resources"].get("backend_instances_observed_min") == case.backend_instances
        if case.hardware in ("ha-one", "ha-two"):
            diagnostic_passed = diagnostic_passed and report["resources"].get("backend_instances_observed_min") == case.app_processes
        if nodes:
            sampled = report["resources"].get("backend_nodes", [])
            diagnostic_passed = diagnostic_passed and len(sampled) == case.instances and all(
                node and node["prometheus_samples"] >= 2 for node in sampled)
        if case.private_scaled:
            safety = report["live_sql_safety"]
            diagnostic_passed = (diagnostic_passed and safety["snapshots"] > 0 and "stop_reason" not in safety
                                 and report["reconciliation"]["final_state_verified"])
        if case.session_storage == "redis":
            redis = report["resources"].get("redis") or {}
            diagnostic_passed = (diagnostic_passed
                                 and report["session_evidence"]["postgres_session_sql_statements_full_run"] == 0
                                 and (redis.get("operations", {}).get("read", {}).get("count") or 0) > 0
                                 and redis.get("evicted_keys") == 0 and redis.get("rejected_connections") == 0)
        if case.catalog_path is not None:
            catalog = report["resources"].get("replication") or {}
            expected_calls = catalog.get(f"{case.catalog_path}_catalog_calls")
            diagnostic_passed = diagnostic_passed and bool(expected_calls and expected_calls > 0)
            if case.catalog_path == "replica":
                diagnostic_passed = diagnostic_passed and catalog.get("primary_catalog_calls") == 0
        if case.jvm_threads:
            diagnostic_passed = diagnostic_passed and report.get("jvm_evidence", {}).get("verified") is True
        if case.jdbc_security_pool is not None:
            pools = (report["resources"].get("jdbc_target_window") or {}).get("hikari_pools") or {}
            expected = {"sdu-runtime": case.pool_size - case.jdbc_security_pool}
            if case.jdbc_security_pool:
                expected["sdu-security"] = case.jdbc_security_pool
            report["jdbc_pool_evidence_verified"] = {name: pool["max_connections"] for name, pool in pools.items()} == expected
            diagnostic_passed = diagnostic_passed and report["jdbc_pool_evidence_verified"]
        if sql["passed"] and diagnostic_passed and load_passed(case, warm, measured, outcome):
            report.update(status="passed", stage="diagnostic-complete" if case.diagnostic_only else "complete")
            return
    report["status"] = "failed"


def capture_trace(path, report, outcome, sandbox, failures=None):
    failures = client_failures(outcome) if failures is None else failures
    all_events = []
    gateway_counts = {}
    for offset in range(0, len(failures) or 1, 1500):
        batch = failures[offset:offset+1500]
        ids = ",".join(row["trace"] for row in batch)
        status, output, _ = execute(sandbox, "awk", "-v", f"ids={ids}", """
        BEGIN { n=split(ids, values, ","); for (i=1;i<=n;i++) selected[values[i]]=1 }
        $3=="trace" && NF==8 { counts[$5]++; if ($4 in selected) print }
        END { for (event in counts) print "count",event,counts[event] }
    """, "/tmp/sdu-cloud/gateway.log", timeout=8)
        if status != 0:
            raise RuntimeError("Gateway trace capture failed")
        result = join_traces(batch, output)
        all_events.extend({**row, "failure_boundary": failure_boundary(row)} for row in result["failures"])
        gateway_counts = result["gateway_events"]
    save_json(path / "http-trace.json", {"failures": all_events, "gateway_events": gateway_counts})
    report["diagnostics"] = {"traced_failures": len(all_events), "gateway_events": gateway_counts,
                             "failure_boundaries": dict(Counter(row["failure_boundary"] for row in all_events))}


def run_case(case, cost, suite_id, sequence, backend, generator, database_image, stop_requested, project_budget,
             shared_pool_control_id=None):
    path, report = new_report(case, cost, suite_id, sequence)
    report["project_budget"] = project_budget
    if shared_pool_control_id is not None:
        report["shared_pool_control_id"] = shared_pool_control_id
    report_path = path / "report.json"
    save_json(report_path, report)
    print(f"{report['id']} {case.profile} {case.vus} VUs", flush=True)
    db = {name: secrets.token_urlsafe(32) for name in
          ("DB_MIGRATION_PASSWORD", "DB_RUNTIME_PASSWORD", "PORTAL_METRICS_PASSWORD")}
    if case.database_path in POOLED:
        db["PGBOUNCER_STATS_PASSWORD"] = secrets.token_urlsafe(32)
    if case.catalog_path == "replica":
        db.update(SDU_REPLICATION_PASSWORD=secrets.token_urlsafe(32),
                  SDU_CATALOG_REPLICA_PASSWORD=secrets.token_urlsafe(32))
    worker = {name: secrets.token_urlsafe(32) for name in ("SDU_GATE_KEY", "SDU_STUDENT_PASSWORD")}
    db_secret, worker_secret = modal.Secret.from_dict(db), modal.Secret.from_dict(worker)
    redactions = [*db.values(), *worker.values()]
    app = modal.App(APP_NAME, tags={"sdu_run": report["id"]}, include_source=False)
    volume_name = f"sdu-evidence-{report['id']}" if case.session_storage else None
    evidence = modal.Volume.from_name(volume_name, create_if_missing=True, version=2) if volume_name else None
    if volume_name:
        report["evidence_volume_name"] = volume_name
        save_json(report_path, report)

    def run_generator(*args):
        import sys
        sys.path.insert(0, "/opt/sdu")
        from benchmark_worker import run_benchmark
        payload = pickle.dumps(run_benchmark(*args), protocol=pickle.HIGHEST_PROTOCOL)
        if len(payload) > MAX_WORKER_RESULT:
            raise ValueError("Generator result exceeded its evidence limit")
        return payload

    def generate_stream(*args):
        payload = run_generator(*args)
        for offset in range(0, len(payload), WORKER_RESULT_CHUNK):
            yield payload[offset:offset + WORKER_RESULT_CHUNK]

    def generate_checkpoint(*args):
        import sys
        sys.path.insert(0, "/opt/sdu")
        from evidence_transport import checkpoint_worker
        return checkpoint_worker(evidence, "/sdu-evidence", lambda: run_generator(*args))

    generate = app.function(image=generator, secrets=[worker_secret], region=REGION, routing_region=REGION,
        cpu=(case.generator_cpu,) * 2, memory=(K6_MIB * case.generator_cpu,) * 2,
        timeout=case.generator_seconds, startup_timeout=K6_STARTUP,
        min_containers=0, max_containers=1, buffer_containers=0, scaledown_window=K6_IDLE,
        restrict_modal_access=True, single_use_containers=True, serialized=True, include_source=False, retries=0,
        volumes={"/sdu-evidence": evidence} if evidence is not None else {})(
            generate_checkpoint if evidence is not None else generate_stream)

    sandbox = database = None
    nodes = []
    try:
        with app.run():
            report.update(app_id=app.app_id, stage="creating-sandbox")
            save_json(report_path, report)
            try:
                app_secrets = [db_secret, worker_secret]
                env = {"PGDATA": "/tmp/sdu-cloud/pg", "SDU_BENCHMARK": "1", "SDU_BENCH_POOL": str(case.pool_size),
                       "SDU_BENCH_STUDENTS": str(case.students), "SDU_BENCH_SECTION_CAPACITY": str(case.section_capacity),
                       "SDU_BENCH_PREFILL_POOL": str(int(case.prefill_pool)),
                       "SDU_BENCH_REQUEST_LIMIT": str(case.request_limit), "SDU_BENCH_RESPONSE_MIB": str(case.response_mib)}
                if case.reconcile:
                    env["SDU_MIXED_DIAGNOSTICS"] = "1"
                    env["SDU_DB_COMMIT_DELAY_US"] = str(case.commit_delay_us)
                if case.session_read_mode:
                    env["PORTAL_SESSION_AUTOCOMMIT_READS"] = str(case.session_read_mode == "autocommit").lower()
                if case.session_storage:
                    env["PORTAL_SESSION_STORE"] = case.session_storage
                if case.jvm_threads:
                    env.update(SPRING_THREADS_VIRTUAL_ENABLED=str(case.jvm_threads == "virtual").lower(),
                               SPRING_MAIN_KEEP_ALIVE="true", PORTAL_BENCHMARK_JVM_EVIDENCE="true",
                               PORTAL_PERFORMANCE_JOINED_ACADEMIC_READS=str(case.joined_academic_reads).lower())
                if case.hardware in ("ha-one", "ha-two"):
                    env.update(SDU_PHASE12_HAPROXY="1", SDU_BACKEND_INSTANCES=str(case.app_processes), SDU_BACKEND_HEAP_BUDGET="4096")
                if case.tomcat_threads is not None:
                    env.update(SDU_DIAGNOSTIC_TOMCAT_THREADS=str(case.tomcat_threads), PORTAL_BENCHMARK_SATURATION_EVIDENCE="true")
                if case.section_read_experiment is not None:
                    env["PORTAL_PERFORMANCE_JOINED_SECTION_MEETINGS"] = str(case.section_read_experiment == "joined").lower()
                if case.jdbc_security_pool:
                    env["SDU_BENCH_POOL"] = str(case.pool_size - case.jdbc_security_pool)
                    env["PORTAL_BENCHMARK_SECURITY_POOL_SIZE"] = str(case.jdbc_security_pool)
                if case.jdbc_security_pool is not None:
                    env["PORTAL_BENCHMARK_TRANSACTION_TRACE"] = "true"
                if case.session_storage == "redis":
                    redis_secret = {key: secrets.token_urlsafe(32) for key in
                                    ("PORTAL_SESSION_REDIS_PASSWORD", "SDU_REDIS_OBSERVER_PASSWORD")}
                    redactions.extend(redis_secret.values())
                    app_secrets.append(modal.Secret.from_dict(redis_secret))
                if case.database_path in POOLED:
                    env["SDU_DATABASE_PATH"] = case.database_path
                if case.backend_instances is not None:
                    env.update(SDU_BACKEND_INSTANCES=str(case.backend_instances),
                               SDU_BACKEND_HEAP_BUDGET="512" if case.scale_out else "256")
                if case.catalog_path is not None:
                    env["SDU_CATALOG_PATH"] = case.catalog_path
                if case.private_db_sidecar:
                    ca, cert, key = split_topology.certificate(sidecar=True)
                    redactions.append(key)
                    database_credentials = modal.Secret.from_dict({**db, "SDU_DB_TLS_CERT": cert, "SDU_DB_TLS_KEY": key})
                    app_secrets.append(modal.Secret.from_dict({"SDU_DB_TLS_CA": ca}))
                    env.update(SDU_DB_PRIVATE_SIDECAR="1", SDU_SPLIT_APP="1")
                    built_database_image = split_topology.image(database_image, baked=True).build(app)
                elif case.split_database:
                    report["stage"] = "database-readiness"
                    ca, cert, key = split_topology.certificate()
                    bridge_key = secrets.token_urlsafe(32)
                    redactions.extend((key, bridge_key))
                    link = {"SDU_DB_BRIDGE_KEY": bridge_key}
                    database = split_topology.create(app, database_image, case.database_seconds,
                        modal.Secret.from_dict({**db, **link, "SDU_DB_TLS_CERT": cert, "SDU_DB_TLS_KEY": key}),
                        report["id"], case.commit_delay_us, bool(case.resource_profile))
                    report["database_sandbox_id"] = database.object_id
                    save_json(report_path, report)
                    host = split_topology.ready(database)
                    redactions.append(host)
                    app_secrets.append(modal.Secret.from_dict({**link, "SDU_DB_TLS_CA": ca, "SDU_DB_BRIDGE_HOST": host}))
                    env["SDU_SPLIT_APP"] = "1"
                    report["database_security"] = {"direct_pg_port_exposed": False, "bridge_requires_key": True,
                                                   "jdbc_tls_mode": "verify-full", "region": REGION}
                if case.resource_profile:
                    node_key = secrets.token_urlsafe(32)
                    redactions.append(node_key)
                    # The edge and backend capabilities are distinct. Only node 1 migrates and seeds.
                    app_secrets[1] = modal.Secret.from_dict({"SDU_STUDENT_PASSWORD": worker["SDU_STUDENT_PASSWORD"]})
                    if case.private_loopback:
                        http_ca, http_cert, http_key = split_topology.certificate(loopback_http=True)
                        redactions.append(http_key)
                        app_secrets.append(modal.Secret.from_dict({"SDU_HTTP_TLS_CA": http_ca,
                                                                  "SDU_HTTP_TLS_CERT": http_cert,
                                                                  "SDU_HTTP_TLS_KEY": http_key}))
                    hosts = []
                    report["stage"] = "backend-readiness"
                    for index in range(case.instances):
                        node = resource_topology.create_node(app, backend, case, report["id"], index, env, app_secrets,
                            node_key, edge_key=worker["SDU_GATE_KEY"] if case.private_loopback else None)
                        nodes.append(node)
                        if case.private_loopback:
                            sandbox = node
                        report["backend_sandbox_ids"] = [item.object_id for item in nodes]
                        save_json(report_path, report)
                        if case.private_db_sidecar:
                            database = private_db.create(node, built_database_image, database_credentials)
                            private_db.wait_ready(database)
                        wait_ready(node)
                        if case.private_db_sidecar:
                            report["resources"]["jdbc_route_probe"] = private_db.verify(node, database, db_secret)
                            report["database_security"] = {"direct_pg_port_exposed": False, "jdbc_tls_mode": "verify-full",
                                "scram": True, "private_bridge": True, "region": REGION}
                            save_json(report_path, report)
                        if not case.private_loopback:
                            hosts.append(resource_topology.node_host(node))
                            redactions.append(hosts[-1])
                    if not case.private_loopback:
                        sandbox = resource_topology.create_edge(app, backend, case, report["id"], hosts,
                                                                worker["SDU_GATE_KEY"], node_key, env)
                else:
                    sandbox = modal.Sandbox.create("bash", "/opt/sdu/start.sh", app=app,
                        name="sdu-diagnostic-app" if database else "sdu-8c-backend-postgres",
                        image=backend, secrets=app_secrets, region=REGION,
                        cpu=(case.sandbox_cpu,) * 2, memory=(case.sandbox_memory,) * 2, timeout=case.sandbox_seconds, volumes={},
                        encrypted_ports=[8080] if case.transport == "encrypted-tunnel" else [],
                        env=env, tags={"sdu_run": report["id"]})
                report.update(stage="readiness", sandbox_id=sandbox.object_id)
                save_json(report_path, report)
                if case.resource_profile:
                    url, edge_checks = resource_topology.verify_edge(sandbox, worker["SDU_GATE_KEY"], case.instances)
                    token = ""
                    report["resource_access_checks"] = edge_checks
                    if case.private_loopback:
                        report["http_tls_preflight"] = resource_topology.verify_loopback_tls(sandbox, node_key)
                    report["resources"]["quotas_before_load"] = {
                        role: resource_topology.quota(host) for role, host in [
                            ("postgres", database), ("edge", sandbox), *((f"app{i}", node) for i, node in enumerate(nodes, 1))]}
                else:
                    wait_ready(sandbox)
                if case.private_scaled:
                    report["fixture_preflight"] = private_db.verify_fixture(database, db_secret, case)
                    shared = (case.sidecar_cpu_limit, report["resources"]["sandbox_memory_limit_mib"])
                    private_db.verify_quotas(report["resources"]["quotas_before_load"],
                                             {"postgres": shared, "app1": shared, "edge": shared if case.private_loopback else (1, 1024)})
                if case.session_storage == "redis":
                    status, raw, _ = execute(sandbox, "cat", "/tmp/sdu-cloud/redis-ready", timeout=8)
                    values = raw.splitlines()
                    if status or len(values) != 2 or values[1] != "268435456" or set(sandbox.tunnels()) != {8080}:
                        raise RuntimeError("Redis ACL, memory or listener preflight failed")
                    report["redis_preflight"] = {"version": values[0], "maxmemory_bytes": int(values[1]),
                        "eviction_policy": "noeviction", "acl_verified": True, "public_port": False,
                        "transport": "same-sandbox-loopback", "shared_cpu_limit": case.sidecar_cpu_limit,
                        "shared_memory_mib": report["resources"]["sandbox_memory_limit_mib"]}
                if case.session_storage:
                    verify_evidence_transport(sandbox)
                    verify_evidence_transport(database)
                    report["evidence_transport_preflight"] = {"method": "modal-filesystem", "verified": True,
                                                              "bytes_per_role": 262144}
                report["sandbox_observed_region"] = REGION
                report["ready_at"] = now()
                if case.transport == "encrypted-tunnel" and not case.resource_profile:
                    url, token = sandbox.tunnels(timeout=15)[8080].url, ""
                elif case.transport != "encrypted-tunnel":
                    credentials = sandbox.create_connect_token(user_metadata={"purpose": "sdu-phase8c"}, port=8080)
                    url, token = credentials.url, credentials.token
                redactions.extend((token, url.rstrip("/")))
                origin = validate_origin(url)
                report["stage"] = "generator"
                report["generator_started_at"] = now()
                save_json(report_path, report)
                watch = (private_db.invariant_watch(database, sandbox, db_secret, report.setdefault("live_sql_safety", {}))
                         if case.private_scaled else nullcontext())
                with watch:
                    worker_args = (
                        origin, token, case.profile, case.vus, case.warmup_seconds, case.pool_size,
                        case.transport, case.arrival_rate, case.spread_start, case.ramp_seconds,
                        case.prefill_pool, case.reconcile, case.ramp_start_vus, case.hot_mixed,
                        case.backend_instances, case.database_path, case.catalog_path, case.resource_profile,
                        case.private_db_sidecar, case.http_experiment, case.diagnostic_only, case.session_storage,
                        case.sidecar_cpu_cores, case.jvm_threads, case.joined_academic_reads, case.hardware, case.tomcat_threads,
                        case.section_read_experiment, case.jdbc_security_pool)
                    if evidence is None:
                        outcome = receive_worker_result(generate.remote_gen(*worker_args))
                    else:
                        call = generate.spawn(*worker_args)
                        report["generator_call_id"] = call.object_id
                        save_json(report_path, report)
                        try:
                            manifest = wait_for_worker(call, case.generator_seconds + K6_STARTUP + 15)
                        except Exception as error:
                            if not transient_read_failure(error):
                                raise
                            manifest = read_worker_manifest(evidence)
                            report["generator_control_plane_recovered"] = type(error).__name__
                if evidence is not None:
                    report["generator_artifact"] = manifest
                    save_json(report_path, report)
                    outcome = pickle.loads(read_worker_artifact(evidence, manifest))
                if case.jvm_threads:
                    try:
                        if case.hardware in ("ha-one", "ha-two"):
                            jvm_instances = [resource_topology.collect_jvm_evidence(nodes[0], path, case.jvm_threads, f"jvm-evidence-{index + 1}")
                                             for index in range(case.app_processes)]
                            report["jvm_evidence"] = {"verified": all(item["verified"] for item in jvm_instances), "instances": jvm_instances}
                            if case.tomcat_threads is not None:
                                measured = jvm_instances[0]
                                report["jvm_evidence"]["verified"] &= (
                                    measured.get("tomcat_max") == case.tomcat_threads
                                    and measured.get("hikari_max") == case.pool_size - (case.jdbc_security_pool or 0))
                        else:
                            report["jvm_evidence"] = resource_topology.collect_jvm_evidence(nodes[0], path, case.jvm_threads)
                    except Exception as error:
                        report["jvm_evidence"] = {"verified": False, "capture_error": type(error).__name__}
                record_outcome(path, report, case, outcome, sandbox, db_secret, database, nodes)
            finally:
                if case.resource_profile:
                    hosts = [("postgres", database), ("edge", sandbox),
                             *((f"app{i}", node) for i, node in enumerate(nodes, 1))]
                    report["resources"]["quotas_after_load"] = {}
                    for role, host in hosts:
                        if host is not None:
                            try:
                                report["resources"]["quotas_after_load"][role] = resource_topology.quota(host)
                                resource_topology.retain_logs(host, path, role, redactions)
                            except Exception:
                                continue
                    if case.private_db_sidecar:
                        diagnostics = ((database, "/tmp/sdu-private-db-startup.log"), (database, "/tmp/sdu-cloud/pg/server.log"),
                                       (nodes[0] if nodes else None, "/tmp/sdu-cloud/private-db-connect.log"))
                        for host, filename in diagnostics:
                            if host is None:
                                continue
                            try:
                                status, raw, _ = execute(host, "tail", "-c", "8000", filename)
                                if status == 0:
                                    (path / filename.rsplit("/", 1)[-1]).write_text(safe_log(raw, *redactions))
                            except Exception:
                                continue
                if sandbox is not None:
                    try:
                        report["resources"].update(collect_logs(sandbox, path, redactions))
                        if case.database_path in POOLED:
                            status, raw, _ = execute(sandbox, "tail", "-c", "12000", "/tmp/sdu-cloud/pgbouncer.log")
                            if status == 0:
                                (path / "pgbouncer.log").write_text(safe_log(raw, *redactions))
                        if case.backend_instances is not None:
                            logs = ["haproxy.log", *(f"backend-{i}.log" for i in range(1, case.backend_instances + 1))]
                            for name in logs:
                                status, raw, _ = execute(sandbox, "tail", "-c", "12000", f"/tmp/sdu-cloud/{name}")
                                if status == 0:
                                    (path / name).write_text(safe_log(raw, *redactions))
                        if case.catalog_path == "replica":
                            status, raw, _ = execute(sandbox, "tail", "-c", "12000", "/tmp/sdu-cloud/replica/standby.log")
                            if status == 0:
                                (path / "standby.log").write_text(safe_log(raw, *redactions))
                    finally:
                        if not case.private_loopback or database is None:
                            try:
                                report["sandbox_exit"] = sandbox.terminate(wait=True)
                            except Exception as error:
                                report["sandbox_termination_error"] = type(error).__name__
                if database is not None:
                    if case.private_db_sidecar:
                        try:
                            database.terminate(wait=True)
                            report["database_sidecar_exit"] = database.poll()
                        except Exception:
                            report["database_termination_error"] = True
                    for node in nodes:
                        try:
                            exit_status = node.terminate(wait=True)
                            if case.private_loopback:
                                report["sandbox_exit"] = exit_status
                        except Exception:
                            report["backend_termination_error"] = True
                    if not case.private_db_sidecar:
                        try:
                            report["database_sandbox_exit"] = database.terminate(wait=True)
                        except Exception as error:
                            report["database_termination_error"] = type(error).__name__
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      failure=type(error).__name__)
        (path / "operator-private.log").write_text(safe_log(str(error), *redactions))
    finally:
        # Modal's App context can consume KeyboardInterrupt during shutdown.
        if report["status"] == "running":
            interrupted = stop_requested()
            report.update(status="interrupted" if interrupted else "failed",
                          failure="KeyboardInterrupt" if interrupted else "RunEndedBeforeOutcome")
        app_id = report.get("app_id") or app.app_id
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
        if volume_name:
            try:
                retry_read(lambda: modal.Volume.objects.delete(volume_name, allow_missing=True))
                try:
                    retry_read(modal.Volume.from_name(volume_name).hydrate)
                except modal.exception.NotFoundError:
                    report["evidence_volume_cleanup_verified"] = True
                else:
                    raise RuntimeError("Evidence volume remains visible after deletion")
            except Exception as error:
                report.update(status="failed", evidence_volume_cleanup_verified=False,
                              evidence_volume_cleanup_error=type(error).__name__)
                if report.get("cleanup"):
                    report["cleanup"]["verified"] = False
        report["finished_at"] = now()
        save_json(report_path, report)
        print(f"{report['id']} {report['status']} · {path}", flush=True)
    return report["status"] == "passed" and bool((report["cleanup"] or {}).get("verified"))


def prior_reservations():
    reserved = Decimal(0)
    suites = set()
    settled_before = datetime.now(timezone.utc) - timedelta(hours=1)
    for path in RUNS.glob("modal-*/report.json"):
        report = json.loads(path.read_text())
        cost = report["cost"]
        estimate = Decimal(str(cost["estimate_usd"]))
        actual = Decimal(str(cost["actual_usd"])) if cost.get("actual_source") == "modal-billing-report" else Decimal(0)
        if not estimate.is_finite() or estimate <= 0 or not actual.is_finite() or actual < 0:
            raise ValueError("Invalid prior benchmark budget evidence")
        # The workspace summary already includes attributable usage. Keep reserving the
        # unmetered part of the ceiling while receipts settle, including stopped runs.
        accounted = actual if (report.get("cleanup") or {}).get("verified") else Decimal(0)
        recorded = cost.get("actual_recorded_at")
        # Keep a settlement buffer for recent or unresolved billing. Older stopped
        # runs with an attributed receipt are already included in workspace usage.
        if accounted and report.get("finished_at") and recorded and datetime.fromisoformat(recorded) < settled_before:
            continue
        reserved += max(estimate - accounted, Decimal(0))
        if report.get("suite_id"):
            suites.add(report["suite_id"])
    return reserved + RESERVE_USD * len(suites)


def session_predecessor_passed(report, previous):
    metrics = report.get("metrics") or {}
    warmup = report.get("warmup_gate") or {}
    audit = report.get("reconciliation") or {}
    cleanup = report.get("cleanup") or {}
    p95, p99 = metrics.get("p95_ms"), metrics.get("p99_ms")
    return (
        report.get("status") == "passed" and report.get("stage") == "complete"
        and metrics.get("duration_seconds") == STEADY_SECONDS and metrics.get("observed_vus_peak") == previous
        and metrics.get("errors") == metrics.get("all_unexpected") == metrics.get("interrupted_iterations") == 0
        and (metrics.get("dropped_iterations") or 0) == 0
        and p95 is not None and p95 < 1000 and p99 is not None and p99 <= 2000
        and warmup.get("passed") is True and report.get("sql_invariants_passed") is True
        and audit.get("passed") is True and audit.get("final_state_verified") is True
        and cleanup.get("verified") is True and cleanup.get("app_state") == "stopped"
        and cleanup.get("active_sandboxes") == cleanup.get("active_containers") == 0
        and report.get("evidence_volume_cleanup_verified") is True
    )


def require_pool_diagnosis_control(case):
    selected_pool = 24 if case.tomcat_threads == 80 else case.pool_size
    required = {selected_pool} if case.tomcat_threads == 80 else {24, 32}
    fingerprints = {"jar_sha256": sha(JAR), "gateway_sha256": sha(ASSETS / "gateway"),
                    "workload_sha256": sha(HERE / "cloud-benchmark.js"), "fixture_sha256": sha(HERE / "bench-seed.sql")}
    seen = set()
    for path in sorted(RUNS.glob("modal-*/report.json"), reverse=True):
        report = json.loads(path.read_text())
        workload = report.get("workload") or {}
        pool = workload.get("pool_size")
        if (pool not in required or pool in seen or workload.get("tomcat_threads") != 80
                or workload.get("vus") != 2500 or workload.get("hardware") != "ha-one"
                or workload.get("section_read_experiment") is not None
                or workload.get("jdbc_security_pool") is not None
                or any((report.get("artifact") or {}).get(key) != value for key, value in fingerprints.items())):
            continue
        seen.add(pool)
        completed = ((report.get("metrics") or {}).get("duration_seconds") == STEADY_SECONDS
                     and (report.get("jvm_evidence") or {}).get("verified") is True
                     and (report.get("cleanup") or {}).get("verified") is True
                     and report.get("evidence_volume_cleanup_verified") is True)
        if completed and (pool != selected_pool or (report.get("sql_invariants_passed") is True
                and (report.get("reconciliation") or {}).get("final_state_verified") is True)):
            required.remove(pool)
    if required:
        sys.exit(f"Require completed matched 80-thread controls for JDBC pools {sorted(required)}")


def enforce_predecessor(case, *, shared_pool_control=False):
    if shared_pool_control and (case.vus, case.jdbc_security_pool) != (2500, 4):
        raise ValueError("A shared-pool control is only supported for the 2,500-VU 4+20 experiment")
    if case.session_storage:
        if case.vus == SESSION_LEVELS[0]:
            return
        if case.jvm_threads and (case.vus == 2000 or (case.tomcat_threads is not None
                and case.section_read_experiment is None and case.jdbc_security_pool is None)):
            baseline_path = RUNS / "modal-20261108T190216Z-f6ee77" / "report.json"
            baseline = json.loads(baseline_path.read_text()) if baseline_path.is_file() else {}
            if (session_predecessor_passed(baseline, 2000)
                    and baseline["artifact"]["fixture_sha256"] == sha(HERE / "bench-seed.sql")
                    and baseline["artifact"]["workload_sha256"] == sha(HERE / "cloud-benchmark.js")):
                if case.tomcat_threads is not None and (case.tomcat_threads, case.pool_size) != (80, 24):
                    require_pool_diagnosis_control(case)
                return
            sys.exit("Require the verified 2,000-VU Redis Sidecar baseline before a JVM comparison")
        previous = SESSION_LEVELS[SESSION_LEVELS.index(case.vus) - 1]
        predecessor_cpu_limits = (case.sidecar_cpu_limit,)
        predecessor_pool_size = case.pool_size
        # Resource retests at 2,000 VUs can use the qualified 1,500-VU control.
        if case.vus == 2000 and case.sidecar_cpu_cores == 16:
            predecessor_cpu_limits = (16, 12)
            predecessor_pool_size = 16
        artifact = (sha(JAR), sha(ASSETS / "gateway"), sha(HERE / "cloud-benchmark.js"), sha(HERE / "bench-seed.sql"))
        control_workload = replace(case, vus=previous, jdbc_security_pool=0).report() if shared_pool_control else None
        for path in sorted(RUNS.glob("modal-*/report.json"), reverse=True):
            report = json.loads(path.read_text())
            workload = report.get("workload") or {}
            build = report.get("artifact") or {}
            if (workload.get("session_storage") != case.session_storage or workload.get("vus") != previous
                    or workload.get("pool_size") != predecessor_pool_size
                    or workload.get("jvm_threads") != case.jvm_threads
                    or workload.get("hardware") != case.hardware
                    or workload.get("section_read_experiment") != case.section_read_experiment
                    or workload.get("jdbc_security_pool") != (0 if shared_pool_control else case.jdbc_security_pool)
                    or (control_workload is not None and workload != control_workload)
                    or ((case.section_read_experiment is not None or case.jdbc_security_pool is not None)
                        and workload.get("tomcat_threads") != case.tomcat_threads)
                    or bool(workload.get("joined_academic_reads")) != case.joined_academic_reads
                    or workload.get("arrival_rate") is not None
                    or (report.get("resources") or {}).get("sandbox_cpu_limit_cores") not in predecessor_cpu_limits
                    or tuple(build.get(key) for key in ("jar_sha256", "gateway_sha256", "workload_sha256", "fixture_sha256")) != artifact):
                continue
            if (session_predecessor_passed(report, previous)
                    and (not case.jvm_threads or (report.get("jvm_evidence") or {}).get("verified") is True)):
                return path.parent.name
            break
        if shared_pool_control:
            sys.exit("Require the matching, reconciled 2,000-VU shared-pool control before the 4+20 experiment")
        sys.exit(f"Require a full reconciled {case.session_storage} mixed pass at {previous} VUs on the same build")
    if not case.closed or case.vus <= 200:
        return
    previous = LEVELS[LEVELS.index(case.vus) - 1]
    predecessor_pool = (16 if case.direct_pool_test or
        (previous == 200 and case.pool_size in SCALED_POOLS and not case.reconcile) else case.pool_size)
    jar = sha(JAR)
    profiles = []
    diagnostic_attempts = []
    workload_sha = sha(HERE / "cloud-benchmark.js") if case.reconcile else None
    require_routed_predecessor = case.private_loopback and case.vus == 2500
    predecessor_route = "direct" if case.session_read_mode else "haproxy"
    for path in sorted(RUNS.glob("modal-*/report.json"), reverse=True):
        report = json.loads(path.read_text())
        workload = report.get("workload") or {}
        artifact = report.get("artifact", {})
        if (report.get("vus") == previous
                and workload.get("transport") == case.transport
                and workload.get("topology", "shared-sandbox") == case.topology
                and bool(workload.get("operation_ledger", workload.get("topology") == "split-websocket")) == case.reconcile
                and workload.get("commit_delay_us", 0) == case.commit_delay_us
                and workload.get("hot_mixed", False) == case.hot_mixed
                and workload.get("database_path") == case.database_path
                and workload.get("backend_instances") == case.backend_instances
                and workload.get("scale_out", False) == case.scale_out
                and workload.get("catalog_path") == case.catalog_path
                and workload.get("resource_profile") == case.resource_profile
                and workload.get("private_db_sidecar", False) == case.private_db_sidecar
                and not workload.get("diagnostic_only")
                and (not (case.http_experiment and case.vus == 2500) or workload.get("http_experiment") == predecessor_route)
                and (not (case.session_read_mode and case.vus == 2500) or workload.get("session_read_mode") == case.session_read_mode)
                and (not case.reconcile or (case.vus != 500 and not case.private_scaled)
                     or workload.get("fixture_students") == case.students)
                and (not case.private_scaled or workload.get("fixture_mixed_seats") == case.section_capacity * 20)
                and workload.get("pool_size") == predecessor_pool
                and workload.get("warmup_seconds") == case.warmup_seconds
                and ((previous == 200 and not case.reconcile) or (workload.get("spread_start", False) == case.spread_start
                                        and workload.get("ramp_seconds", 0) == case.ramp_seconds
                                        and workload.get("ramp_start_vus", 50) == case.ramp_start_vus
                                        and workload.get("pool_min_idle", 2) == (predecessor_pool if case.direct_pool_test else case.pool_min_idle)))
                and (artifact.get("jar_sha256") == jar or (case.session_read_mode and case.vus == 1000
                     and artifact.get("jar_sha256") == QUALIFIED_SESSION_BASELINE_JAR))
                and (not require_routed_predecessor or case.session_read_mode
                     or (report.get("architecture") or {}).get("http_upstream_reuse") == "always")):
            passed = (report.get("status") == "passed" and report.get("sql_invariants_passed")
                      and (report.get("cleanup") or {}).get("verified")
                      and (not require_routed_predecessor or (report.get("warmup_gate") or {}).get("passed")))
            if case.http_experiment and case.vus == 2500 and not case.session_read_mode:
                # A degraded control may still authorize a bounded diagnostic ladder. It cannot qualify capacity.
                control = report.get("metrics") or {}
                passed = (report.get("sql_invariants_passed") and (report.get("cleanup") or {}).get("verified")
                          and (report.get("warmup_gate") or {}).get("passed") and control.get("duration_seconds") == STEADY_SECONDS
                          and control.get("all_unexpected") == 0 and control.get("interrupted_iterations") == 0)
            if (case.reconcile and workload.get("profile") == "mixed"
                    and artifact.get("workload_sha256") in (workload_sha, PRE_LADDER_WORKLOAD_SHA)):
                audit = report.get("reconciliation") or {}
                diagnostic_attempts.append(bool(passed and audit.get("passed")
                                                and (not case.private_scaled or audit.get("final_state_verified"))))
            elif not case.reconcile and passed:
                profiles.append(workload.get("profile"))
    if case.reconcile:
        required = 1 if previous == 200 or case.private_scaled else 2
        if len(diagnostic_attempts) < required or not all(diagnostic_attempts[:required]):
            sys.exit(f"Require reconciled mixed passes at {previous} VUs on this topology, backend and pool")
    elif not {"steady-read", "mixed"} <= set(profiles):
        sys.exit(f"Require passed read and mixed at {previous} VUs on this transport, backend and pool")
    if case.http_experiment and case.vus == 2500 and not case.diagnostic_only:
        for path in sorted(RUNS.glob("modal-*/report.json"), reverse=True):
            report = json.loads(path.read_text())
            workload = report.get("workload") or {}
            if (workload.get("diagnostic_only") and workload.get("http_experiment") == case.http_experiment
                    and workload.get("vus") == case.vus and workload.get("pool_size") == case.pool_size
                    and workload.get("session_read_mode") == case.session_read_mode
                    and report.get("artifact", {}).get("jar_sha256") == jar
                    and report.get("artifact", {}).get("workload_sha256") == workload_sha):
                if (report.get("status") == "passed" and (report.get("warmup_gate") or {}).get("passed")
                        and report.get("sql_invariants_passed") and (report.get("cleanup") or {}).get("verified")):
                    return
                break
        sys.exit("Require a qualified diagnostic ladder on the selected HTTP route")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run"))
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--suite", choices=("first",))
    selection.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--vus", type=int, choices=sorted(set(LEVELS + SESSION_LEVELS)))
    parser.add_argument("--warmup", type=int, choices=WARMUP_SECONDS, default=30)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3))
    parser.add_argument("--pool-size", type=int, choices=BASE_POOLS + SCALED_POOLS, default=10)
    parser.add_argument("--transport", choices=("connect-token", "encrypted-tunnel"), default="connect-token")
    parser.add_argument("--arrival-rate", type=int, choices=(*OPEN_RATES, *MIXED_OPEN_RATES))
    parser.add_argument("--spread-start", action="store_true")
    parser.add_argument("--ramp-seconds", type=int, choices=(20, 30, 60), default=0)
    parser.add_argument("--ramp-start-vus", type=int, choices=(1, 5, 50), default=50)
    parser.add_argument("--commit-delay-us", type=int, choices=(0, 200), default=0)
    parser.add_argument("--prefill-pool", action="store_true")
    parser.add_argument("--split-database", action="store_true")
    parser.add_argument("--diagnose-mixed", action="store_true")
    parser.add_argument("--hot-mixed", action="store_true")
    parser.add_argument("--database-path", choices=("direct", *POOLED))
    parser.add_argument("--backend-instances", type=int, choices=(1, 2, 3))
    parser.add_argument("--scale-out", action="store_true")
    parser.add_argument("--catalog-path", choices=("primary", "replica"))
    parser.add_argument("--resource-profile", choices=RESOURCE_PROFILES)
    parser.add_argument("--private-db-sidecar", action="store_true")
    parser.add_argument("--http-experiment", choices=("haproxy", "direct"))
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--session-read-mode", choices=("baseline", "autocommit"))
    parser.add_argument("--session-storage", choices=("jdbc", "redis"))
    parser.add_argument("--sidecar-cpu-cores", type=int, choices=REDIS_SIDECAR_CPU_LIMITS)
    parser.add_argument("--jvm-threads", choices=("platform", "virtual"))
    parser.add_argument("--joined-academic-reads", action="store_true")
    parser.add_argument("--hardware", choices=("larger", "ha-one", "ha-two"))
    parser.add_argument("--tomcat-threads", type=int, choices=(80, 120))
    parser.add_argument("--section-read-experiment", choices=("baseline", "joined"))
    parser.add_argument("--jdbc-security-pool", type=int, choices=(0, 4, 6))
    parser.add_argument("--shared-pool-control", action="store_true",
                        help="Use the matched 2,000-VU shared pool control for one confirmed 2,500-VU 4+20 run")
    parser.add_argument("--max-usd", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--approved-project-limit-usd", type=Decimal,
                        help="Explicitly approved cumulative pre-credit limit for one run; default USD 25")
    parser.add_argument("--confirm", action="store_true", help="Explicit approval for the selected paid plan")
    args = parser.parse_args()
    if (args.profile and args.vus is None) or (args.suite and (args.vus is not None or args.repeat is not None)):
        parser.error("Choose a preset suite or a profile and VUs")
    if args.suite and (args.pool_size != 10 or args.transport != "connect-token" or args.arrival_rate or args.spread_start
                       or args.ramp_seconds or args.ramp_start_vus != 50 or args.commit_delay_us or args.prefill_pool
                       or args.split_database or args.diagnose_mixed or args.hot_mixed or args.database_path
                       or args.backend_instances or args.scale_out or args.catalog_path or args.resource_profile
                       or args.private_db_sidecar or args.http_experiment or args.diagnostic_only or args.session_read_mode
                       or args.session_storage or args.sidecar_cpu_cores or args.jvm_threads or args.joined_academic_reads
                       or args.hardware or args.tomcat_threads or args.section_read_experiment or args.jdbc_security_pool is not None):
        parser.error("Use a single profile for a pool or transport experiment")
    run_budget_limit = Decimal("4.00") if args.resource_profile else Decimal("1.00")
    if not args.max_usd.is_finite() or not Decimal(0) < args.max_usd <= run_budget_limit:
        parser.error(f"Budget must be positive and no greater than USD {run_budget_limit}")
    if args.action == "run" and not args.confirm:
        parser.error("Review the plan and obtain explicit approval before using --confirm")
    if args.shared_pool_control and (args.action != "run" or not args.confirm or args.suite
            or args.repeat not in (None, 1) or (args.vus, args.jdbc_security_pool) != (2500, 4)):
        parser.error("The shared-pool control requires one confirmed 2,500-VU 4+20 run")
    project_limit = PROJECT_LIMIT_USD
    if args.approved_project_limit_usd is not None:
        project_limit = args.approved_project_limit_usd
        if (not project_limit.is_finite() or project_limit <= 0 or args.action != "run"
                or not args.confirm or args.suite or args.repeat not in (None, 1)):
            parser.error("An approved project limit requires a positive USD amount and one confirmed run")
    if modal.__version__ != "1.6.3" or sys.version_info[:2] != (3, 12):
        parser.error("Use Python 3.12 and Modal SDK 1.6.3")

    try:
        cases = first_suite(args.warmup) if args.suite else [
            Case(args.profile, args.vus, args.warmup, args.pool_size, args.transport, args.arrival_rate, args.spread_start,
                 args.ramp_seconds, args.prefill_pool, args.split_database, args.diagnose_mixed, args.ramp_start_vus, args.commit_delay_us,
                 args.hot_mixed, args.database_path, args.backend_instances, args.scale_out, args.catalog_path, args.resource_profile,
                 args.private_db_sidecar, args.http_experiment, args.diagnostic_only, args.session_read_mode, args.session_storage,
                 args.sidecar_cpu_cores, args.jvm_threads, args.joined_academic_reads, args.hardware, args.tomcat_threads,
                 args.section_read_experiment, args.jdbc_security_pool)
        ] * (args.repeat or 1)
    except ValueError as error:
        parser.error(str(error))
    rates = modal.Workspace.from_context().billing.rates()
    cost = budget(rates, len(cases), cases[0])
    print(json.dumps({"runs": [case.report() for case in cases], "region": REGION,
                      "limits": {**resource_limits(cases[0]), "generator_startup_seconds": K6_STARTUP, "max_concurrency": 1},
                      "cost_usd": {key: str(value) for key, value in cost.items()}}, indent=2), flush=True)
    if cost["estimate_usd"] > args.max_usd:
        sys.exit("Planning total exceeds the budget")
    if args.action == "plan":
        return
    assets_valid()
    if cases[0].database_path in POOLED and not POOLER_ASSET.is_file():
        sys.exit("Build the checksum-pinned PgBouncer bundle before running this architecture")
    if cases[0].instances is not None and not BALANCER_ASSET.is_file():
        sys.exit("Build the checksum-pinned HAProxy binary before running this architecture")
    asset_stamp = json.loads((ASSETS / "gateway-source.json").read_text()) if (ASSETS / "gateway-source.json").exists() else {}
    if asset_stamp != {"source_sha256": sha(HERE / "gateway.go"), "binary_sha256": sha(ASSETS / "gateway")}:
        sys.exit("Regenerate the benchmark gateway with prepare.py")
    stopping = False
    def interrupt(*_):
        nonlocal stopping
        if not stopping:
            stopping = True
            raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / ".lock").open("a") as lock, ExitStack() as assets:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metered = Decimal(str(cli_json("billing", "summary")["metered_cost"]))
        if not metered.is_finite() or metered < 0:
            sys.exit("The benchmark requires valid Modal metered usage")
        reserved = prior_reservations()
        projected = metered + reserved + cost["estimate_usd"]
        project_budget = {key: str(value) for key, value in (
            ("metered_usd", metered), ("reserved_usd", reserved), ("projected_usd", projected), ("limit_usd", project_limit))}
        print(json.dumps({"project_budget": project_budget}), flush=True)
        if projected > project_limit:
            sys.exit(f"The benchmark would exceed the conservative USD {project_limit} project limit")
        backend, generator, database = images_for_benchmark(cases[0].split_database, cases[0].reconcile,
                                                             cases[0].database_path, cases[0].backend_instances is not None
                                                             or cases[0].hardware in ("ha-one", "ha-two"),
                                                             cases[0].catalog_path, cases[0].resource_profile,
                                                             bool(cases[0].session_storage))
        if cases[0].private_db_sidecar:
            backend = private_db.attach_probe(backend, assets.enter_context(tempfile.TemporaryDirectory(prefix="sdu-route-probe-")))
        suite_id = secrets.token_hex(6)
        for sequence, case in enumerate(cases, 1):
            control_id = enforce_predecessor(case, shared_pool_control=args.shared_pool_control)
            active_app = any(row.get("description") in (APP_NAME, "sdu-phase8b-smoke") and row.get("state") != "stopped"
                             for row in cli_json("app", "list"))
            if active_app or cli_json("container", "list"):
                sys.exit("Modal environment is not idle. Refusing to start another App")
            if not run_case(case, cost, suite_id, sequence, backend, generator, database, lambda: stopping, project_budget,
                            control_id if args.shared_pool_control else None):
                sys.exit("Stopped at the first failed or unverified run")


if __name__ == "__main__":
    main()
