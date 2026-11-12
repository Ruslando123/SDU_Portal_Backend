#!/bin/bash
# Isolated shared Sandbox only. All app listeners and probes remain on loopback.
start_balanced_backends() {
    local count=$1 heap_budget=$2 index api management size heap minimum threads ready attempt base=8081 processors=2
    local -a java_options=()
    if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]]; then
        [[ "$count" =~ ^[12]$ && "$heap_budget" == 4096 && "${SDU_DB_PRIVATE_SIDECAR:-0}" == 1 ]]
        base=8082
        processors=$((4 / count))
        java_options=(-Xlog:gc+init=info)
    else
        [[ "$count" =~ ^[123]$ && "$heap_budget" =~ ^(256|512)$ ]]
    fi
    heap=$((heap_budget / count))
    minimum=$((heap / 2))
    threads=$((80 / count))
    if [[ -n "${SDU_DIAGNOSTIC_TOMCAT_THREADS:-}" ]]; then
        [[ "${SDU_PHASE12_HAPROXY:-0}:$count:$SDU_DIAGNOSTIC_TOMCAT_THREADS" =~ ^1:1:(80|120)$ ]]
        threads=$SDU_DIAGNOSTIC_TOMCAT_THREADS
    fi
    install -m 755 /opt/sdu/haproxy "$work/haproxy"
    cp /opt/sdu/haproxy.cfg "$work/haproxy.cfg"
    if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]]; then
        sed -i -e 's/127.0.0.1:8081/127.0.0.1:8082/' -e 's/maxconn 160/maxconn 8192/' \
            -e 's/option http-server-close/http-reuse always/' "$work/haproxy.cfg"
        ln -s backend-1.log "$work/backend.log"
        minimum=$((512 / count))
        if [[ -n "${SDU_DIAGNOSTIC_TOMCAT_THREADS:-}" ]]; then
            install -d -m 700 -o nobody "$work/edge-runtime"
            sed -i '/^global$/a\    stats socket /tmp/sdu-cloud/edge-runtime/haproxy.sock mode 600 level user' "$work/haproxy.cfg"
        fi
    fi
    for ((index=1; index<=count; index++)); do
        api=$((base + index))
        management=$((9089 + index))
        size=$((pool_size / count + (index <= pool_size % count)))
        if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]]; then
            install -d -m 700 -o nobody "$work/jvm-evidence-$index"
        fi
        (
            unset PGPASSWORD SDU_GATE_KEY SDU_NODE_GATE_KEY SDU_STUDENT_PASSWORD PGBOUNCER_STATS_PASSWORD SDU_REDIS_OBSERVER_PASSWORD
            export SERVER_PORT="$api" MANAGEMENT_SERVER_PORT="$management"
            export DB_POOL_MAX_SIZE="$size" DB_POOL_MIN_IDLE="$size"
            export SERVER_TOMCAT_THREADS_MAX="$threads" SERVER_SHUTDOWN=graceful
            export SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE=10s
            if [[ "${SDU_PHASE12_HAPROXY:-0}" == 1 ]]; then
                export PORTAL_BENCHMARK_JVM_EVIDENCE_DIRECTORY="$work/jvm-evidence-$index"
                export ENROLLMENT_ADMISSION="$((64 / count))"
            fi
            if ((index > 1)); then export SPRING_FLYWAY_ENABLED=false; fi
            exec gosu nobody "$work/jre/bin/java" "-Xms${minimum}m" "-Xmx${heap}m" \
                -XX:MaxMetaspaceSize=192m -XX:ReservedCodeCacheSize=64m "-XX:ActiveProcessorCount=$processors" \
                -XX:+ExitOnOutOfMemoryError "${java_options[@]}" -jar "$work/backend.jar"
        ) > "$work/backend-$index.log" 2>&1 &
        java_pids+=("$!")
        ready=false
        for ((attempt=0; attempt<45; attempt++)); do
            kill -0 "${java_pids[index-1]}"
            if "$work/gateway" probe "$management"; then ready=true; break; fi
            sleep 1
        done
        [[ "$ready" == true ]]
        printf '    server app%s 127.0.0.1:%s check port %s inter 2s fall 2 rise 1\n' "$index" "$api" "$management" >> "$work/haproxy.cfg"
    done
    chmod 644 "$work/haproxy.cfg"
    "$work/haproxy" -c -f "$work/haproxy.cfg" >/dev/null
    (
        unset PGPASSWORD DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_METRICS_PASSWORD SDU_STUDENT_PASSWORD SDU_GATE_KEY PGBOUNCER_STATS_PASSWORD
        exec gosu nobody "$work/haproxy" -db -f "$work/haproxy.cfg"
    ) > "$work/haproxy.log" 2>&1 &
    haproxy_pid=$!
    if [[ -n "${SDU_DIAGNOSTIC_TOMCAT_THREADS:-}" ]]; then printf '%s\n' "$haproxy_pid" > "$work/haproxy.pid"; fi
}
