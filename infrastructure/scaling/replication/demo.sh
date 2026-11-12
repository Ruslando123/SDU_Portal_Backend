#!/bin/bash
# Local demo only. One Docker namespace, private PostgreSQL ports and synthetic accounts.
start_demo_replica() {
    local replica_port
    "$JAVA_HOME/bin/java" -Xmx160m -Dloader.main=org.example.sdu.registration.bootstrap.database.MigrationCli \
        -cp "$state"/sdu-course-registration-backend-*.jar org.springframework.boot.loader.launch.PropertiesLauncher > "$state/migration.log" 2>&1
    export SPRING_FLYWAY_ENABLED=false
    export SDU_REPLICATION_PASSWORD="$(openssl rand -hex 24)" SDU_CATALOG_REPLICA_PASSWORD="$(openssl rand -hex 24)"
    docker exec -e SDU_REPLICATION_PASSWORD -e SDU_CATALOG_REPLICA_PASSWORD "$container" bash -euc '
        umask 077
        work=/tmp/sdu-catalog-demo
        install -d -m 755 "$work"
        pg_data="$PGDATA"
        export SDU_REPLICA_LISTEN=0.0.0.0
        export PGHOST=127.0.0.1 PGPORT=5432 PGUSER="$POSTGRES_USER" PGDATABASE="$POSTGRES_DB" PGPASSWORD="$POSTGRES_PASSWORD"
        pg() { psql -XqAt -v ON_ERROR_STOP=1 "$@"; }
        pg -c "ALTER SYSTEM SET wal_keep_size = '\''128MB'\''"
        pg -c "SELECT pg_reload_conf()"
        source /replica-start.sh
        start_catalog_replica
    '
    unset SDU_REPLICATION_PASSWORD
    replica_port=$(docker port "$container" 5433/tcp | sed -n 's/^127\.0\.0\.1://p')
    [[ "$replica_port" =~ ^[0-9]+$ ]]
    export SDU_CATALOG_REPLICA_URL="jdbc:postgresql://127.0.0.1:$replica_port/$POSTGRES_DB"
    export SDU_CATALOG_REPLICA_USER=sdu_catalog_reader
}

await_demo_replica() {
    local target attempt
    target=$(psql_demo -qAt -c 'SELECT pg_current_wal_flush_lsn()')
    [[ "$target" =~ ^[0-9A-F]+/[0-9A-F]+$ ]]
    for ((attempt=0; attempt<20; attempt++)); do
        if [[ "$(psql_demo -qAt -c "SELECT replay_lsn >= '$target'::pg_lsn FROM pg_stat_replication WHERE application_name='sdu_catalog_replica'")" == t ]]; then
            return 0
        fi
        sleep 1
    done
    return 1
}
