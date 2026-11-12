#!/bin/bash
set -u
export PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=pgbouncer PGUSER=pgbouncer_stats
export PGPASSWORD="$PGBOUNCER_STATS_PASSWORD" PGCONNECT_TIMEOUT=2
pid=$1
for ((i=0;i<110;i++)); do
    kill -0 "$pid" 2>/dev/null || break
    printf 'T\t%s\n' "$(date +%s%3N)"
    awk '/^VmRSS:/ && $2 ~ /^[0-9]+$/ {printf "M\trss_kib\t%s\n", $2}' "/proc/$pid/status" 2>/dev/null
    awk '{printf "M\tcpu_ticks\t%s\n", $14 + $15}' "/proc/$pid/stat" 2>/dev/null
    printf 'M\tticks_per_second\t%s\n' "$(getconf CLK_TCK)"
    psql -Xq --csv -v ON_ERROR_STOP=1 -c 'SHOW POOLS;' | sed 's/^/P\t/'
    psql -Xq --csv -v ON_ERROR_STOP=1 -c 'SHOW STATS;' | sed 's/^/S\t/'
    sleep 5
done
