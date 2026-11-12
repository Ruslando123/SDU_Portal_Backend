# Modal Cloud Smoke Deployment

Phase 8B runs a temporary synthetic smoke using the official Modal Python SDK 1.6.3. Both compute resources request the narrow `us-east` region. Modal selects the provider. A single Sandbox runs PostgreSQL 18.6 and the existing Java 21 Spring Boot JAR. A separate Modal Function starts a pinned k6 2.3.0 binary. The application is ephemeral and the Sandbox database resides on its writable local filesystem.

## Operator prerequisites

Use macOS, Linux or WSL with Python 3.12, the Gradle wrapper and JDK 21, Go, and the workspace's Modal authorization. Install the pinned SDK in an isolated environment. These commands create no Modal compute.

```bash
uv venv --python 3.12 infrastructure/.local/modal/venv
source infrastructure/.local/modal/venv/bin/activate
uv pip install -r infrastructure/modal/requirements.txt
modal --version
modal app list
modal container list
```

If the CLI is unauthenticated, run `modal setup` in your own terminal and complete authorization in the browser. Keep your Modal credentials in the CLI profile. Use the intended workspace and environment before planning a run. [Modal CLI setup](https://modal.com/docs/cli/latest/setup).

From the repository root, build the existing JAR and prepare local assets. Preparation verifies official release SHA-256 values for the Linux/amd64 JRE and k6, and compiles a static gateway. Modal receives only explicit file mounts. The stock PostgreSQL and Python registry images are digest pinned. The runner has no remote package installation, Docker build commands, persistent image snapshot, scheduled function, public app, Volume or external database.

```bash
./backend/gradlew -p backend "-Dorg.gradle.java.home=$JAVA_HOME" bootJar
python infrastructure/modal/prepare.py
python infrastructure/modal/smoke.py plan
```

## Cost gate and launch

The plan reads the workspace's current unit rates. Modal bills Sandbox CPU and memory at the higher Sandbox rate. The narrow region multiplier used here is 1.75. The run requests hard limits equal to the reservations. The generator has a single-use container with no configured retries and its autoscaling ceiling is one.

| Resource | CPU | RAM | Bounded time | Planning amount |
| --- | ---: | ---: | ---: | ---: |
| Backend and PostgreSQL Sandbox | 1.5 physical cores | 2 GiB | 240 s | $0.03043 |
| k6 Function | 0.5 physical cores | 0.5 GiB | 45 s startup, 70 s invocation, 5 s idle allowance | $0.00161 |
| Total compute | | | | $0.03205 |
| Network allowance at list rate | | | 1 GiB | $0.04000 |
| Planning total | | | | $0.07205 |

These are pre-credit estimates at the rates checked for this workspace. The traffic allowance is deliberately larger than the fixed smoke and its bounded artifacts. It is not an account-wide budget or an invoice. Use the workspace billing controls for a hard monthly spend limit. The script exits before provisioning if the current estimate exceeds the supplied ceiling. [Modal pricing](https://modal.com/pricing), [region selection](https://modal.com/docs/guide/region-selection), [Modal budgets](https://modal.com/docs/guide/budgets).

Only an authorized operator can launch via the CLI after reviewing the estimate. `--confirm` authorizes one smoke. The script refuses an already active container in the selected environment and takes a local lock. The workspace operator should coordinate concurrent launches from other machines.

```bash
python infrastructure/modal/smoke.py run --confirm --max-usd 1.00
```

## Access and lifecycle

The Sandbox starts PostgreSQL on `127.0.0.1:5432`, Spring Boot on `127.0.0.1:8081` and the management listener on `127.0.0.1:9090`. PostgreSQL keeps `fsync`, `synchronous_commit` and `full_page_writes` enabled. Spring Boot applies the original Flyway V1–V7. The readiness gate waits for PostgreSQL, Spring Boot database health, the migrations and ten synthetic students before the generator begins. The fixed smoke fixture reuses the synthetic demo course and 30-seat section.

The gateway accepts only the smoke routes on port 8080. A Modal Sandbox Connect Token authorizes the HTTPS connection and a separate randomly generated header authorizes the gateway. Neither is written to reports. There are no Sandbox TCP tunnels. Database, metrics and admin routes stay outside the gateway. The Function cannot access other Modal resources through its runtime credentials. It checks both authorization layers and confirms the student route still requires a session. Passwords and the gateway key are ephemeral Modal Secrets. The client keeps the Connect Token only in memory and supplies it as an authorization header. [Sandbox networking and Connect Tokens](https://modal.com/docs/guide/sandbox-networking), [Sandbox limits](https://modal.com/docs/sdk/py/latest/Sandbox).

Four workers perform real CSRF, login and CSRF-refresh preparation outside the timed workload. Then exactly 10 k6 VUs each execute one course-listing, enrollment, schedule, withdrawal and same-key replay journey. The timed stage has a 30-second limit. Reported RPS and percentiles describe the measured journey, including its HTTPS path, while login completion and preparation time are reported separately. Independent SQL checks require 10 withdrawn episodes, 0 active seats, 20 durable outcomes, 20 audit events and zero invariant violations.

## Evidence, monitoring and stop

Watch `infrastructure/.local/modal/runs/<run-id>/report.json` for the stage and current status. The Modal dashboard shows the App and infrastructure resource metrics. The local evidence directory retains k6 summary, bounded gzipped raw points, sanitized logs, resource samples and independent SQL checks when available. It is ignored by Git. Use the Performance Lab Cloud runs view for the normalized read-only report. Refresh the page to load newer evidence. It does not read private logs or connect to Modal.

Ctrl+C requests cleanup. The runner terminates the Sandbox in `finally`, exits the ephemeral App and verifies that the exact App is stopped with zero active Sandboxes and containers. The 240-second Sandbox deadline protects against operator disconnect or process death. For an interrupted or unverified run, read the App ID from the private report and check the exact App in Modal before doing anything else.

```bash
modal app list
modal container list --app-id <app-id>
modal app stop <app-id>
modal container list --app-id <app-id>
```

The stop command prompts for confirmation. Stop only the recorded smoke App. No local Docker resources participate in the run.

Compute usage may be delayed. The billing reconciler reads the requested UTC day, matches the exact App ID and `sdu_run` tag, and saves provider-reported metered usage before credits. Rerun it if the report still has missing billing rows. Workspace totals and net credits cannot be safely treated as one run's usage.

```bash
python infrastructure/modal/billing.py --date YYYY-MM-DD
``` [Modal billing report and attribution](https://modal.com/docs/guide/billing).

For the separately approved 10–200 VU benchmark plans, see [Phase 8C controlled cloud benchmark](modal-controlled-benchmark.md).
