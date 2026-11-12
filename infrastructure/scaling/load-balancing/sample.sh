#!/bin/bash
# Same aggregate CPU, memory and pool metrics for every fixed-budget topology.
set -u
work=/tmp/sdu-cloud
pids=("$@")
ticks=$(getconf CLK_TCK)
sample_limit=110
if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]]; then sample_limit=220; fi
for ((sample=0; sample<sample_limit; sample++)); do
    printf 'T\t%s\n' "$(date +%s%3N)"
    total_ticks=0
    total_rss=0
    combined=
    live=0
    for ((i=0; i<${#pids[@]}; i++)); do
        pid="${pids[i]}"
        if [[ -r "/proc/$pid/stat" ]] && read -r process < "/proc/$pid/stat"; then
            read -r -a fields <<< "${process##*) }"
            total_ticks=$((total_ticks + fields[11] + fields[12]))
            rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status")
            total_rss=$((total_rss + ${rss:-0}))
        fi
        if metrics=$("$work/gateway" metrics "$((9090 + i))" 2>/dev/null); then
            live=$((live+1))
            combined+="$metrics"$'\n'
        fi
    done
    printf 'M\tsdu_backend_instances_up %s\n' "$live"
    if ((live == ${#pids[@]})); then
        printf 'M\tsdu_java_cpu_ticks_total %s\nM\tsdu_java_ticks_per_second %s\nM\tsdu_java_rss_kib %s\n' \
            "$total_ticks" "$ticks" "$total_rss"
        while IFS= read -r line; do
            [[ -z "$line" ]] || printf 'M\t%s\n' "$line"
        done <<< "$combined"
    fi
    if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]] && info=$(REDISCLI_AUTH="$SDU_REDIS_OBSERVER_PASSWORD" timeout 2 redis-cli \
        --no-auth-warning --user sdu-observer -h 127.0.0.1 --raw INFO ALL 2>/dev/null); then
        tr -d '\r' <<< "$info" | awk -F: '
          $1 ~ /^(evicted_keys|rejected_connections|used_memory|used_memory_rss|total_error_replies)$/ && $2 ~ /^[0-9]+$/ {
            print "M\tsdu_redis_" $1 " " $2
          }'
    fi
    sleep 5
done
