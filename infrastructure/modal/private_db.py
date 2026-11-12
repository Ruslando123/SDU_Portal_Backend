"""Documented Modal Sidecar database and bounded private route verification."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event, Thread
import zipfile

from smoke import HERE, JAR, VIOLATIONS, execute, query

JDBC_PATH = "jdbc:postgresql://db:5432/portal_cloud_smoke?sslmode=verify-full&sslrootcert=/tmp/sdu-cloud/db-ca.crt"


def attach_probe(image, directory):
    directory = Path(directory)
    javac = shutil.which("javac")
    if not javac:
        raise RuntimeError("Install JDK 21 to compile the JDBC route probe")
    subprocess.run([javac, "--release", "21", "-d", directory, HERE / "JdbcRouteProbe.java"], check=True, timeout=20)
    with zipfile.ZipFile(JAR) as jar:
        drivers = [name for name in jar.namelist() if name.startswith("BOOT-INF/lib/postgresql-")]
        if len(drivers) != 1:
            raise RuntimeError("Expected exactly one PostgreSQL JDBC driver in the benchmark JAR")
        (directory / "postgresql.jar").write_bytes(jar.read(drivers[0]))
    for name in ("JdbcRouteProbe.class", "postgresql.jar"):
        image = image.add_local_file(directory / name, f"/opt/sdu/{name}")
    return image


def create(main, built_database_image, credentials):
    return main._experimental_sidecars.create(
        "bash", "-c", "exec bash /opt/sdu/split-db.sh >/tmp/sdu-private-db-startup.log 2>&1",
        name="db", image=built_database_image, secrets=[credentials], outbound_cidr_allowlist=[],
        env={"SDU_RESOURCE_DB": "1", "SDU_DB_PRIVATE_SIDECAR": "1", "SDU_DB_COMMIT_DELAY_US": "0"},
    )


def verify(backend, database, db_secret):
    statement = """SELECT
        current_setting('listen_addresses') ~ '^127[.]0[.]0[.]1,[0-9.]+$'
        AND current_setting('shared_buffers')='1GB'
        AND current_setting('effective_cache_size')='4GB'
        AND current_setting('max_connections')='60'
        AND current_setting('commit_delay')='0'
        AND (SELECT count(*)=2 AND bool_and(type='hostssl' AND auth_method='scram-sha-256'
                 AND netmask='255.255.255.255' AND error IS NULL) FROM pg_hba_file_rules);"""
    if query(database, ["-c", statement], db_secret) != "t":
        raise RuntimeError("Private database settings and exact TLS/SCRAM allowlist verification failed")
    status, checks, _ = execute(backend, "bash", "-c", """
        set -euo pipefail
        export PGPASSWORD="$DB_RUNTIME_PASSWORD" PGHOST=db PGDATABASE=portal_cloud_smoke PGUSER=portal_runtime
        export PGCONNECT_TIMEOUT=3 PGSSLROOTCERT=/tmp/sdu-cloud/db-ca.crt PGSSLMODE=verify-full
        pg() { psql -XqAt --no-password -v ON_ERROR_STOP=1 -c "$1"; }
        [[ "$(pg 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')" == t ]]
        reject() {
          local expected=$1 output
          shift
          if output=$("$@" psql -XqAt --no-password -v ON_ERROR_STOP=1 -c 'SELECT 1' 2>&1); then exit 1; fi
          [[ "$output" == *"$expected"* ]]
        }
        ip=$(getent ahostsv4 db | awk 'NR==1{print $1}')
        reject 'no pg_hba.conf entry' env PGSSLMODE=disable
        reject 'does not match host name' env PGHOST=wrong-db PGHOSTADDR="$ip"
        reject 'password authentication failed' env PGPASSWORD=invalid-password
        printf '%s\n' verified
    """, timeout=18, secret=db_secret)
    if status or checks.strip() != "verified" or set(backend.tunnels()) != {8080}:
        raise RuntimeError("Private database TLS and negative probes did not pass")
    status, result, _ = execute(backend, "/tmp/sdu-cloud/jre/bin/java", "-Xmx32m", "-cp",
                                "/opt/sdu:/opt/sdu/postgresql.jar", "JdbcRouteProbe", JDBC_PATH,
                                timeout=25, secret=db_secret)
    if status:
        raise RuntimeError("JDBC route probe did not complete")
    return json.loads(result)


def wait_ready(database):
    status, _, _ = execute(database, "bash", "-c", """
        for ((attempt=0;attempt<45;attempt++)); do
          [[ ! -f /tmp/sdu-cloud/failed ]] || exit 2
          [[ ! -f /tmp/sdu-cloud/ready ]] || exit 0
          sleep 1
        done
        exit 1
    """, timeout=50)
    if status:
        raise RuntimeError(f"PostgreSQL Sidecar readiness failed with code {status}")


def verify_fixture(database, secret, case):
    state = json.loads(query(database, ["-c", """SELECT json_build_object(
        'students', (SELECT count(*) FROM identity.test_accounts WHERE role='STUDENT' AND login_name LIKE 'cloud-bench-%'),
        'sections', count(*), 'capacity', coalesce(sum(capacity),0), 'min_capacity', min(capacity),
        'active', (SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'),
        'durable', current_setting('fsync')='on' AND current_setting('synchronous_commit')='on'
                   AND current_setting('full_page_writes')='on')
        FROM sections.course_sections WHERE section_code LIKE 'BENCH%';"""], secret))
    if (state["students"] != case.students or state["sections"] != 20 or state["capacity"] != 20 * case.section_capacity
            or state["min_capacity"] < (case.vus + 19) // 20 or state["active"] or not state["durable"]):
        raise RuntimeError("Private benchmark fixture, per-section headroom or durability preflight failed")
    return state


def verify_quotas(quota, limits):
    for role, (cores, mib) in limits.items():
        observed = quota.get(role) or {}
        v2 = observed.get("cpu.max", "").split() if isinstance(observed.get("cpu.max"), str) else []
        numerator, period = (map(int, v2) if len(v2) == 2 and v2[0].isdigit() and v2[1].isdigit()
                             else (observed.get("cpu/cpu.cfs_quota_us"), observed.get("cpu/cpu.cfs_period_us")))
        memory = observed.get("memory.max", observed.get("memory/memory.limit_in_bytes"))
        if not numerator or not period or numerator != period * cores or memory != mib * 1024**2:
            raise RuntimeError(f"Private benchmark quota mismatch for {role}")


@contextmanager
def invariant_watch(database, edge, secret, state):
    """Sample committed PostgreSQL state without taking locks on writes."""
    ended = Event()

    def watch():
        consecutive_misses = 0
        while not ended.is_set():
            reason = None
            try:
                rows = query(database, ["-c", "SET statement_timeout='3s'", "-f", "/opt/sdu/invariants.sql"], secret).splitlines()
                counts = dict(zip(VIOLATIONS, map(int, rows[:len(VIOLATIONS)]), strict=True))
                state["snapshots"] += 1
                consecutive_misses = 0
                if any(counts.values()):
                    reason = "SQL invariant violation"
                    state["violations"] = counts
            except Exception:
                state["missed_snapshots"] += 1
                consecutive_misses += 1
                if consecutive_misses == 3:
                    reason = "SQL safety observations unavailable"
            if reason and not ended.is_set():
                state["stop_reason"] = reason
                try:
                    # Halt requests while retaining the edge and PostgreSQL evidence.
                    status, _, _ = execute(edge, "bash", "-c", """
                        if [[ -r /tmp/sdu-cloud/edge-gateway.pid ]]; then
                          read -r pid < /tmp/sdu-cloud/edge-gateway.pid
                          if [[ "$pid" =~ ^[0-9]+$ && "$(cat /proc/$pid/comm)" == gateway ]]; then
                            kill -STOP "$pid" && exit 0
                          fi
                        fi
                        for file in /proc/[0-9]*/comm; do
                          if read -r name < "$file" && [[ "$name" == haproxy ]]; then
                            pid=${file#/proc/}
                            kill -STOP "${pid%/comm}" && exit 0
                          fi
                        done
                        exit 1
                    """, timeout=5)
                    state["edge_halted"] = status == 0
                except Exception:
                    state["edge_halted"] = False
                return
            if ended.wait(20):
                return

    state.update(snapshots=0, missed_snapshots=0)
    worker = Thread(target=watch, name="private-db-invariants", daemon=True)
    worker.start()
    try:
        yield
    finally:
        ended.set()
        worker.join(timeout=20)
