#!/bin/bash
set -euo pipefail
umask 077
work=/tmp/sdu-cloud
pg_data="$work/pg"
mkdir -m 755 "$work"
install -d -m 700 -o postgres "$pg_data"
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=benchmark_migrator PGDATABASE=portal_cloud_smoke
export PGPASSWORD="$DB_MIGRATION_PASSWORD" PGCONNECT_TIMEOUT=3
pg() { psql -X -qAt --no-password -v ON_ERROR_STOP=1 "$@"; }
sampler=
bridge=
cleanup() {
  if [[ -n "$sampler" ]]; then kill -TERM "$sampler" 2>/dev/null || true; fi
  if [[ -n "$bridge" ]]; then kill -TERM "$bridge" 2>/dev/null || true; fi
  if [[ -f "$pg_data/postmaster.pid" ]]; then
    gosu postgres pg_ctl -D "$pg_data" -m fast -w -t 8 stop >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
trap 'trap - ERR; printf "database startup failed at line %s\n" "$LINENO" >&2; touch "$work/failed"; sleep 20; exit 1' ERR
printf '%s\n' "$DB_MIGRATION_PASSWORD" > "$work/init-password"
chown postgres "$work/init-password"
gosu postgres initdb -D "$pg_data" -U benchmark_migrator --pwfile="$work/init-password" \
  --auth-host=scram-sha-256 --auth-local=trust --locale=C.UTF-8 > "$work/postgres-init.log" 2>&1
rm "$work/init-password"
printf '%s\n' "$SDU_DB_TLS_CERT" > "$pg_data/server.crt"
printf '%s\n' "$SDU_DB_TLS_KEY" > "$pg_data/server.key"
chown postgres "$pg_data/server.crt" "$pg_data/server.key"
cat > "$pg_data/pg_hba.conf" <<'EOF'
hostssl all all 127.0.0.1/32 scram-sha-256
EOF
listen=127.0.0.1
if [[ "${SDU_DB_PRIVATE_SIDECAR:-0}" == 1 ]]; then
  app_ip=$(getent ahostsv4 main | awk 'NR==1{print $1}')
  db_ip=$(getent ahostsv4 db | awk 'NR==1{print $1}')
  for ip in "$app_ip" "$db_ip"; do
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ && "$ip" != 127.* && "$ip" != 0.0.0.0 ]]
  done
  [[ "$app_ip" != "$db_ip" ]]
  # Modal NATs same-Sandbox bridge traffic through the Sidecar's private gateway.
  bridge_peer=$(python3 - <<'PY'
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

routes = [line.split() for line in Path("/proc/net/route").read_text().splitlines()[1:]]
gateways = [row[2] for row in routes if row[1] == "00000000" and int(row[3], 16) & 2]
if len(gateways) != 1:
    raise SystemExit("Expected exactly one private bridge gateway")
address = IPv4Address(bytes.fromhex(gateways[0])[::-1])
private = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
if not any(address in IPv4Network(block) for block in private):
    raise SystemExit("Bridge gateway is outside RFC 1918")
print(address)
PY
)
  listen+=",$db_ip"
  printf 'hostssl portal_cloud_smoke portal_runtime,benchmark_migrator %s/32 scram-sha-256\n' "$bridge_peer" >> "$pg_data/pg_hba.conf"
fi
source /opt/sdu/pg-diagnostic-settings.sh
shared_buffers=96MB
if [[ "${SDU_RESOURCE_DB:-0}" == 1 ]]; then
    shared_buffers=1024MB
    sdu_pg_diagnostic_options+=" -c effective_cache_size=4096MB"
fi
gosu postgres pg_ctl -D "$pg_data" -l "$pg_data/server.log" -w -t 15 -o \
  "-h $listen -p 5432 -k '' -c ssl=on -c shared_buffers=$shared_buffers -c max_connections=60 -c fsync=on -c synchronous_commit=on -c full_page_writes=on $sdu_pg_diagnostic_options" start >/dev/null
createdb --maintenance-db=postgres "$PGDATABASE"
pg <<'SQL'
\getenv runtime DB_RUNTIME_PASSWORD
CREATE ROLE portal_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'runtime';
CREATE EXTENSION pgcrypto;
SQL
pg -f /opt/sdu/pg-diagnostics.sql
export PGAPPNAME=sdu_diagnostics
bash /opt/sdu/sample-postgres.sh > "$work/pg-samples.tsv" 2>/dev/null &
sampler=$!
if [[ "${SDU_DB_PRIVATE_SIDECAR:-0}" == 1 ]]; then
  printf '%s\n' ready > "$work/ready"
  wait "$sampler"
  exit
fi
(
  unset DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PGPASSWORD SDU_DB_TLS_KEY SDU_DB_TLS_CERT
  exec gosu nobody /usr/bin/python3 /opt/sdu/db-bridge.py server
) > "$work/db-bridge.log" 2>&1 &
bridge=$!
sleep 1
kill -0 "$bridge"
printf '%s\n' ready > "$work/ready"
wait -n "$sampler" "$bridge"
