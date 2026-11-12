# Final demonstration guide

## Start the isolated demo

From the repository root with Docker, JDK 21, Node.js and OpenSSL available:

```sh
export JAVA_HOME=/path/to/jdk-21
npm --prefix frontend ci
bash infrastructure/student-demo.sh --with-portal
```

This command starts a disposable PostgreSQL container, applies Flyway V1–V7, seeds the synthetic catalog and starts Spring Boot and Next.js on loopback. The script prints the location of a mode-0600 `credentials.env` file and the Student Portal URL. Default ports are 18180 for Spring and 3132 for Next.js. Override them with `SDU_DEMO_BACKEND_PORT` and `SDU_DEMO_PORTAL_PORT`. Ctrl-C stops the processes, removes the disposable database and deletes the credentials file. Keep the main demo terminal open during the walkthrough.

## Walkthrough

1. Open [Student Portal](http://127.0.0.1:3132/student) and sign in as `synthetic-demo` with the student password from the private file.
2. Search for **Data Structures**. Enable **Open seats only**, select **Demo Instructor A**, and open section **A01**.
3. Enroll and verify the server confirmation. Open **My timetable** and inspect the meeting and 3 registered credits.
4. Withdraw, confirm the seat release, and verify the timetable and credits return to their initial state.
5. Open [Cloud runs](http://127.0.0.1:3132/performance-lab/cloud). Use the run IDs in the [mixed workload report](mixed-overload-admission.md). Compare the timed HTTP gate with the durable final-state check. Expand acknowledged business operations to show successful enrollment and withdrawal rates, and inspect the Hikari, lock, billing and cleanup evidence.
6. Open [Benchmark history](http://127.0.0.1:3132/performance-lab/history) and show the **Local hotspot and spike experiments** table. The saved 2,000-attempt run shows 30 confirmations and 1,970 full-section denials. [Allocation artifact](../infrastructure/k6/results/autonomous-hot2000-late-warmed-v20/allocation.json).
7. Optionally sign in as `synthetic-peer` in a separate browser context and open **Single-seat Workshop** on both sessions. Competing requests result in one enrollment and one full-section message. Withdraw from the winning session to restore the demo seat.

## Private observability

The archive and retained Cloud Runs work offline. To demonstrate live Prometheus, start the dedicated [monitoring stack](../infrastructure/monitoring/README.md) and run the same demo command with `SDU_PROMETHEUS_ORIGIN=http://127.0.0.1:19090`. Sign in as `synthetic-admin` with the separately generated admin password, then open **Live metrics**. The live view displays the configured benchmark stack rather than the disposable demo database. In a workspace with the benchmark stack already provisioned:

```sh
docker compose --env-file infrastructure/.local/benchmark/environment -f infrastructure/monitoring/compose.yaml start postgres backend prometheus postgres-exporter
SDU_PROMETHEUS_ORIGIN=http://127.0.0.1:19090 bash infrastructure/student-demo.sh --with-portal
```

After the demo, stop the private monitoring services with the same Compose arguments and `stop`.

Paid Modal controls are disabled in the one-command demo. The [operator runbook](demo.md#modal-operator-controls) documents the separate private, explicit opt-in path. The demo only needs existing benchmark evidence.

[Verification](final-verification.md) · [Capacity report](performance-capacity-report.md) · [Known limitations](known-limitations.md)
