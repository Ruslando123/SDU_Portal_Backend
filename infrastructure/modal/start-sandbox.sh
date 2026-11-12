#!/bin/bash
set -euo pipefail
umask 077
work=/tmp/sdu-cloud
mkdir -m 755 "$work"
mkdir -m 755 "$work/jre"
pg_data="$work/pg"
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=benchmark_migrator PGDATABASE=portal_cloud_smoke
export PGPASSWORD="$DB_MIGRATION_PASSWORD"
pg() { psql -X -qAt --no-password -v ON_ERROR_STOP=1 "$@"; }
java_pid=
java_pids=()
haproxy_pid=
gateway_pid=
edge_pid=
http_sampler_pid=
sampler_pid=
bridge_pid=
pg_sampler_pid=
pgbouncer_pid=
pgbouncer_sampler_pid=
replica_sampler_pid=
redis_pid=
cleanup() {
  trap - EXIT TERM INT
  if [[ -n "$sampler_pid" ]]; then kill -TERM "$sampler_pid" 2>/dev/null || true; fi
  if [[ -n "$gateway_pid" ]]; then kill -TERM "$gateway_pid" 2>/dev/null || true; fi
  if [[ -n "$edge_pid" ]]; then kill -TERM "$edge_pid" 2>/dev/null || true; fi
  if [[ -n "$http_sampler_pid" ]]; then kill -TERM "$http_sampler_pid" 2>/dev/null || true; fi
  if [[ -n "$haproxy_pid" ]]; then
    kill -USR1 "$haproxy_pid" 2>/dev/null || true
    for ((attempt=0; attempt<12; attempt++)); do
      kill -0 "$haproxy_pid" 2>/dev/null || break
      sleep 1
    done
  fi
  for pid in "${java_pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  if [[ -n "$redis_pid" ]]; then kill -TERM "$redis_pid" 2>/dev/null || true; fi
  if [[ -n "$haproxy_pid" ]]; then
    for ((attempt=0; attempt<12; attempt++)); do
      alive=0
      for pid in "${java_pids[@]}"; do if kill -0 "$pid" 2>/dev/null; then alive=1; fi; done
      [[ "$alive" == 1 ]] || break
      sleep 1
    done
  fi
  if [[ -n "$bridge_pid" ]]; then kill -TERM "$bridge_pid" 2>/dev/null || true; fi
  if [[ -n "$pg_sampler_pid" ]]; then kill -TERM "$pg_sampler_pid" 2>/dev/null || true; fi
  if [[ -n "$pgbouncer_sampler_pid" ]]; then kill -TERM "$pgbouncer_sampler_pid" 2>/dev/null || true; fi
  if [[ -n "$replica_sampler_pid" ]]; then kill -TERM "$replica_sampler_pid" 2>/dev/null || true; fi
  if [[ "${SDU_CATALOG_PATH:-}" == replica ]] && declare -F stop_catalog_replica >/dev/null; then
    stop_catalog_replica || true
  fi
  if [[ -n "$pgbouncer_pid" ]]; then kill -INT "$pgbouncer_pid" 2>/dev/null || true; fi
  if [[ -f "$pg_data/postmaster.pid" ]]; then
    gosu postgres pg_ctl -D "$pg_data" -m fast -w -t 8 stop >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
trap 'trap - ERR; printf "%s\n" "startup failed" > "$work/failed"; sleep 25; exit 1' ERR

printf "%s\n" "Preparing runtime assets"
(umask 022; tar -xzf /opt/sdu/jre.tar.gz --strip-components=1 -C "$work/jre")
install -m 644 /opt/sdu/backend.jar "$work/backend.jar"
install -m 755 /opt/sdu/gateway "$work/gateway"
if [[ "${SDU_DB_PRIVATE_SIDECAR:-0}" == 1 ]]; then
  printf '%s\n' "$SDU_DB_TLS_CA" > "$work/db-ca.crt"
  chmod 644 "$work/db-ca.crt"
  export PGHOST=db PGSSLMODE=verify-full PGSSLROOTCERT="$work/db-ca.crt" PGCONNECT_TIMEOUT=3
  export DB_URL="jdbc:postgresql://db:5432/portal_cloud_smoke?sslmode=verify-full&sslrootcert=$work/db-ca.crt"
  connected=false
  for ((attempt=0;attempt<40;attempt++)); do
    if [[ "$(pg -c 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()' 2>"$work/private-db-connect.log")" == t ]]; then connected=true; break; fi
    sleep 1
  done
  [[ "$connected" == true ]]
  { printf '\\timing on\n'; for ((i=0;i<30;i++)); do printf 'SELECT 1;\n'; done; } | pg > "$work/db-roundtrip.log"
elif [[ "${SDU_SPLIT_APP:-0}" == 1 ]]; then
  printf '%s\n' "$SDU_DB_TLS_CA" > "$work/db-ca.crt"
  chmod 644 "$work/db-ca.crt"
  (
    unset PGPASSWORD DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_METRICS_PASSWORD SDU_STUDENT_PASSWORD SDU_GATE_KEY
    exec gosu nobody /usr/bin/python3 /opt/sdu/db-bridge.py client
  ) > "$work/db-bridge.log" 2>&1 &
  bridge_pid=$!
  export PGPORT=15432 PGSSLMODE=verify-full PGSSLROOTCERT="$work/db-ca.crt" PGCONNECT_TIMEOUT=4
  connected=false
  for ((attempt=0; attempt<8; attempt++)); do
    kill -0 "$bridge_pid"
    if [[ "$(pg -c 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()' 2>/dev/null)" == t ]]; then connected=true; break; fi
    sleep 1
  done
  [[ "$connected" == true ]]
  { printf '\\timing on\n'; for ((i=0;i<30;i++)); do printf 'SELECT 1;\n'; done; } | pg > "$work/db-roundtrip.log"
  export DB_URL="jdbc:postgresql://127.0.0.1:15432/portal_cloud_smoke?sslmode=verify-full&sslrootcert=$work/db-ca.crt"
else
install -d -m 700 -o postgres "$pg_data"
printf '%s\n' "$DB_MIGRATION_PASSWORD" > "$pg_data/init-password"
chown postgres "$pg_data/init-password"
# initdb requires an empty target, so move the password file outside PGDATA.
mv "$pg_data/init-password" "$work/init-password"
gosu postgres initdb -D "$pg_data" -U benchmark_migrator --pwfile="$work/init-password" \
  --auth-host=scram-sha-256 --auth-local=trust --locale=C.UTF-8 > "$work/postgres-init.log" 2>&1
rm -f "$work/init-password"
sdu_pg_diagnostic_options=
if [[ "${SDU_MIXED_DIAGNOSTICS:-0}" == 1 ]]; then source /opt/sdu/pg-diagnostic-settings.sh; fi
if [[ -n "${SDU_CATALOG_PATH:-}" ]]; then sdu_pg_diagnostic_options+=" -c wal_keep_size=128MB"; fi
gosu postgres pg_ctl -D "$pg_data" -l "$pg_data/server.log" -w -t 15 \
  -o "-h 127.0.0.1 -p 5432 -k '' -c shared_buffers=96MB -c max_connections=60 -c fsync=on -c synchronous_commit=on -c full_page_writes=on $sdu_pg_diagnostic_options" start >/dev/null
printf "%s\n" "PostgreSQL started"
createdb --maintenance-db=postgres "$PGDATABASE"
pg <<'SQL'
\getenv runtime DB_RUNTIME_PASSWORD
CREATE ROLE portal_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'runtime';
CREATE EXTENSION pgcrypto;
SQL
if [[ "${SDU_MIXED_DIAGNOSTICS:-0}" == 1 ]]; then
  pg -f /opt/sdu/pg-diagnostics.sql
  { printf '\\timing on\n'; for ((i=0;i<30;i++)); do printf 'SELECT 1;\n'; done; } | pg > "$work/db-roundtrip.log"
  PGAPPNAME=sdu_diagnostics bash /opt/sdu/sample-postgres.sh > "$work/pg-samples.tsv" 2>/dev/null &
  pg_sampler_pid=$!
fi
export DB_URL=jdbc:postgresql://127.0.0.1:5432/portal_cloud_smoke
fi
export SPRING_PROFILES_ACTIVE=dev,benchmark
pool_size=10
pool_min_idle=2
if [[ "${SDU_BENCHMARK:-}" == 1 ]]; then
  case "${SDU_BENCH_POOL:-10}" in
    10|16|24|32) pool_size="${SDU_BENCH_POOL:-10}" ;;
    18|20)
      [[ "${SDU_PHASE12_HAPROXY:-0}:${SDU_BACKEND_INSTANCES:-0}:${SDU_BENCH_POOL}:${PORTAL_BENCHMARK_SECURITY_POOL_SIZE:-0}" =~ ^1:1:(18:6|20:4)$ ]]
      pool_size="$SDU_BENCH_POOL" ;;
    8) [[ -n "${SDU_RESOURCE_NODE:-}" ]]; pool_size=8 ;;
    *) exit 2 ;;
  esac
  case "${SDU_BENCH_PREFILL_POOL:-0}:$pool_size" in
    0:*) ;;
    1:8|1:16) [[ "${SDU_MIXED_DIAGNOSTICS:-0}" == 1 ]]; pool_min_idle="$pool_size" ;;
    1:18|1:20|1:24|1:32) pool_min_idle="$pool_size" ;;
    *) exit 2 ;;
  esac
