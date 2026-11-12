# Isolated local monitoring stack

The separate Compose project is `sdu-registration-benchmark`. It does not reuse the application development database or volume. PostgreSQL durability stays enabled. Database port 16432, application port 18080, management port 19091, Prometheus port 19090 and Grafana port 13000 are all published on **127.0.0.1 only**. The exporter has no host port.

Images are version pinned; resolved image identities are recorded in `images.lock.json`. PostgreSQL uses the same manifest digest as Phases 4–5. Backend Java is Temurin 21.0.13+9, limited to two CPUs and 640 MiB, with a 256-MiB heap. PostgreSQL has 1.5 CPUs/384 MiB, Prometheus 0.5 CPU/192 MiB, Grafana 0.5 CPU/256 MiB, exporter 0.25 CPU/64 MiB. These are reproducible local limits, not sizing recommendations.

From the repository root, with Docker, Python 3, psql and JDK 21 installed:

```sh
python3 infrastructure/benchmark/dataset.py init
./backend/gradlew -p backend "-Dorg.gradle.java.home=$JAVA_HOME" bootJar
# The checked-in dependency locks are already updated; ordinary builds do not use --write-locks.
docker compose --env-file infrastructure/.local/benchmark/environment -f infrastructure/monitoring/compose.yaml up -d --wait postgres
python3 infrastructure/benchmark/dataset.py migrate
python3 infrastructure/benchmark/dataset.py seed
docker compose --env-file infrastructure/.local/benchmark/environment -f infrastructure/monitoring/compose.yaml build backend
docker compose --env-file infrastructure/.local/benchmark/environment -f infrastructure/monitoring/compose.yaml up -d backend prometheus grafana postgres-exporter
```

`seed` is once per clean fixture/reset and requires the dedicated backend to be stopped. Never run destructive fixture operations while another generator is active. Safeguards check fixed loopback address/port/database/role, the Compose project and a random database marker. No arbitrary DB URL or production reset target is accepted. `reset-run` also requires the dedicated backend stopped; it clears only benchmark episodes/outcomes/audit/counters and preserves academic facts/sessions. `seed` additionally recreates the whole synthetic fixture, invalidating cached sessions. It never drops a database, volume or Flyway history.

Grafana: [local dashboard](http://127.0.0.1:13000/d/portal-phase6). Login is `benchmark-admin`; its generated password is BENCH_GRAFANA_PASSWORD in the ignored `infrastructure/.local/benchmark/environment`. The parent directory is mode 0700 and the environment/session files are mode 0600. Dedicated password-file mounts are read only. No default credential is committed.

Prometheus: [local targets](http://127.0.0.1:19090/targets). Both backend and PostgreSQL must be up, **and pg_exporter_last_scrape_error must be zero**; a reachable exporter alone does not prove its SQL collectors work. The dashboard covers HTTP RPS/p50/p95/p99/error ratio, enrollment outcomes, Hikari active/idle/pending/acquisition latency, student/section query wait, section hold/transaction duration, JVM CPU/heap/GC, PostgreSQL states/waits/I/O/SQL timing and slow statement families. Detailed normalized SQL is retained in result artifacts, never as high-cardinality Prometheus labels.

The management listener requires a distinct read-only Basic credential (`metrics`, BENCH_METRICS_PASSWORD). It is stateless and cannot authenticate a student API. For manual testing, `curl -u metrics http://127.0.0.1:19091/actuator/prometheus` prompts for the password; do not place it in command arguments/history. The same URL on the application port is denied. Health remains low detail. No public CORS or anonymous Grafana login is enabled.

The benchmark profile enables latency histograms and raises only the per-minute login preparation allowance to 6000; password verification, the 8-verification concurrency limit, CSRF, account revocation, business rules, transaction/lock timeouts and durability remain active. The default synthetic app still uses 60 login attempts/minute. A private benchmark operator role reads PostgreSQL diagnostics through pg_monitor. pg_stat_statements and pgcrypto are installed only in this dedicated diagnostic database, outside unchanged V1–V7.

Stop this stack with Compose `stop`; retain its data/evidence. Do not use `down -v` or modify the ordinary development Compose project to reset experiments.
