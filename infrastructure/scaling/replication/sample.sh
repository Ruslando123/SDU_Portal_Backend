#!/bin/bash
# Observe source usage and replay state from both private PostgreSQL listeners.
set -u
export PGCONNECT_TIMEOUT=2 PGOPTIONS='-c statement_timeout=2000'
mode=$1
for ((sample=0;sample<110;sample++)); do
    time_ms=$(date +%s%3N)
    primary=$(psql -XqAt -v ON_ERROR_STOP=1 -c "
        SELECT json_build_object(
            'catalog_calls',coalesce((SELECT sum(calls) FROM sdu_diagnostics.pg_stat_statements
             WHERE query LIKE '%FROM catalog.course_offerings o%JOIN catalog.courses c%strpos(lower(c.code)%'
               AND userid=(SELECT oid FROM pg_roles WHERE rolname='portal_runtime')),0),
            'stream', (SELECT json_build_object('state',state,
              'lag_bytes',pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn),
              'replay_lag_ms',extract(epoch FROM replay_lag)*1000)
              FROM pg_stat_replication WHERE application_name='sdu_catalog_replica'));" 2>/dev/null) || primary=null
    standby=null
    if [[ "$mode" == replica ]]; then
        standby=$(psql -XqAt -p 5433 -v ON_ERROR_STOP=1 -c "
            SELECT json_build_object('recovery',pg_is_in_recovery(),
                'catalog_calls',coalesce((SELECT sum(calls) FROM sdu_diagnostics.pg_stat_statements
                 WHERE query LIKE '%FROM replica_catalog.published_courses%'
                   AND userid=(SELECT oid FROM pg_roles WHERE rolname='sdu_catalog_reader')),0));" 2>/dev/null) || standby=null
    fi
    printf '%s\t%s\t%s\n' "$time_ms" "$primary" "$standby"
    sleep 5
done
