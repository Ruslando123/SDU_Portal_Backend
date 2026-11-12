#!/bin/bash
# Called by start-sandbox.sh after Redis and private PostgreSQL are ready.
start_balanced_backends() {
    [[ "$1" == 1 && ( "$2" == 512 || "$2" == 4096 ) && "${PORTAL_WRITE_BEHIND_ENABLED:-}" == true ]]
    local heap="$2" processors=2 connections=160 threads=1
    if [[ "$heap" == 4096 ]]; then
      [[ "${SDU_BENCHMARK:-0}:${SDU_BENCH_POOL:-0}" == 1:24 ]]
      processors=4; connections=8192; threads=2
      export SERVER_TOMCAT_THREADS_MAX=80 SERVER_TOMCAT_THREADS_MIN_SPARE=16
      export SERVER_TOMCAT_MAX_CONNECTIONS=8192 SERVER_TOMCAT_ACCEPT_COUNT=100
      export SERVER_TOMCAT_MBEANREGISTRY_ENABLED=true SPRING_THREADS_VIRTUAL_ENABLED=false
    fi
    printf '%s\n' "$SDU_RABBIT_TLS_CA" > "$work/rabbit-ca.crt"
    printf '%s\n' "$SDU_HTTP_TLS_CA" > "$work/http-ca.crt"
    printf '%s\n' "$SDU_HTTP_TLS_CERT" > "$work/http.crt"
    printf '%s\n' "$SDU_HTTP_TLS_KEY" > "$work/http.key"
    chmod 644 "$work/rabbit-ca.crt" "$work/http-ca.crt" "$work/http.crt"
    chown nobody "$work/http.key"
    export PORTAL_WRITE_BEHIND_CA_FILE="$work/rabbit-ca.crt"
    (
        unset PGPASSWORD SDU_GATE_KEY SDU_STUDENT_PASSWORD SDU_RABBIT_TLS_KEY SDU_HTTP_TLS_KEY SDU_REDIS_OBSERVER_PASSWORD
        export SERVER_PORT=8082 SERVER_SSL_ENABLED=true
        export SERVER_SSL_CERTIFICATE="file:$work/http.crt" SERVER_SSL_CERTIFICATE_PRIVATE_KEY="file:$work/http.key"
        export MANAGEMENT_SERVER_SSL_ENABLED=false SERVER_SHUTDOWN=graceful
        exec gosu nobody "$work/jre/bin/java" -Xms128m "-Xmx${heap}m" "-XX:ActiveProcessorCount=$processors" -Xlog:gc=info \
          -XX:+ExitOnOutOfMemoryError -jar "$work/backend.jar"
    ) > "$work/backend.log" 2>&1 &
    java_pid=$!
    java_pids+=("$java_pid")
    ready=false
    for ((attempt=0; attempt<160; attempt++)); do
      kill -0 "$java_pid"
      if "$work/gateway" probe 9090; then ready=true; break; fi
      sleep 1
    done
    [[ "$ready" == true ]]
    install -m 755 /opt/sdu/haproxy "$work/haproxy"
    cat > "$work/haproxy.cfg" <<CONF
global
    maxconn $connections
    nbthread $threads
defaults
    mode http
    timeout connect 2s
    timeout client 15s
    timeout server 10s
    timeout queue 3s
    retries 0
frontend portal
    bind 127.0.0.1:8081
    default_backend memory_owner
backend memory_owner
    http-reuse always
    http-response set-header X-SDU-Backend-Instance app1
    server owner 127.0.0.1:8082 ssl verify required ca-file $work/http-ca.crt sni str(localhost) verifyhost localhost
CONF
    chmod 644 "$work/haproxy.cfg"
    "$work/haproxy" -c -f "$work/haproxy.cfg" >/dev/null
    (unset DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_WRITE_BEHIND_PASSWORD PORTAL_METRICS_PASSWORD SDU_GATE_KEY
     exec gosu nobody "$work/haproxy" -db -f "$work/haproxy.cfg") > "$work/haproxy.log" 2>&1 &
    haproxy_pid=$!
}
