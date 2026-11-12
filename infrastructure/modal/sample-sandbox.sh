#!/bin/bash
# Loopback only. Prometheus credentials are inherited from the Sandbox secret.
set -u
work=/tmp/sdu-cloud
java_pid=$1
redis_pid=${2:-}
ticks=$(getconf CLK_TCK 2>/dev/null) || ticks=
export PGCONNECT_TIMEOUT=2 PGOPTIONS='-c statement_timeout=2000'
sample_sql="
  WITH connections AS (
    SELECT state, wait_event_type, wait_event FROM pg_stat_activity
    WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()
  )
  SELECT json_build_object(
    'connections',count(*),
    'active',count(*) FILTER (WHERE state='active'),
    'waits',coalesce((SELECT json_object_agg(wait_event_type || ':' || wait_event, n)
      FROM (SELECT wait_event_type,wait_event,count(*) AS n FROM connections
      WHERE state='active' AND wait_event IS NOT NULL GROUP BY 1,2) w),'{}'::json))
  FROM connections;"
sample_limit=110
if [[ -n "${SDU_RESOURCE_NODE:-}" ]]; then sample_limit=420; fi
for ((sample=0; sample<sample_limit; sample++)); do
  printf 'T\t%s\n' "$(date +%s%3N)"
  if [[ -n "$ticks" && -r "/proc/$java_pid/stat" ]] && read -r process < "/proc/$java_pid/stat"; then
    read -r -a fields <<< "${process##*) }"
    if [[ "${fields[11]:-}" =~ ^[0-9]+$ && "${fields[12]:-}" =~ ^[0-9]+$ ]]; then
      printf 'M\tsdu_java_cpu_ticks_total %s\nM\tsdu_java_ticks_per_second %s\n' "$(( fields[11] + fields[12] ))" "$ticks"
    fi
    awk '/^VmRSS:/ && $2 ~ /^[0-9]+$/ {print "M\tsdu_java_rss_kib " $2}' "/proc/$java_pid/status"
  fi
  if prom=$("$work/gateway" metrics 2>/dev/null); then
    while IFS= read -r metric; do
      if [[ -n "$metric" ]]; then printf 'M\t%s\n' "$metric"; fi
    done <<< "$prom"
  fi
  if [[ -n "$redis_pid" ]]; then
    if info=$(REDISCLI_AUTH="$SDU_REDIS_OBSERVER_PASSWORD" timeout 2 redis-cli --no-auth-warning \
        --user sdu-observer -h 127.0.0.1 --raw INFO ALL 2>/dev/null); then
      tr -d '\r' <<< "$info" | awk -F: '
        $1 ~ /^(connected_clients|used_memory|used_memory_rss|used_cpu_user|used_cpu_sys|keyspace_hits|keyspace_misses|evicted_keys|expired_keys|instantaneous_ops_per_sec|total_commands_processed|rejected_connections|total_error_replies)$/ && $2 ~ /^[0-9.]+$/ {
          print "M\tsdu_redis_" $1 " " $2
        }'
    fi
  fi
  if [[ -n "${SDU_RESOURCE_NODE:-}" ]]; then
    if [[ -r /sys/fs/cgroup/cpu.stat ]]; then
      awk '$1=="throttled_usec" {print "M\tsdu_cgroup_throttled_usec " $2}' /sys/fs/cgroup/cpu.stat
    elif [[ -r /sys/fs/cgroup/cpu/cpu.stat ]]; then
      awk '$1=="throttled_time" {print "M\tsdu_cgroup_throttled_usec " $2/1000}' /sys/fs/cgroup/cpu/cpu.stat
    fi
  fi
  if [[ "${SDU_SPLIT_APP:-0}" != 1 && "${SDU_MIXED_DIAGNOSTICS:-0}" != 1 ]] && pg=$(psql -X -qAt --no-password -v ON_ERROR_STOP=1 -c "$sample_sql" 2>/dev/null); then
    printf 'P\t%s\n' "$pg"
  fi
  sleep 5
done
