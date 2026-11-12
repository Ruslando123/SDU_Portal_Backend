#!/bin/bash
# Only the authenticated Go gateway accepts tunnel traffic. HAProxy stays on loopback.
set -euo pipefail
umask 077
work=/tmp/sdu-cloud
marker=ready
if [[ "${SDU_PRIVATE_HTTP_LOOPBACK:-0}" == 1 ]]; then
    marker=edge-ready
    hosts=(localhost)
else
    mkdir -m 755 "$work"
    install -m 755 /opt/sdu/gateway "$work/gateway"
    IFS=, read -r -a hosts <<< "$SDU_NODE_HOSTS"
fi
install -m 755 /opt/sdu/haproxy "$work/haproxy"
[[ "$SDU_NODE_GATE_KEY" =~ ^[a-zA-Z0-9_-]{43}$ ]]
[[ "${#hosts[@]}" == 1 || "${#hosts[@]}" == 2 ]]
if [[ "${SDU_HTTP_EXPERIMENT:-}" == direct && "$marker" == edge-ready ]]; then
    SDU_DIRECT_UPSTREAM=1 gosu nobody "$work/gateway" > "$work/gateway.log" 2>&1 &
    gateway_pid=$!
    trap 'kill -TERM "$gateway_pid" 2>/dev/null || true' EXIT
    trap 'exit 0' TERM INT
    printf '%s\n' "$gateway_pid" > "$work/edge-gateway.pid"
    sleep 1
    kill -0 "$gateway_pid"
    printf '%s\n' ready > "$work/$marker"
    wait "$gateway_pid"
    exit
fi
reuse="${SDU_EDGE_HTTP_REUSE:-safe}"
[[ "$reuse" == safe || "$reuse" == always ]]
config="$work/haproxy.cfg"
cat > "$config" <<EOF
global
    maxconn 2048
    nbthread 1
    hard-stop-after 12s
defaults
    mode http
    timeout connect 2s
    timeout client 22s
    timeout server 20s
    timeout http-request 5s
    timeout queue 3s
    timeout check 2s
    retries 0
frontend portal
    bind 127.0.0.1:8081
    default_backend portal_instances
EOF
if [[ "${SDU_EDGE_DIAGNOSTICS:-0}" == 1 ]]; then
    sed -i '/^global$/a\
    log stdout format raw local0' "$config"
    sed -i '/^defaults$/a\
    log global' "$config"
    cat >> "$config" <<'EOF'
    http-request set-var(txn.sdu_trace) req.hdr(X-Correlation-Id) if { req.hdr(X-Correlation-Id) -m reg ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ }
    log-format "sdu_haproxy %[var(txn.sdu_trace)] %ST %tsc %TR %Tw %Tc %Tr %Ta %rc %sq %bq"
EOF
fi
if [[ -n "${SDU_HTTP_EXPERIMENT:-}" ]]; then
    install -d -m 700 -o daemon "$work/edge-runtime"
    sed -i '/^global$/a\
    stats socket /tmp/sdu-cloud/edge-runtime/haproxy.sock mode 600 level user' "$config"
fi
cat >> "$config" <<EOF
backend portal_instances
    balance roundrobin
    http-reuse $reuse
    http-send-name-header Host
    http-request set-header X-SDU-Benchmark-Key $SDU_NODE_GATE_KEY
EOF
for index in "${!hosts[@]}"; do
    host=${hosts[$index]}
    if [[ "$marker" == edge-ready ]]; then
        printf '    server localhost 127.0.0.1:8443 ssl sni str(localhost) verify required verifyhost localhost ca-file %s/http-ca.crt check check-sni localhost\n' "$work" >> "$config"
    else
        [[ "$host" =~ ^[a-z0-9.-]+\.modal\.(host|run)$ ]]
        printf '    server %s %s:443 ssl sni str(%s) verify required verifyhost %s ca-file /etc/ssl/certs/ca-certificates.crt check check-sni %s\n' \
            "$host" "$host" "$host" "$host" "$host" >> "$config"
    fi
    printf '    http-response set-header X-SDU-Backend-Instance app%s if { srv_name %s }\n' "$((index+1))" "$host" >> "$config"
done
haproxy_user=nobody
if [[ "$marker" == edge-ready ]]; then haproxy_user=daemon; fi
chown "$haproxy_user" "$config"
haproxy_pid=
gateway_pid=
cleanup() {
    [[ -z "$gateway_pid" ]] || kill -TERM "$gateway_pid" 2>/dev/null || true
    if [[ -n "$haproxy_pid" ]]; then
        kill -USR1 "$haproxy_pid" 2>/dev/null || true
        wait "$haproxy_pid" || true
    fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
trap 'trap - ERR; printf "%s\n" "edge startup failed" > "$work/failed"; sleep 10; exit 1' ERR
(
    unset SDU_NODE_GATE_KEY SDU_NODE_HOSTS SDU_GATE_KEY SDU_STUDENT_PASSWORD
    exec gosu "$haproxy_user" "$work/haproxy" -db -f "$config"
) > "$work/haproxy.log" 2>&1 &
haproxy_pid=$!
printf '%s\n' "$haproxy_pid" > "$work/haproxy.pid"
(
    unset SDU_NODE_GATE_KEY SDU_NODE_HOSTS SDU_STUDENT_PASSWORD
    exec gosu nobody "$work/gateway"
) > "$work/gateway.log" 2>&1 &
gateway_pid=$!
printf '%s\n' "$gateway_pid" > "$work/edge-gateway.pid"
sleep 1
kill -0 "$haproxy_pid" "$gateway_pid"
printf '%s\n' ready > "$work/$marker"
wait -n "$haproxy_pid" "$gateway_pid"
