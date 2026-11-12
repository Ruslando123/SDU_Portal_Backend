#!/bin/bash
# Sourced by the isolated benchmark launcher after primary migration and fixture creation.
start_catalog_replica() {
    local directory="$work/replica" passfile="$work/replication.pgpass" ready=false
    [[ "${SDU_REPLICATION_PASSWORD:-}" =~ ^[a-zA-Z0-9_-]{32,64}$ ]]
    [[ "${SDU_CATALOG_REPLICA_PASSWORD:-}" =~ ^[a-zA-Z0-9_-]{32,64}$ ]]
    pg <<'SQL'
\getenv replication_password SDU_REPLICATION_PASSWORD
\getenv reader_password SDU_CATALOG_REPLICA_PASSWORD
CREATE ROLE sdu_replicator LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'replication_password';
CREATE ROLE sdu_catalog_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD :'reader_password';
ALTER ROLE sdu_catalog_reader SET default_transaction_read_only = on;
SELECT format('GRANT CONNECT ON DATABASE %I TO sdu_catalog_reader', current_database()) \gexec
CREATE SCHEMA replica_catalog;
CREATE VIEW replica_catalog.published_courses WITH (security_barrier=true) AS
SELECT c.course_id, o.offering_id, c.code, o.title, o.credits, o.term_id
FROM catalog.course_offerings o
JOIN catalog.courses c ON c.course_id=o.course_id
JOIN rules.term_policy_gates g ON g.term_id=o.term_id AND g.configuration_state='PUBLISHED'
WHERE o.publication_status='PUBLISHED';
REVOKE ALL ON SCHEMA replica_catalog FROM PUBLIC;
GRANT USAGE ON SCHEMA replica_catalog TO sdu_catalog_reader;
GRANT SELECT ON replica_catalog.published_courses TO sdu_catalog_reader;
SQL
    printf 'host replication sdu_replicator 127.0.0.1/32 scram-sha-256\n' >> "$pg_data/pg_hba.conf"
    gosu postgres pg_ctl -D "$pg_data" reload >/dev/null
    printf '127.0.0.1:5432:replication:sdu_replicator:%s\n' "$SDU_REPLICATION_PASSWORD" > "$passfile"
    chown postgres:postgres "$passfile"
    chmod 600 "$passfile"
    install -d -m 700 -o postgres "$directory"
    (
        unset PGPASSWORD SDU_REPLICATION_PASSWORD SDU_CATALOG_REPLICA_PASSWORD
        exec gosu postgres pg_basebackup -D "$directory" -R -X stream --checkpoint=fast --no-password \
            -d "host=127.0.0.1 port=5432 user=sdu_replicator passfile=$passfile application_name=sdu_catalog_replica connect_timeout=3"
    )
    if ! launch_catalog_replica; then
        tail -8 "$directory/standby.log" >&2
        return 1
    fi
    for ((i=0; i<20; i++)); do
        if [[ "$(pg -c "SELECT state FROM pg_stat_replication WHERE application_name='sdu_catalog_replica'")" == streaming ]] &&
           [[ "$(PGPASSWORD="$SDU_CATALOG_REPLICA_PASSWORD" psql -XqAt -h 127.0.0.1 -p 5433 \
              -U sdu_catalog_reader -d "$PGDATABASE" -c "SELECT pg_is_in_recovery() AND current_setting('transaction_read_only')='on'")" == t ]]; then
            ready=true
            break
        fi
        sleep 1
    done
    [[ "$ready" == true ]]
}

launch_catalog_replica() {
    local listen="${SDU_REPLICA_LISTEN:-127.0.0.1}"
    [[ "$listen" == 127.0.0.1 || "$listen" == 0.0.0.0 ]]
    (
        unset PGPASSWORD SDU_REPLICATION_PASSWORD SDU_CATALOG_REPLICA_PASSWORD
        exec gosu postgres pg_ctl -D "$work/replica" -l "$work/replica/standby.log" -w -t 20 \
            -o "-h $listen -p 5433 -k '' -c hot_standby=on -c shared_buffers=48MB -c shared_preload_libraries=pg_stat_statements" start
    ) >/dev/null
}

stop_catalog_replica() {
    if [[ -f "$work/replica/postmaster.pid" ]]; then
        gosu postgres pg_ctl -D "$work/replica" -m fast -w -t 8 stop >/dev/null
    fi
}
