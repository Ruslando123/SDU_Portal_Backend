# Local load testing on Windows

This transfers the current application source and the existing local benchmark tools. No application rewrite, Modal account, Modal credits, or GitHub push is required. The Windows machine runs the test; the Mac does not need to remain on. All data is synthetic.

## Install once

In **PowerShell as administrator**, install Ubuntu with WSL2, then restart Windows if requested:

```powershell
wsl --install -d Ubuntu-24.04
```

Install and start [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Select the **WSL 2 based engine**, Linux containers, and enable **Settings → Resources → WSL Integration → Ubuntu-24.04**. Use the WSL2 engine, not Docker VMM, because this setup mounts files stored in Ubuntu. Do not install a second Docker Engine inside Ubuntu. [Microsoft WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install), [Docker WSL2 instructions](https://docs.docker.com/desktop/features/wsl/).

Open **Ubuntu**, create its Linux user when prompted, and install the local tools:

```bash
sudo apt update
sudo apt install -y openjdk-21-jdk python3 postgresql-client curl ca-certificates
docker version
docker compose version
```

The `postgresql-client` package supplies `psql`, not a second database server. Java, Python, Docker CLI and all test commands below must run in Ubuntu. Node.js is optional and is not needed for API load testing. Keep the computer awake during tests.

## Transfer and unpack

Copy `sdu-portal-windows.tar.gz` and its `.sha256` file into Windows **Downloads**. In Ubuntu, replace `YOUR_WINDOWS_USER` below with the actual Windows account folder:

```bash
cd /mnt/c/Users/YOUR_WINDOWS_USER/Downloads
sha256sum -c sdu-portal-windows.tar.gz.sha256
```

Extract into a **new directory on the Ubuntu filesystem**. This command refuses to overwrite an existing checkout:

```bash
if test ! -e ~/sdu-portal; then
  tar -xzf sdu-portal-windows.tar.gz -C ~ && cd ~/sdu-portal
else
  echo 'Project directory already exists. Choose a new destination before continuing.'
fi
```

The package contains source, dependency locks, unchanged migrations, docs, the Phase 6 UI snapshot, and a per-file SHA-256 manifest. It excludes existing credentials, local database state, sessions, Modal credentials, raw cloud artifacts, build output and dependencies. Dependencies and images are downloaded on the Windows machine. New private benchmark passwords are generated there. A source archive works without a Git commit or remote checkout.

## Prepare and check

From `~/sdu-portal` in Ubuntu:

```bash
bash infrastructure/benchmark/windows-local.sh prepare
bash infrastructure/benchmark/windows-local.sh smoke
```

`prepare` builds the backend, verifies/downloads the pinned k6 binary, starts the dedicated PostgreSQL, applies Flyway, seeds a new `semester-v1` fixture, and starts the backend and monitoring. Repeating it preserves existing dataset/history and refuses to rebuild while the benchmark backend is running. It never resets a populated fixture automatically.

`smoke` checks readiness and Prometheus targets, prepares 10 real authenticated sessions, runs the existing one-iteration-per-VU smoke, collects HTTP/resource/SQL evidence, and checks for remaining active enrollments. It stops the benchmark containers afterwards on success or failure, preserving their volumes and results. A failure does not authorize reset or escalation. This smoke verifies setup; it does not certify a 120-second load level.

## Monitor and stop

```bash
bash infrastructure/benchmark/windows-local.sh start
bash infrastructure/benchmark/windows-local.sh status
bash infrastructure/benchmark/windows-local.sh stop
```

While the stack is running, open Grafana at <http://127.0.0.1:13000/d/portal-phase6> and Prometheus at <http://127.0.0.1:19090/targets> in the Windows browser. Grafana user is `benchmark-admin`; view `BENCH_GRAFANA_PASSWORD` privately in `infrastructure/.local/benchmark/environment`. Do not send that file or `sessions.json` to chat. PostgreSQL, HTTP and management ports bind to loopback; management metrics also require the dedicated credential.

Results are under `infrastructure/k6/results/windows-smoke10-*`. Retain `summary.json`, `classified-results.json`, `metadata.json`, `resources.json`, `monitoring.json`, `invariants.txt` and `final-state.json`. Resource, HTTP or SQL failures remain failures. Use Ctrl+C to interrupt an active test, allow collection to finish, then check `status`. `stop` retains evidence and database history. Never use `down -v` as routine shutdown.

## What this setup measures

The source is current, but this shortcut deliberately uses the existing **local Compose benchmark**, with its own configuration:

| Property | Local setup |
| --- | --- |
| Backend | 2 CPU cap, 640 MiB RAM, 256 MiB Java heap, platform threads |
| Tomcat / HikariCP | 80 threads / 10 max connections |
| PostgreSQL | Separate local container, 1.5 CPU cap, 384 MiB RAM, 96 MiB shared buffers, durability enabled |
| HTTP sessions | JDBC, as configured in the existing local stack |
| HTTP route | Direct loopback HTTP; no Modal ingress or HAProxy |
| Database route | Private Docker network; this local stack does not reproduce the Modal TLS Sidecar |
| Dataset | `semester-v1`, 5,000 synthetic students; retained if already initialized |
| Generator | Native k6 inside WSL, sharing the Windows host with the services |

The local setup is **not the Redis + HAProxy + PostgreSQL TLS Sidecar cloud topology**. Local results cannot be presented as a matched comparison with the confirmed cloud 2,000 VUs. The old local runner also does not perform the cloud runner's complete per-operation durable reconciliation for mixed tests. Its historical SLO thresholds remain unchanged; a successful process exit is not the newer full HTTP/SQL qualification. There is no automatic escalation to 2,500 or 5,000 VUs.

After the small smoke, inspect real CPU/RAM availability and generator utilization before choosing the next load. The [Windows benchmark runbook](windows-benchmark-setup.md) and [load methodology](load-testing-methodology.md) contain explicit read, mixed, hotspot and arrival-rate commands, reset guards and evidence collection. Preserving Redis, HAProxy and the Modal Sidecar's TLS behavior would be a separate local topology configuration, not a change to enrollment business logic. Record it as a separate experiment before comparing performance.

## Verification scope

The helper's Bash syntax and preflight rejection passed on the preparation host; all nine existing Python benchmark dataset/safety tests passed. The source archive is inspected for excluded paths and verified by SHA-256. Actual Docker startup, downloads, Windows localhost forwarding and the smoke must still be verified on the destination Windows machine; they are not claimed as passed here.
