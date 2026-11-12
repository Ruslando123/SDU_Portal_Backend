#!/usr/bin/env bash
# Disposable loopback PostgreSQL and Spring Boot, optionally with Next.js.
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${JAVA_HOME:?Set JAVA_HOME to a JDK 21 installation}"
export JAVA_HOME
state="$(mktemp -d "${TMPDIR:-/tmp}/sdu-student-demo.XXXXXXXX")"
container=""
backend_pid=""
secondary_backend_pid=""
balancer_pid=""
portal_pid=""
pooler_container=""
pooler_mode="${SDU_DEMO_DATABASE_PATH:-direct}"
postgres_args=(-p 127.0.0.1::5432)
replica_mode="${SDU_DEMO_CATALOG_REPLICA:-0}"
backend_instances="${SDU_DEMO_BACKEND_INSTANCES:-1}"
postgres_image=postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395
case "$pooler_mode" in
  direct) ;;
  session|transaction) postgres_args+=(-p 127.0.0.1::6432) ;;
  *) printf 'Unsupported demo database path\n' >&2; exit 2 ;;
esac
case "$backend_instances:$pooler_mode:$replica_mode" in
  1:*:*|2:direct:0) ;;
  *) printf 'Choose one demo infrastructure experiment\n' >&2; exit 2 ;;
esac
case "$replica_mode:$pooler_mode" in
  0:*) ;;
  1:direct)
    postgres_image=postgres:18.6-bookworm@sha256:02fb3245e038f33536cf13bc6c4e59f21d07b0cea3c1fe1c07c6e8aa8f083bcc
    postgres_args+=(--platform linux/amd64 -p 127.0.0.1::5433
                  --mount "type=bind,src=$root/infrastructure/scaling/replication/start.sh,dst=/replica-start.sh,readonly") ;;
  *) printf 'Choose one demo infrastructure experiment\n' >&2; exit 2 ;;
esac
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --with-portal ) ]]; then
  printf 'Usage: %s [--with-portal]\n' "$0" >&2
  exit 2
