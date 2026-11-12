#!/usr/bin/env bash
# Convenience entry point for the existing isolated local benchmark, inside WSL2.
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
action="${1:-help}"
if [[ $# -gt 1 ]]; then echo 'Expected one command: prepare, start, smoke, status, or stop.' >&2; exit 2; fi
case "$action" in
  help|--help|-h)
    echo 'Usage: bash infrastructure/benchmark/windows-local.sh {prepare|start|smoke|status|stop}'
    echo 'prepare builds and starts the isolated stack; smoke runs only 10 VUs and stops it.'
    echo 'Existing data/results are retained. No Modal calls or automatic load escalation.'
    exit 0 ;;
  prepare|start|smoke|status|stop) ;;
  *) echo 'Unknown command. Use --help.' >&2; exit 2 ;;
esac
[[ "$(uname -s)" == Linux ]] || { echo 'Run this command inside Ubuntu on WSL2.' >&2; exit 1; }
for tool in docker python3 flock; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; follow docs/windows-local-quickstart.md." >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
docker compose version >/dev/null
endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
endpoint="${DOCKER_HOST:-$endpoint}"
[[ "$endpoint" == unix://* ]] || { echo 'Select the local Docker Desktop WSL2 socket, not a remote Docker endpoint.' >&2; exit 1; }
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo 'Switch Docker Desktop to Linux containers.' >&2; exit 1; }
local_dir="$root/infrastructure/.local/benchmark"
mkdir -p "$local_dir"
exec 9> "$local_dir/windows-local.lock"
flock -n 9 || { echo 'Another local helper is running; wait for it to finish or use Ctrl+C.' >&2; exit 1; }
bench() { docker compose --env-file "$local_dir/environment" -f infrastructure/monitoring/compose.yaml "$@"; }
if [[ "$action" != prepare && ! -f "$local_dir/environment" ]]; then
  echo 'Run prepare first.' >&2; exit 1
fi
case "$action" in
  stop) bench stop; bench ps; exit 0 ;;
  status) bench ps; exit 0 ;;
esac
export PYTHONPATH="$root/infrastructure/benchmark"
stop_on_exit=0
finish() {
  result=$?
  trap - EXIT
  if [[ "$stop_on_exit" == 1 ]]; then
    if ! bench stop; then echo 'Could not confirm shutdown. Run the stop command again.' >&2; result=1; fi
    if [[ -n "$(bench ps --status running -q)" ]]; then echo 'Benchmark containers are still running.' >&2; result=1; fi
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$action" == prepare ]]; then
  for tool in javac psql curl; do
    command -v "$tool" >/dev/null || { echo "Missing $tool; follow docs/windows-local-quickstart.md." >&2; exit 1; }
  done
  export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}"
  [[ "$("$JAVA_HOME/bin/javac" -version 2>&1)" == 'javac 21.'* ]] || { echo 'Select JDK 21 in JAVA_HOME.' >&2; exit 1; }
  python3 infrastructure/benchmark/dataset.py init
  [[ -z "$(bench ps --status running -q backend)" ]] || { echo 'Stop the existing benchmark before prepare; data will be retained.' >&2; exit 1; }
  ./backend/gradlew -p backend "-Dorg.gradle.java.home=$JAVA_HOME" --no-daemon bootJar
  python3 infrastructure/benchmark/install-k6.py
  stop_on_exit=1
  bench up -d --wait postgres
  python3 infrastructure/benchmark/dataset.py migrate
  if [[ ! -f "$local_dir/dataset.json" ]]; then
    python3 - <<'PY'
from common import guard, sql
guard(require_stopped=True)
if sql('SELECT count(*) FROM identity.test_accounts;') != '0':
    raise RuntimeError('Database already contains accounts but the local manifest is missing; refusing automatic reseed')
PY
    python3 infrastructure/benchmark/dataset.py seed --variant semester-v1
  else
    echo 'Existing dataset and operation history retained; no reset performed.'
  fi
  bench build backend
fi
[[ -f "$local_dir/dataset.json" ]] || { echo 'Dataset is missing. Run prepare.' >&2; exit 1; }
command -v psql >/dev/null || { echo 'Install postgresql-client inside WSL2.' >&2; exit 1; }
stop_on_exit=1
bench up -d --wait postgres
python3 -c 'from common import guard; guard()'
bench up -d backend prometheus grafana postgres-exporter
python3 - <<'PY'
import json, time, urllib.parse, urllib.request
deadline = time.monotonic() + 120
def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)
while time.monotonic() < deadline:
    try:
        healthy = get('http://127.0.0.1:19091/actuator/health/readiness')['status'] == 'UP'
        query = '(up{job="backend"} == 1) and on() (up{job="postgres"} == 1) and on() (pg_exporter_last_scrape_error == 0)'
        monitored = get('http://127.0.0.1:19090/api/v1/query?' + urllib.parse.urlencode({'query': query}))
        if healthy and monitored.get('status') == 'success' and monitored['data']['result']:
            print('Backend, PostgreSQL, and monitoring are ready.')
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(2)
else:
    raise RuntimeError('Readiness failed. Inspect private Compose logs; no load test was started.')
PY
if [[ "$action" == smoke ]]; then
  [[ -x "$local_dir/tools/k6/k6" ]] || { echo 'Pinned k6 is missing. Run prepare.' >&2; exit 1; }
  python3 infrastructure/benchmark/dataset.py sessions --count 10
  run_name="windows-smoke10-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  python3 infrastructure/benchmark/run.py --mode smoke --vus 10 --max-vus 10 \
    --resource-budget-vus 10 --wall-clock-seconds 180 --name "$run_name"
  python3 - "$run_name" <<'PY'
import json, sys
from common import RESULTS, sql
active = int(sql("SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE';"))
(RESULTS / sys.argv[1] / 'final-state.json').write_text(json.dumps({'active_enrollments': active, 'passed': active == 0}, indent=2))
if active:
    raise RuntimeError('Active enrollments remain. Preserve evidence and investigate before resetting or increasing load.')
PY
  echo "Smoke completed; evidence: infrastructure/k6/results/$run_name"
  echo 'Stopping the local stack and retaining its data. This is not a capacity qualification.'
else
  stop_on_exit=0
  echo 'Local stack running. Grafana: http://127.0.0.1:13000; Prometheus: http://127.0.0.1:19090'
  echo 'Next: bash infrastructure/benchmark/windows-local.sh smoke'
  echo 'Stop: bash infrastructure/benchmark/windows-local.sh stop'
fi