fi
export DB_MIGRATION_USERNAME=benchmark_migrator DB_POOL_MAX_SIZE="$pool_size" DB_POOL_MIN_IDLE="$pool_min_idle"
if [[ "${SDU_DATABASE_PATH:-direct}" != direct ]]; then
  source /opt/sdu/start-pgbouncer.sh
  setup_pgbouncer "$SDU_DATABASE_PATH"
fi
if [[ "${SDU_CATALOG_PATH:-}" == replica ]]; then
  (
    unset PGPASSWORD SDU_REPLICATION_PASSWORD SDU_CATALOG_REPLICA_PASSWORD SDU_GATE_KEY SDU_STUDENT_PASSWORD
    exec gosu nobody "$work/jre/bin/java" -Xmx160m -XX:ActiveProcessorCount=2 \
      -Dloader.main=org.example.sdu.registration.bootstrap.database.MigrationCli \
      -cp "$work/backend.jar" org.springframework.boot.loader.launch.PropertiesLauncher
  ) > "$work/backend.log" 2>&1
  source /opt/sdu/start-catalog-replica.sh
  start_catalog_replica
  export SPRING_FLYWAY_ENABLED=false
  export SDU_CATALOG_REPLICA_URL="jdbc:postgresql://127.0.0.1:5433/$PGDATABASE" SDU_CATALOG_REPLICA_USER=sdu_catalog_reader
