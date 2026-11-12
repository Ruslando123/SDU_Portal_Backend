#!/bin/bash
# Runs two real Spring Boot processes on the host behind loopback-only HAProxy.
start_demo_balancer() {
    local binary="$root/infrastructure/.local/scaling/haproxy-native" index api pid ready attempt
    [[ -x "$binary" && "$SERVER_PORT" =~ ^[0-9]{4,5}$ ]]
    sed "s/127.0.0.1:8081/127.0.0.1:$SERVER_PORT/" "$root/infrastructure/scaling/load-balancing/haproxy.cfg" > "$state/haproxy.cfg"
    for index in 1 2; do
        api=$((SERVER_PORT + index))
        (
            export SERVER_PORT="$api" SERVER_SHUTDOWN=graceful
            export DB_POOL_MAX_SIZE=8 SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE=10s
            if ((index == 2)); then export SPRING_FLYWAY_ENABLED=false; fi
            exec "$JAVA_HOME/bin/java" -Xms64m -Xmx128m -XX:ActiveProcessorCount=2 -jar "$state"/sdu-course-registration-backend-*.jar
        ) > "$state/backend-$index.log" 2>&1 &
        pid=$!
        if ((index == 1)); then backend_pid=$pid; else secondary_backend_pid=$pid; fi
        ready=false
        for ((attempt=0; attempt<45; attempt++)); do
            kill -0 "$pid"
            if curl -fsS "http://127.0.0.1:$api/actuator/health/readiness" >/dev/null 2>&1; then ready=true; break; fi
            sleep 1
        done
        [[ "$ready" == true ]]
        printf '    server app%s 127.0.0.1:%s check inter 2s fall 2 rise 1\n' "$index" "$api" >> "$state/haproxy.cfg"
    done
    "$binary" -c -f "$state/haproxy.cfg"
    "$binary" -db -f "$state/haproxy.cfg" > "$state/haproxy.log" 2>&1 &
    balancer_pid=$!
}