fi
cleanup() {
  if [ -n "$portal_pid" ]; then kill "$portal_pid" 2>/dev/null || true; wait "$portal_pid" 2>/dev/null || true; fi
  if [ -n "$balancer_pid" ]; then kill -USR1 "$balancer_pid" 2>/dev/null || true; wait "$balancer_pid" 2>/dev/null || true; fi
  if [ -n "$backend_pid" ]; then kill "$backend_pid" 2>/dev/null || true; wait "$backend_pid" 2>/dev/null || true; fi
  if [ -n "$secondary_backend_pid" ]; then kill "$secondary_backend_pid" 2>/dev/null || true; wait "$secondary_backend_pid" 2>/dev/null || true; fi
  if [ -n "$pooler_container" ]; then
    docker stop -t 2 "$pooler_container" >/dev/null 2>&1 || true
    docker rm "$pooler_container" >/dev/null 2>&1 || true
  fi
  if [ -n "$container" ]; then docker rm -fv "$container" >/dev/null; fi
  rm -f "$state/credentials.env"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export POSTGRES_DB=sdu_student_demo POSTGRES_USER=demo_migrator
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export DB_RUNTIME_PASSWORD="$(openssl rand -hex 24)"
export SDU_DEMO_STUDENT_PASSWORD="$(openssl rand -hex 18)"
export SDU_DEMO_ADMIN_PASSWORD="$(openssl rand -hex 18)"
export DB_MIGRATION_USERNAME="$POSTGRES_USER" DB_MIGRATION_PASSWORD="$POSTGRES_PASSWORD"
export SERVER_ADDRESS=127.0.0.1 SERVER_PORT="${SDU_DEMO_BACKEND_PORT:-18180}" SPRING_PROFILES_ACTIVE=dev
export SDU_DEMO_JAR_DIR="$state"
printf 'Demo state and logs: %s\n' "$state"
cat > "$state/demo.gradle" <<'GRADLE'
allprojects {
    tasks.matching { it.name == 'bootJar' }.configureEach {
        destinationDirectory.set(file(System.getenv('SDU_DEMO_JAR_DIR')))
    }
}
GRADLE
"$root/backend/gradlew" -p "$root/backend" --no-daemon "-Dorg.gradle.java.home=$JAVA_HOME" -I "$state/demo.gradle" bootJar > "$state/build.log" 2>&1
container="$(docker run -d --rm --memory=512m --cpus=1.5 --label org.sdu.role=student-demo \
  "${postgres_args[@]}" -e POSTGRES_DB -e POSTGRES_USER -e POSTGRES_PASSWORD \
  "$postgres_image")"
port="$(docker port "$container" 5432/tcp | sed -n 's/^127\.0\.0\.1://p')"
[ -n "$port" ]
export DB_URL="jdbc:postgresql://127.0.0.1:$port/$POSTGRES_DB"
psql_demo() {
  docker exec -i -e DB_RUNTIME_PASSWORD -e SDU_DEMO_STUDENT_PASSWORD -e SDU_DEMO_ADMIN_PASSWORD "$container" \
    psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}
for attempt in $(seq 1 50); do
  if docker exec "$container" pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then break; fi
  sleep 1
done
psql_demo <<'SQL'
\getenv runtime DB_RUNTIME_PASSWORD
CREATE ROLE portal_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'runtime';
CREATE EXTENSION pgcrypto;
SQL
if [[ "$pooler_mode" != direct ]]; then
  source "$root/infrastructure/scaling/pgbouncer/demo.sh"
  start_demo_pooler
fi
if [[ "$replica_mode" == 1 ]]; then
  source "$root/infrastructure/scaling/replication/demo.sh"
  start_demo_replica
fi
if [[ "$backend_instances" == 2 ]]; then
  source "$root/infrastructure/scaling/load-balancing/demo.sh"
  start_demo_balancer
else
  "$JAVA_HOME/bin/java" -Xms64m -Xmx256m -XX:ActiveProcessorCount=2 -jar "$state"/sdu-course-registration-backend-*.jar > "$state/backend.log" 2>&1 &
  backend_pid=$!
fi
origin="http://127.0.0.1:$SERVER_PORT"
for attempt in $(seq 1 60); do
  if curl -fsS "$origin/actuator/health/readiness" >/dev/null 2>&1; then break; fi
  kill -0 "$backend_pid"
  sleep 1
done
curl -fsS "$origin/actuator/health/readiness" >/dev/null
psql_demo < "$root/infrastructure/synthetic-demo.sql"
psql_demo < "$root/infrastructure/student-demo.sql"
if [[ "$replica_mode" == 1 ]]; then await_demo_replica; fi
if [[ "$pooler_mode" != direct && "${SDU_DEMO_POOLER_VERIFY:-0}" == 1 ]]; then verify_demo_pooler; fi
printf 'export SDU_BACKEND_ORIGIN=%s\nexport SDU_DEMO_STUDENT_PASSWORD=%s\nexport SDU_DEMO_ADMIN_PASSWORD=%s\nexport SDU_DEMO_DB_CONTAINER=%s\n' \
  "$origin" "$SDU_DEMO_STUDENT_PASSWORD" "$SDU_DEMO_ADMIN_PASSWORD" "$container" > "$state/credentials.env"
if [[ -n "$pooler_container" ]]; then printf 'export SDU_DEMO_POOLER_CONTAINER=%s\n' "$pooler_container" >> "$state/credentials.env"; fi
if [[ "$replica_mode" == 1 ]]; then printf 'export SDU_DEMO_REPLICA_CONTAINER=%s\n' "$container" >> "$state/credentials.env"; fi
if [[ "$backend_instances" == 2 ]]; then
  printf 'export SDU_DEMO_HAPROXY=1\nexport SDU_DEMO_SECONDARY_PID=%s\nexport SDU_DEMO_STATE=%s\n' \
    "$secondary_backend_pid" "$state" >> "$state/credentials.env"
fi
printf 'Ready. Source %s/credentials.env for the frontend and E2E tests. Keep this process running.\n' "$state"
if [[ "${1:-}" == --with-portal ]]; then
  portal_port="${SDU_DEMO_PORTAL_PORT:-3132}"
  (
    cd "$root/frontend"
    export SDU_BACKEND_ORIGIN="$origin" SDU_NEXT_DIST_DIR="${SDU_NEXT_DIST_DIR:-.next/release-demo}" SDU_MODAL_ADMIN_ENABLED=0
    unset SDU_DEMO_STUDENT_PASSWORD SDU_DEMO_ADMIN_PASSWORD
    exec node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port "$portal_port"
  ) > "$state/portal.log" 2>&1 &
  portal_pid=$!
  for ((attempt=0; attempt<45; attempt++)); do
    if curl -fsS "http://127.0.0.1:$portal_port/student" >/dev/null 2>&1; then break; fi
    kill -0 "$portal_pid" "$backend_pid"
    sleep 1
  done
  kill -0 "$portal_pid" "$backend_pid"
  curl -fsS "http://127.0.0.1:$portal_port/student" >/dev/null
  printf 'Student Portal: http://127.0.0.1:%s/student\n' "$portal_port"
  # macOS ships Bash 3, which lacks wait -n.
  while kill -0 "$portal_pid" "$backend_pid" 2>/dev/null; do sleep 2; done
else
  wait "$backend_pid"
fi
