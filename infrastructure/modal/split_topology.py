"""Provision and inspect the isolated, authenticated benchmark database."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
from pathlib import Path
import json
import subprocess
import tempfile
import urllib.error
import urllib.request

import modal

from bench_config import DB_CPU, DB_MIB, RESOURCE_DB_CPU, RESOURCE_DB_MIB
from evidence_transport import retry_read
from smoke import HERE, REGION, execute, query
from worker import validate_origin

DIAGNOSTIC_FILES = ("pg-diagnostic-settings.sh", "pg-diagnostics.sql", "pg-lock-sample.sql", "pg-slow.sql", "sample-postgres.sh")
DB_FILES = ("split-db.sh", "db-bridge.py", *DIAGNOSTIC_FILES)


def image(base, *, baked=False):
    for source in DB_FILES:
        base = base.add_local_file(HERE / source, f"/opt/sdu/{source}", copy=baked)
    return base.add_local_file(HERE.parent.parent / "backend/verification/phase5-invariants.sql", "/opt/sdu/invariants.sql", copy=baked)


def certificate(sidecar=False, *, loopback_http=False):
    with tempfile.TemporaryDirectory(prefix="sdu-db-pki-") as temp:
        path = Path(temp)
        alt_names = "DNS:localhost" if loopback_http else "IP:127.0.0.1,DNS:db" if sidecar else "IP:127.0.0.1"
        (path / "extensions").write_text(f"subjectAltName={alt_names}\nextendedKeyUsage=serverAuth\n")
        for args in (
            ("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=SDU Disposable Benchmark CA",
             "-days", "1", "-keyout", "ca.key", "-out", "ca.crt"),
            ("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-keyout", "server.key", "-out", "server.csr"),
            ("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-set_serial", "1",
             "-days", "1", "-extfile", "extensions", "-out", "server.crt"),
        ):
            subprocess.run(["openssl", *args], cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return tuple((path / name).read_text() for name in ("ca.crt", "server.crt", "server.key"))


def create(app, base, timeout, credentials, run_id, commit_delay_us, resource_scaled=False):
    cpu, memory = (RESOURCE_DB_CPU, RESOURCE_DB_MIB) if resource_scaled else (DB_CPU, DB_MIB)
    return modal.Sandbox.create("bash", "/opt/sdu/split-db.sh", app=app, name="sdu-diagnostic-db",
        image=image(base), secrets=[credentials], region=REGION, cpu=(cpu, cpu), memory=(memory, memory),
        timeout=timeout, volumes={}, encrypted_ports=[8090], tags={"sdu_run": run_id},
        env={"SDU_DB_COMMIT_DELAY_US": str(commit_delay_us), "SDU_RESOURCE_DB": str(int(resource_scaled))})


def ready(sandbox):
    status, region, _ = execute(sandbox, "bash", "-c", """
        for ((i=0;i<55;i++)); do
          [[ ! -f /tmp/sdu-cloud/failed ]] || exit 2
          if [[ -f /tmp/sdu-cloud/ready ]]; then printf '%s' "$MODAL_REGION"; exit; fi
          sleep 1
        done
        exit 1
    """, timeout=65)
    if status != 0 or region != REGION:
        raise RuntimeError("Split database did not become ready in the selected region")
    origin = validate_origin(sandbox.tunnels(timeout=15)[8090].url)
    try:
        with urllib.request.urlopen(origin + "/database", timeout=6):
            raise RuntimeError("Database bridge accepted an unauthenticated connection")
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise RuntimeError("Database bridge did not return the required authorization rejection") from None
    return origin.removeprefix("https://")


def drain(sandbox, secret):
    query(sandbox, ["-c", """DO $$ DECLARE quiet integer := 0; BEGIN
        FOR i IN 1..24 LOOP
            PERFORM pg_stat_clear_snapshot();
            IF EXISTS (SELECT FROM pg_stat_activity WHERE usename='portal_runtime'
                       AND datname=current_database() AND (xact_start IS NOT NULL OR state='active')) THEN quiet := 0;
            ELSE quiet := quiet + 1; END IF;
            IF quiet >= 4 THEN RETURN; END IF;
            PERFORM pg_sleep(.5);
        END LOOP;
        RAISE EXCEPTION 'Application connections did not drain';
    END $$"""], secret)


def export_operations(sandbox, secret, statement, *, limit_mib=8):
    destination = "/tmp/sdu-cloud/operation-export.tsv.gz"
    status, raw_size, error = execute(sandbox, "bash", "-c", """
        set -e -o pipefail
        umask 077
        PGPASSWORD="$DB_MIGRATION_PASSWORD" psql -X -qAt -v ON_ERROR_STOP=1 \
            -h 127.0.0.1 -U benchmark_migrator -d portal_cloud_smoke -c "$1" | gzip -1 > "$2"
        wc -c < "$2"
    """, "sdu-mixed-export", statement, destination, timeout=25, secret=secret)
    if status != 0:
        raise RuntimeError(f"PostgreSQL operation export exited with {status}: {error[:600]}")
    return read_gzip_export(sandbox, destination, raw_size, limit_mib=limit_mib, label="PostgreSQL evidence")


def read_gzip_export(sandbox, destination, raw_size, *, limit_mib, label):
    return gzip.decompress(read_binary_export(sandbox, destination, raw_size, limit_mib=limit_mib, label=label)).decode("ascii")


def read_binary_export(sandbox, destination, raw_size, *, limit_mib, label):
    size = int(raw_size.strip())
    if not 0 < size <= limit_mib * 1024 * 1024:
        raise RuntimeError(f"{label} compressed export exceeded its bound")
    page_size = 512 * 1024
    if size <= page_size:
        compressed = retry_read(lambda: sandbox.filesystem.read_bytes(destination))
    else:
        # Each page is immutable and independently retrievable after a transient DNS failure.
        prefix = destination + ".part-"
        status, _, _ = execute(sandbox, "bash", "-ec",
            'umask 077; split -b "$1" -d -a 3 -- "$2" "$3"',
            "sdu-evidence-split", str(page_size), destination, prefix, timeout=12)
        if status:
            raise RuntimeError(f"{label} could not prepare binary pages")

        def page(index):
            data = retry_read(lambda: sandbox.filesystem.read_bytes(f"{prefix}{index:03d}"))
            if len(data) != min(page_size, size - index * page_size):
                raise RuntimeError(f"{label} filesystem page {index} was incomplete")
            return data

        with ThreadPoolExecutor(max_workers=4) as pool:
            compressed = b"".join(pool.map(page, range((size + page_size - 1) // page_size)))
    if len(compressed) != size:
        raise RuntimeError(f"{label} filesystem export was incomplete")
    return compressed


def read_sample_snapshot(sandbox, source, destination, label):
    status, size, _ = execute(sandbox, "bash", "-ec",
        'umask 077; gzip -1c "$1" > "$2"; wc -c < "$2"',
        "sdu-sample-export", source, destination, timeout=15)
    if status:
        raise RuntimeError(f"{label} snapshot exited with {status}")
    return read_gzip_export(sandbox, destination, size, limit_mib=4, label=label)


def postgres_evidence(sandbox, secret, path, window, *, compressed=False):
    source = "/tmp/sdu-cloud/pg-samples.tsv"
    if compressed:
        raw = read_sample_snapshot(sandbox, source, "/tmp/sdu-cloud/pg-sample-export.tsv.gz", "PostgreSQL samples")
    else:
        status, raw, _ = execute(sandbox, "cat", source, timeout=20)
        if status:
            raise RuntimeError("PostgreSQL sampling artifact unavailable")
    (path / "pg-samples.tsv").write_text(raw)
    (path / "pg-stat-statements.csv").write_text(query(sandbox, ["-f", "/opt/sdu/pg-slow.sql"], secret) + "\n")
    status, logs, _ = execute(sandbox, "awk", "/process [0-9]+ (still waiting for|acquired).*after [0-9.]+ ms/ { print }",
                              "/tmp/sdu-cloud/pg/server.log", timeout=12)
    # Only process IDs, lock names, elapsed time and PostgreSQL timestamps, no statement text.
    if status == 0:
        (path / "pg-lock-events.log").write_text(logs)
    epochs = [window.get(key) for key in ("scenario_started_ms", "observed_window_ended_ms")]
    edges = Counter()
    wait_events = Counter()
    connection_budget = Counter()
    wait_max = None
    samples = postgres_peak = pg_active = pending = cpu_total = cpu_intervals = rss_pages = 0
    stats, previous, current = {}, {}, {}
    first_stats = last_stats = None
    clock_ticks = page_size = None
    previous_ts = epoch = None
    for line in raw.rpartition("\n")[0].splitlines() + ["T\t0"]:
        kind, _, value = line.partition("\t")
        if kind == "T":
            if epoch is not None and all(x is not None for x in epochs) and epochs[0] <= epoch <= epochs[1]:
                if previous_ts is not None and previous and current:
                    cpu_intervals += 1
                    cpu_total += sum(max(0, ticks - previous.get(pid, 0)) for pid, (ticks, _) in current.items())
                if current:
                    rss_pages = max(rss_pages, sum(rss for _, rss in current.values()))
                previous = {pid: ticks for pid, (ticks, _) in current.items()}
                previous_ts = epoch
                if stats:
                    first_stats = first_stats or stats
                    last_stats = stats
                    samples += 1
                    postgres_peak = max(postgres_peak, stats["connections"])
                    pg_active = max(pg_active, stats["active"])
                    pending = max(pending, sum(n for label, n in stats["waits"].items() if label.startswith("Lock:")))
                    wait_events |= Counter(stats["waits"])
                    connection_budget |= Counter(stats.get("connection_budget", {}))
                    for edge in stats["lock_edges"]:
                        edges[edge["waiting_step"], edge["blocker_last_step"], edge["wait_event"]] += 1
                        if edge["wait_ms"] is not None:
                            wait_max = max(wait_max or 0, edge["wait_ms"])
            epoch = int(value)
            current, stats = {}, {}
        elif kind == "P":
            stats = json.loads(value)
        elif kind == "M":
            label, raw_value = value.split()
            if label == "sdu_pg_ticks_per_second":
                clock_ticks = int(raw_value)
            elif label == "sdu_pg_page_size":
                page_size = int(raw_value)
        elif kind == "O":
            pid, ticks, rss = map(int, value.split())
            current[pid] = ticks, rss
    wal = {}
    if samples >= 2:
        for group in ("wal", "wal_io"):
            first, last = first_stats.get(group, {}), last_stats.get(group, {})
            if first.get("reset") == last.get("reset"):
                wal.update({name: value - first[name] for name, value in last.items() if name != "reset"
                            and isinstance(value, (int, float)) and isinstance(first.get(name), (int, float)) and value >= first[name]})
    return {"postgres_samples": samples, "postgres_connections_peak": postgres_peak if samples else None,
            "postgres_connection_budget_peaks": dict(connection_budget) if connection_budget else None,
            "postgres_active_peak": pg_active if samples else None, "postgres_lock_waits_peak": pending if samples else None,
            "postgres_wait_events": dict(wait_events) if samples else None,
            "postgres_wal_window": wal or None,
            "postgres_lock_wait_max_ms": wait_max,
            "postgres_process_cpu_seconds": cpu_total / clock_ticks if cpu_intervals and clock_ticks else None,
            "postgres_process_rss_sum_peak_mib": rss_pages * page_size / 2**20 if rss_pages and page_size else None,
            "postgres_lock_edges": [{"waiter": waiter, "blocker_last": blocker, "event": event, "samples": count}
                                    for (waiter, blocker, event), count in edges.most_common()]}