fi
# RAM recovery must finish before readiness. Seed this isolated database before booting the owner.
if [[ "${PORTAL_WRITE_BEHIND_ENABLED:-false}" == true ]]; then
  [[ "${SDU_DB_PRIVATE_SIDECAR:-0}" == 1 ]]
  "$work/jre/bin/java" -Xmx160m -XX:ActiveProcessorCount=2 \
    -Dloader.main=org.example.sdu.registration.bootstrap.database.MigrationCli \
    -cp "$work/backend.jar" org.springframework.boot.loader.launch.PropertiesLauncher > "$work/migration.log" 2>&1
  pg -f /opt/sdu/demo.sql > "$work/seed.log" 2>&1
  if [[ "${SDU_BENCHMARK:-0}" == 1 ]]; then
    [[ "${SDU_BENCH_STUDENTS:-0}:${SDU_BENCH_SECTION_CAPACITY:-0}" == 5000:252 ]]
    pg -f /opt/sdu/bench-seed.sql >> "$work/seed.log" 2>&1
  else
    pg -f /opt/sdu/seed.sql >> "$work/seed.log" 2>&1
  fi
  export SPRING_FLYWAY_ENABLED=false SDU_MEMORY_PRESEEDED=1
fi
export SERVER_ADDRESS=127.0.0.1 SERVER_PORT=8081
if [[ "${SDU_PRIVATE_HTTP_LOOPBACK:-0}" == 1 ]]; then
  export SERVER_PORT=8082
  printf '%s\n' "$SDU_HTTP_TLS_CA" > "$work/http-ca.crt"
  printf '%s\n' "$SDU_HTTP_TLS_CERT" > "$work/http-node.crt"
  printf '%s\n' "$SDU_HTTP_TLS_KEY" > "$work/http-node.key"
  chmod 644 "$work/http-ca.crt"
  chown daemon "$work/http-node.crt" "$work/http-node.key"
  unset SDU_HTTP_TLS_CA SDU_HTTP_TLS_CERT SDU_HTTP_TLS_KEY
