# Reproducible local demonstration

The demo uses synthetic accounts and a disposable PostgreSQL 18 container. All listeners bind to loopback. The benchmark monitoring stack uses its own fixture and telemetry.

## Student Portal

Prerequisites are JDK 21, Docker, Node.js 22.6 or later, OpenSSL and Bash. From the repository root:

```sh
export JAVA_HOME=/path/to/jdk-21
bash infrastructure/student-demo.sh
```

Keep that terminal open. The script builds the demo JAR in its own temporary directory, starts a fresh PostgreSQL instance on an assigned loopback port, applies Flyway V1–V7, and seeds published courses, sections, instructors and two synthetic accounts. It prints a state directory with a private `credentials.env`. Spring defaults to port 18180. Use `SDU_DEMO_BACKEND_PORT` to choose a free port.

In a second terminal, source the file path printed by the script and start Next.js:

```sh
. /path/printed/by/demo/credentials.env
cd frontend
npm ci
npm run dev -- --port 3132
```

If a Next.js development server already uses this checkout, set `SDU_NEXT_DIST_DIR=.next/student-demo` in the second terminal. Open [Student Portal](http://127.0.0.1:3132/student). Use `synthetic-demo` with the generated student password from the private file.

1. Search for **Data Structures**, turn on **Open seats only**, then choose **Demo Instructor A**.
2. Register for **A01**. The seat snapshot is advisory until the server confirms.
3. Open **My timetable** to see the meeting and the change from 0 to 3 registered credits.
4. Withdraw, confirm the seat release, and sign out.
5. Sign in as `synthetic-admin` with the separate generated admin password, then open **Performance Lab**.

For the fault-recovery demonstration, the Playwright student test drops the first confirmed server reply and returns a 503 to the browser. Reloading preserves the pending request. Retrying uses the original key and returns the original committed enrollment before verifying the timetable and withdrawal.

Registration commands require browser storage and the Web Locks API. They use a principal-specific lock so two tabs cannot create different keys for an unresolved operation.

Stop the Next.js process and then the demo script with Ctrl-C. The script terminates its Java process, removes its PostgreSQL container and anonymous volume, and removes the generated credentials file. Build and backend logs remain in the private state directory. The script does not use the development or benchmark database.

## Performance Lab

Open [Cloud runs](http://127.0.0.1:3132/performance-lab/cloud) to select a measured or failed Modal run. Inspect outcomes, window charts, sampled resource evidence, SQL violations and billing/cleanup. [Benchmark history](http://127.0.0.1:3132/performance-lab/history) retains Phase 6 comparisons.

For the live tab, start the [separate monitoring stack](../infrastructure/monitoring/README.md) and set `SDU_PROMETHEUS_ORIGIN=http://127.0.0.1:19090` when starting Next.js. Use the synthetic ADMIN session on the demo backend. The charts show the configured benchmark stack, which has its own Spring and PostgreSQL services. Missing metrics show **No Data**. Stop that Compose stack when finished.

### Modal operator controls

The optional controls require the private single-process Node.js server, Modal SDK 1.6.3 in Python 3.12, an authenticated operator environment, verified runner assets and `SDU_MODAL_ADMIN_ENABLED=1`. Set `SDU_MODAL_PYTHON` to the absolute path of that Python executable. Keep Next.js on loopback and start it from `frontend/`. For reverse-proxied deployments, explicitly pin the public origin with `SDU_PORTAL_ORIGIN` and review the deployment security separately.

Only a confirmed, same-origin command with a real ADMIN session and valid CSRF can start or stop an allowlisted run. Starts use one profile, one VU level, a USD 0.20 estimate ceiling and the CLI's global reserve and concurrency checks. Stop sends SIGTERM to the exact child started by that Node.js process, allowing the runner to perform cleanup. The UI's command status is process-local. After a Node restart or unexpected host loss, consult the retained cloud reports and the [operator runbook](modal-controlled-benchmark.md) for verification and recovery. Keep Modal credentials in the operator environment. The browser receives only projected job and evidence fields.

To verify the lifecycle with approved cloud usage, sign in as the synthetic administrator, choose **Steady read** and **10 VUs**, then use **Review and start run**. Once the new run reaches the generator stage, choose **Request stop** and confirm. The saved receipt should show `interrupted`, the App should be stopped, and cleanup should verify zero active Sandboxes and containers. An interruption is an operator check with no benchmark result.

## Verification commands

```sh
# From the backend directory with Docker and JDK 21.
./gradlew test -PstressWorkers=4 correctnessStress

# From the frontend directory with the demo credentials already sourced.
npm test
npm run typecheck
npx playwright install chromium
PLAYWRIGHT_BASE_URL=http://127.0.0.1:3132 SDU_PROMETHEUS_ORIGIN=http://127.0.0.1:19090 npm run test:ui
npm run build
```

Omit `SDU_PROMETHEUS_ORIGIN` in both the server and test command if the monitoring stack is unavailable. The live integration case then skips. Run the operator security test with the paid controls disabled.

[Architecture](architecture.md) · [Database ERD](database-erd.md) · [API contract](api-specification.md) · [Benchmark results](autonomous-verification.md)
