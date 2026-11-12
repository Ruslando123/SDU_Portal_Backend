#!/bin/bash
# Sourced by start.sh. Redis shares the app network namespace and is loopback only.
redis_cli() {
  REDISCLI_AUTH="$SDU_REDIS_OBSERVER_PASSWORD" redis-cli --no-auth-warning --user sdu-observer -h 127.0.0.1 --raw "$@"
}

start_session_redis() {
  [[ "${PORTAL_SESSION_REDIS_PASSWORD:-}" =~ ^[a-zA-Z0-9_-]{43}$ ]]
  [[ "${SDU_REDIS_OBSERVER_PASSWORD:-}" =~ ^[a-zA-Z0-9_-]{43}$ ]]
  local app_hash observer_hash redis_dir="$work/redis" metadata_acl= version
  app_hash=$(printf '%s' "$PORTAL_SESSION_REDIS_PASSWORD" | sha256sum | cut -d' ' -f1)
  observer_hash=$(printf '%s' "$SDU_REDIS_OBSERVER_PASSWORD" | sha256sum | cut -d' ' -f1)
  version=$(redis-server --version | sed -nE 's/.* v=([0-9]+)\.([0-9]+)\..*/\1 \2/p')
  local major minor
  read -r major minor <<< "$version"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]]
  # CLIENT SETINFO was introduced in Redis 7.2. Redis 7.0 rejects it in ACL files.
  if (( major > 7 || (major == 7 && minor >= 2) )); then metadata_acl=' +client|setinfo'; fi
  install -d -m 700 -o nobody "$redis_dir"
  cat > "$redis_dir/users.acl" <<ACL
user default off
user sdu-session on #$app_hash ~sdu:http-session:bench-v1:sessions:* -@all +auth +hello +ping +client|setname +select +exists +hgetall +hmset +pexpireat +del +rename$metadata_acl
user sdu-observer on #$observer_hash -@all +auth +ping +info
ACL
  cat > "$redis_dir/redis.conf" <<CONF
bind 127.0.0.1
port 6379
protected-mode yes
daemonize no
dir $redis_dir
aclfile $redis_dir/users.acl
maxmemory 268435456
maxmemory-policy noeviction
maxclients 64
io-threads 1
save ""
appendonly no
CONF
  chown nobody "$redis_dir/users.acl" "$redis_dir/redis.conf"
  (
    exec env -i PATH=/usr/bin:/bin "$(command -v gosu)" nobody "$(command -v redis-server)" "$redis_dir/redis.conf"
  ) > "$work/redis.log" 2>&1 &
  redis_pid=$!
  local ready=false
  for ((attempt=0;attempt<12;attempt++)); do
    if ! kill -0 "$redis_pid" 2>/dev/null; then
      printf '%s\n' "Redis process exited before readiness (Redis $major.$minor)" >&2
      return 1
    fi
    if [[ "$(redis_cli PING 2>/dev/null)" == PONG ]]; then ready=true; break; fi
    sleep 1
  done
  [[ "$ready" == true ]]
  [[ "$(redis-cli --raw PING)" == NOAUTH* ]]
  [[ "$(REDISCLI_AUTH="$PORTAL_SESSION_REDIS_PASSWORD" redis-cli --no-auth-warning --user sdu-session --raw HGETALL other:namespace)" == NOPERM* ]]
  [[ "$(REDISCLI_AUTH="$PORTAL_SESSION_REDIS_PASSWORD" redis-cli --no-auth-warning --user sdu-session --raw CONFIG GET maxmemory)" == NOPERM* ]]
  [[ "$(redis_cli INFO memory | tr -d '\r' | awk -F: '$1=="maxmemory_policy" {print $2}')" == noeviction ]]
  {
    redis_cli INFO server | tr -d '\r' | awk -F: '$1=="redis_version" {print $2}'
    redis_cli INFO memory | tr -d '\r' | awk -F: '$1=="maxmemory" {print $2}'
  } > "$work/redis-ready"
  export PORTAL_SESSION_REDIS_NAMESPACE=sdu:http-session:bench-v1
}
