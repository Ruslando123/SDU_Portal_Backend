#!/bin/bash
# Sourced only inside the isolated shared benchmark Sandbox.
setup_pgbouncer() {
    local mode=$1 directory="$work/pgbouncer"
    [[ "$mode" == session || "$mode" == transaction ]]
    [[ "${PGBOUNCER_STATS_PASSWORD:-}" =~ ^[a-zA-Z0-9_-]{32,64}$ ]]
    # The existing Flyway callback resets the runtime password and its SCRAM salt.
    # Complete migration before reading the verifier and prevent a second rotation.
    (
        unset PGPASSWORD PGBOUNCER_STATS_PASSWORD SDU_GATE_KEY SDU_STUDENT_PASSWORD
        exec gosu nobody "$work/jre/bin/java" -Xmx160m -XX:ActiveProcessorCount=2 \
            -Dloader.main=org.example.sdu.registration.bootstrap.database.MigrationCli \
            -cp "$work/backend.jar" org.springframework.boot.loader.launch.PropertiesLauncher
    ) > "$work/backend.log" 2>&1
    printf '%s\n' "Flyway completed directly on primary before PgBouncer"
    install -d -m 755 "$directory"
    tar -xzf /opt/sdu/pgbouncer.tar.gz -C "$directory"
    sed "s/@POOL_MODE@/$mode/" /opt/sdu/pgbouncer.ini > "$directory/pgbouncer.ini"
    # The verifier must match the primary's verifier byte-for-byte for SCRAM pass-through.
    pg -c "SELECT format('\"%s\" \"%s\"',rolname,rolpassword) FROM pg_authid WHERE rolname='portal_runtime' AND rolpassword LIKE 'SCRAM-SHA-256%';" > "$directory/users"
    [[ -s "$directory/users" ]]
    printf '"pgbouncer_stats" "%s"\n' "$PGBOUNCER_STATS_PASSWORD" >> "$directory/users"
    chown postgres:postgres "$directory/users" "$directory/pgbouncer.ini"
    (
        unset PGPASSWORD DB_MIGRATION_PASSWORD DB_RUNTIME_PASSWORD PORTAL_METRICS_PASSWORD SDU_STUDENT_PASSWORD SDU_GATE_KEY PGBOUNCER_STATS_PASSWORD
        exec gosu postgres env LD_LIBRARY_PATH="$directory/lib" "$directory/pgbouncer" "$directory/pgbouncer.ini"
    ) > "$work/pgbouncer.log" 2>&1 &
    pgbouncer_pid=$!
    local ready=false
    for ((attempt=0;attempt<20;attempt++)); do
        kill -0 "$pgbouncer_pid"
        if PGPASSWORD="$DB_RUNTIME_PASSWORD" psql -XAt -h 127.0.0.1 -p 6432 -U portal_runtime -d "$PGDATABASE" -c 'SELECT 1' >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    [[ "$ready" == true ]]
    export SPRING_FLYWAY_URL="$DB_URL"
    export SPRING_FLYWAY_ENABLED=false
    export DB_URL="jdbc:postgresql://127.0.0.1:6432/$PGDATABASE"
}
