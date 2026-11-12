#!/bin/bash
# Run with the pinned PostgreSQL image, no published ports and a disposable filesystem.
set -euo pipefail
umask 077
work=/tmp/sdu-replica-verification
pg_data="$work/primary"
install -d -m 755 "$work"
export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=portal_cloud_smoke PGUSER=benchmark_migrator
export PGPASSWORD="$DB_MIGRATION_PASSWORD" PGCONNECT_TIMEOUT=3
pg() { psql -XqAt --no-password -v ON_ERROR_STOP=1 "$@"; }
source /experiment/start.sh
cleanup() {
    local code=$? log line
    if ((code)); then
        for log in "$pg_data/primary.log" "$work/replica/standby.log"; do
            if [[ -f "$log" ]]; then
                while IFS= read -r line; do
                    line=${line//"$DB_MIGRATION_PASSWORD"/[REDACTED]}
                    line=${line//"$SDU_REPLICATION_PASSWORD"/[REDACTED]}
                    line=${line//"$SDU_CATALOG_REPLICA_PASSWORD"/[REDACTED]}
                    printf '%s\n' "$line" >&2
                done < <(tail -12 "$log")
            fi
        done
    fi
    stop_catalog_replica || true
    if [[ -f "$pg_data/postmaster.pid" ]]; then gosu postgres pg_ctl -D "$pg_data" -m fast -w stop >/dev/null; fi
}
trap cleanup EXIT
install -d -m 700 -o postgres "$pg_data"
printf '%s\n' "$DB_MIGRATION_PASSWORD" > "$work/password"
chown postgres:postgres "$work/password"
gosu postgres initdb -D "$pg_data" -U benchmark_migrator --pwfile="$work/password" \
    --auth-host=scram-sha-256 --auth-local=trust --locale=C.UTF-8 >/dev/null
start_primary() {
    gosu postgres pg_ctl -D "$pg_data" -l "$pg_data/primary.log" -w -t 15 \
      -o "-h 127.0.0.1 -p 5432 -k '' -c shared_buffers=48MB -c wal_level=replica -c wal_keep_size=128MB -c max_connections=60" start >/dev/null
}
start_primary
createdb --maintenance-db=postgres "$PGDATABASE"
pg <<'SQL'
REVOKE CONNECT ON DATABASE portal_cloud_smoke FROM PUBLIC;
CREATE SCHEMA catalog;
CREATE SCHEMA rules;
CREATE TABLE catalog.courses(course_id int PRIMARY KEY, code text);
CREATE TABLE catalog.course_offerings(offering_id int PRIMARY KEY, course_id int REFERENCES catalog.courses,
                                     term_id int, publication_status text, title text, credits numeric);
CREATE TABLE rules.term_policy_gates(term_id int PRIMARY KEY, configuration_state text);
INSERT INTO catalog.courses VALUES (1, 'VERIFY');
INSERT INTO rules.term_policy_gates VALUES (1, 'PUBLISHED');
INSERT INTO catalog.course_offerings VALUES (1,1,1,'PUBLISHED','Before',3);
SQL
start_catalog_replica
printf '%s\n' 'Replica entered streaming recovery'
read_catalog() {
    PGPASSWORD="$SDU_CATALOG_REPLICA_PASSWORD" psql -XqAt --no-password -v ON_ERROR_STOP=1 \
      -p 5433 -U sdu_catalog_reader -c 'SELECT title FROM replica_catalog.published_courses;'
}
await_title() {
    local expected=$1 i
    for ((i=0;i<25;i++)); do
        [[ "$(read_catalog 2>/dev/null)" != "$expected" ]] || return 0
        sleep 1
    done
    return 1
}
await_title Before
if PGPASSWORD="$SDU_CATALOG_REPLICA_PASSWORD" psql -XqAt --no-password -v ON_ERROR_STOP=1 \
    -p 5433 -U sdu_catalog_reader -c "UPDATE replica_catalog.published_courses SET title='Forbidden';" >/dev/null 2>&1; then
    exit 2
fi
if PGPASSWORD="$SDU_CATALOG_REPLICA_PASSWORD" psql -XqAt --no-password -v ON_ERROR_STOP=1 \
    -p 5432 -U sdu_catalog_reader -c 'SELECT * FROM catalog.courses' >/dev/null 2>&1; then
    exit 3
fi
pg -p 5433 -c 'SELECT pg_wal_replay_pause()' >/dev/null
pg -c "UPDATE catalog.course_offerings SET title='Paused';"
[[ "$(read_catalog)" == Before ]]
pg -p 5433 -c 'SELECT pg_wal_replay_resume()' >/dev/null
await_title Paused
printf '%s\n' 'Replay pause and catch-up verified'
stop_catalog_replica
pg -c "UPDATE catalog.course_offerings SET title='ReplicaRestart';"
launch_catalog_replica
await_title ReplicaRestart
gosu postgres pg_ctl -D "$pg_data" -m fast -w -t 8 stop >/dev/null
[[ "$(read_catalog)" == ReplicaRestart ]]
[[ "$(pg -p 5433 -c 'SELECT pg_is_in_recovery()')" == t ]]
start_primary
pg -c "UPDATE catalog.course_offerings SET title='PrimaryRestart';"
await_title PrimaryRestart
printf '%s\n' 'Verified streaming WAL, stale reads during replay pause, restricted credentials, both restarts and no promotion.'
