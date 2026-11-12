#!/bin/bash
# Disposable single-node quorum broker: application restart recovery, not host-loss HA.
set -euo pipefail
umask 077
install -d -m 700 -o rabbitmq /tmp/sdu-rabbit
printf '%s\n' "$SDU_RABBIT_TLS_CERT" > /tmp/sdu-rabbit/server.crt
printf '%s\n' "$SDU_RABBIT_TLS_KEY" > /tmp/sdu-rabbit/server.key
printf '%s\n' "$SDU_RABBIT_TLS_CA" > /tmp/sdu-rabbit/ca.crt
chown -R rabbitmq:rabbitmq /tmp/sdu-rabbit
cat > /etc/rabbitmq/rabbitmq.conf <<'CONF'
listeners.tcp = none
listeners.ssl.default = 5671
ssl_options.cacertfile = /tmp/sdu-rabbit/ca.crt
ssl_options.certfile = /tmp/sdu-rabbit/server.crt
ssl_options.keyfile = /tmp/sdu-rabbit/server.key
ssl_options.verify = verify_none
ssl_options.fail_if_no_peer_cert = false
ssl_options.versions.1 = tlsv1.3
vm_memory_high_watermark.absolute = 512MiB
disk_free_limit.absolute = 100MB
log.console.level = warning
CONF
chown rabbitmq:rabbitmq /etc/rabbitmq/rabbitmq.conf
# The TLS client validates the broker certificate and hostname; AMQP authenticates with a scoped password.
export RABBITMQ_DEFAULT_USER=portal RABBITMQ_DEFAULT_VHOST=/portal
export RABBITMQ_DEFAULT_PASS="$SDU_RABBIT_PASSWORD"
export RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS='+S 2:2 +A 4'
unset SDU_RABBIT_PASSWORD SDU_RABBIT_TLS_KEY SDU_RABBIT_TLS_CERT SDU_RABBIT_TLS_CA
exec docker-entrypoint.sh rabbitmq-server