fi
export MANAGEMENT_SERVER_ADDRESS=127.0.0.1 MANAGEMENT_SERVER_PORT=9090
if [[ "${PORTAL_BENCHMARK_JVM_EVIDENCE:-false}" == true ]]; then
  [[ "${SDU_BENCHMARK:-0}" == 1 ]]
  install -d -m 700 -o nobody "$work/jvm-evidence"
fi
if [[ -n "${SDU_HTTP_EXPERIMENT:-}" ]]; then
  export SERVER_TOMCAT_MBEANREGISTRY_ENABLED=true
fi
export SERVER_SERVLET_SESSION_COOKIE_SECURE=true
if [[ "${PORTAL_SESSION_STORE:-jdbc}" == redis ]]; then
  source /opt/sdu/start-redis-session.sh
  start_session_redis
fi
heap_min=128
heap_max=256
processors=2
java_options=()
if [[ -n "${SDU_RESOURCE_NODE:-}" ]]; then
  [[ "$SDU_RESOURCE_NODE" == primary || "$SDU_RESOURCE_NODE" == secondary ]]
  [[ "$SDU_NODE_HEAP_MIB:$SDU_NODE_PROCESSORS" == 2048:2 || "$SDU_NODE_HEAP_MIB:$SDU_NODE_PROCESSORS" == 4096:4 ]]
  heap_min=512
  heap_max=$SDU_NODE_HEAP_MIB
  processors=$SDU_NODE_PROCESSORS
  java_options=(-Xlog:gc+init=info)
  if [[ "$SDU_RESOURCE_NODE" == secondary ]]; then export SPRING_FLYWAY_ENABLED=false; fi
fi
if [[ -n "${SDU_BACKEND_INSTANCES:-}" ]]; then
  source /opt/sdu/start-balanced.sh
  start_balanced_backends "$SDU_BACKEND_INSTANCES" "$SDU_BACKEND_HEAP_BUDGET"
  java_pid="${java_pids[0]}"
else
(
  unset PGPASSWORD SDU_GATE_KEY SDU_NODE_GATE_KEY SDU_STUDENT_PASSWORD SDU_DB_BRIDGE_KEY SDU_DB_BRIDGE_HOST SDU_DB_TLS_CA PGBOUNCER_STATS_PASSWORD SDU_REPLICATION_PASSWORD SDU_REDIS_OBSERVER_PASSWORD
  exec gosu nobody "$work/jre/bin/java" "-Xms${heap_min}m" "-Xmx${heap_max}m" -XX:MaxMetaspaceSize=192m \
    -XX:ReservedCodeCacheSize=64m "-XX:ActiveProcessorCount=$processors" -XX:+ExitOnOutOfMemoryError "${java_options[@]}" \
    -jar "$work/backend.jar"
) > "$work/backend.log" 2>&1 &
java_pid=$!
java_pids+=("$java_pid")
fi

ready=false
for ((attempt=0; attempt<60; attempt++)); do
  kill -0 "$java_pid"
  if "$work/gateway" probe; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]]
printf "%s\n" "Spring Boot database readiness is UP"
if [[ "$pool_min_idle" -gt 2 ]]; then
  expected_idle=$((pool_min_idle + ${PORTAL_BENCHMARK_SECURITY_POOL_SIZE:-0}))
  if [[ "${SDU_MEMORY_PRESEEDED:-0}" == 1 ]]; then expected_idle=$((expected_idle - 1)); fi
  connections=0
  for ((attempt=0; attempt<15; attempt++)); do
    if [[ -n "$pgbouncer_pid" || -n "${SDU_RESOURCE_NODE:-}" ]]; then
      connections=$(
        for ((i=0; i<${#java_pids[@]}; i++)); do "$work/gateway" metrics "$((9090+i))"; done |
          awk '/^hikaricp_connections_idle[{ ]/ {sum += $NF} END {print sum+0}'
      )
    else
      connections=$(pg -c "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND usename='portal_runtime' AND state='idle';")
    fi
    if [[ "$connections" == "$expected_idle" ]]; then break; fi
    sleep 1
  done
  [[ "$connections" == "$expected_idle" ]]
fi
if [[ "${SDU_MEMORY_PRESEEDED:-0}" != 1 ]]; then
if [[ "${SDU_RESOURCE_NODE:-}" != secondary ]]; then
  pg -f /opt/sdu/demo.sql > "$work/seed.log" 2>&1
fi
if [[ "${SDU_BENCHMARK:-}" == 1 ]]; then
  export SDU_BENCH_STUDENTS="${SDU_BENCH_STUDENTS:-205}" SDU_BENCH_SECTION_CAPACITY="${SDU_BENCH_SECTION_CAPACITY:-32}"
  [[ "$SDU_BENCH_STUDENTS" =~ ^[0-9]{3,4}$ && "$SDU_BENCH_STUDENTS" -ge 205 && "$SDU_BENCH_STUDENTS" -le 5000 ]]
  [[ "$SDU_BENCH_SECTION_CAPACITY" =~ ^[0-9]{2,3}$ && "$SDU_BENCH_SECTION_CAPACITY" -ge 32 && "$SDU_BENCH_SECTION_CAPACITY" -le 252 ]]
  if [[ "${SDU_RESOURCE_NODE:-}" != secondary ]]; then
    pg -f /opt/sdu/bench-seed.sql >> "$work/seed.log" 2>&1
  fi
  [[ "$(pg -c "SELECT count(*) FROM identity.test_accounts WHERE role='STUDENT' AND login_name LIKE 'cloud-bench-%';")" == "$SDU_BENCH_STUDENTS" ]]
  [[ "$(pg -c "SELECT coalesce(sum(capacity),0) FROM sections.course_sections WHERE section_code LIKE 'BENCH%';" )" == "$((20 * SDU_BENCH_SECTION_CAPACITY))" ]]
  if [[ "${SDU_CATALOG_PATH:-}" == replica ]]; then
    seed_lsn=$(pg -c 'SELECT pg_current_wal_flush_lsn()')
    [[ "$seed_lsn" =~ ^[0-9A-F]+/[0-9A-F]+$ ]]
    seed_replayed=false
    for ((attempt=0; attempt<20; attempt++)); do
      if [[ "$(pg -c "SELECT replay_lsn >= '$seed_lsn'::pg_lsn FROM pg_stat_replication WHERE application_name='sdu_catalog_replica'")" == t ]]; then
        seed_replayed=true
        break
      fi
      sleep 1
    done
    [[ "$seed_replayed" == true ]]
  fi
  if [[ -n "${SDU_CATALOG_PATH:-}" ]]; then
    bash /opt/sdu/sample-catalog.sh "$SDU_CATALOG_PATH" > "$work/catalog-samples.tsv" 2>/dev/null &
    replica_sampler_pid=$!
  fi
  if [[ -n "$haproxy_pid" ]]; then
    bash /opt/sdu/sample-balanced.sh "${java_pids[@]}" > "$work/samples.tsv" 2>/dev/null &
  else
    bash /opt/sdu/sample-sandbox.sh "$java_pid" "$redis_pid" > "$work/samples.tsv" 2>/dev/null &
  fi
  sampler_pid=$!
  if [[ -n "$pgbouncer_pid" ]]; then
    bash /opt/sdu/sample-pgbouncer.sh "$pgbouncer_pid" > "$work/pgbouncer-samples.tsv" 2>/dev/null &
    pgbouncer_sampler_pid=$!
  fi
else
  pg -f /opt/sdu/seed.sql >> "$work/seed.log" 2>&1
  [[ "$(pg -c "SELECT count(*) FROM identity.test_accounts WHERE role='STUDENT' AND login_name LIKE 'cloud-smoke-%';")" == 10 ]]
fi
fi # existing fixture creation; memory mode was seeded before JVM startup
[[ "$(pg -c "SELECT string_agg(version, ',' ORDER BY installed_rank) FROM flyway_schema_history WHERE success;")" == 1,2,3,4,5,6,7,8 ]]
[[ "$(pg -c "SELECT concat_ws(',',current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes'));")" == on,on,on ]]
(
  unset PGPASSWORD DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_METRICS_PASSWORD SDU_STUDENT_PASSWORD PGBOUNCER_STATS_PASSWORD SDU_REPLICATION_PASSWORD SDU_CATALOG_REPLICA_PASSWORD PORTAL_WRITE_BEHIND_PASSWORD SDU_HTTP_TLS_KEY
  gateway_user=nobody
  if [[ "${SDU_PRIVATE_HTTP_LOOPBACK:-0}" == 1 ]]; then
    export SDU_GATE_KEY="$SDU_NODE_GATE_KEY" SDU_PRIVATE_GATEWAY_TLS=1
    gateway_user=daemon
  fi
  unset SDU_NODE_GATE_KEY
  exec gosu "$gateway_user" "$work/gateway"
) > "$work/${SDU_NODE_GATEWAY_LOG:-gateway}.log" 2>&1 &
gateway_pid=$!
if [[ "${SDU_PRIVATE_HTTP_LOOPBACK:-0}" == 1 ]]; then
  printf '%s\n' "$gateway_pid" > "$work/node-gateway.pid"
  printf '%s\n' "$java_pid" > "$work/java.pid"
  (
    unset PGPASSWORD DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_METRICS_PASSWORD SDU_STUDENT_PASSWORD
    exec bash /opt/sdu/resource-edge.sh
  ) > "$work/edge-start.log" 2>&1 &
  edge_pid=$!
  for ((attempt=0; attempt<10; attempt++)); do
    kill -0 "$edge_pid" "$gateway_pid"
    [[ ! -f "$work/failed" ]]
    [[ ! -f "$work/edge-ready" ]] || break
    sleep 1
  done
  [[ -f "$work/edge-ready" ]]
  if [[ -n "${SDU_HTTP_EXPERIMENT:-}" ]]; then
    python3 /opt/sdu/sample-http.py > "$work/http-samples.jsonl" 2> "$work/http-samples.log" &
    http_sampler_pid=$!
  fi
fi
if [[ "${SDU_MEMORY_PRESEEDED:-0}:${SDU_BENCHMARK:-0}" == 1:1 ]]; then
  SDU_RESOURCE_NODE=primary bash /opt/sdu/sample-sandbox.sh "$java_pid" "$redis_pid" > "$work/samples.tsv" 2> "$work/samples.log" &
  sampler_pid=$!
fi
printf '%s\n' ready > "$work/ready"
printf "%s\n" "Fixture ready"
processes=("$gateway_pid")
if [[ -n "$edge_pid" ]]; then processes+=("$edge_pid"); fi
if [[ -n "$haproxy_pid" ]]; then
  processes+=("$haproxy_pid")
else
  processes+=("$java_pid")
fi
if [[ -n "$bridge_pid" ]]; then processes+=("$bridge_pid"); fi
if [[ -n "$pgbouncer_pid" ]]; then processes+=("$pgbouncer_pid"); fi
wait -n "${processes[@]}"
