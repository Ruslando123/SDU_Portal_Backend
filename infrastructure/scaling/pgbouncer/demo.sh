#!/bin/bash
# Local disposable verification. The Docker-published client port binds host loopback.
start_demo_pooler() {
    local bundle="$root/infrastructure/.local/modal/assets/pgbouncer-linux-amd64.tar.gz"
    local config="$root/infrastructure/scaling/pgbouncer/pgbouncer.ini"
    local primary_url="$DB_URL" verifier port
    [[ -f "$bundle" ]]
    "$JAVA_HOME/bin/java" -Xmx160m -Dloader.main=org.example.sdu.registration.bootstrap.database.MigrationCli \
        -cp "$state"/sdu-course-registration-backend-*.jar org.springframework.boot.loader.launch.PropertiesLauncher > "$state/migration.log" 2>&1
    verifier=$(psql_demo -qAt -c "SELECT rolpassword FROM pg_authid WHERE rolname='portal_runtime';")
    [[ "$verifier" == 'SCRAM-SHA-256$'* ]]
    export SDU_DEMO_POOLER_VERIFIER="$verifier" SDU_DEMO_POOLER_MODE="$pooler_mode"
    pooler_container=$(docker run -d --platform linux/amd64 --network "container:$container" \
        --memory=128m --cpus=.25 --label org.sdu.role=student-demo-pooler \
        -e SDU_DEMO_POOLER_VERIFIER -e SDU_DEMO_POOLER_MODE \
        --mount "type=bind,src=$bundle,dst=/bundle.tar.gz,readonly" \
        --mount "type=bind,src=$config,dst=/source.ini,readonly" \
        --entrypoint bash postgres:18.6-bookworm@sha256:02fb3245e038f33536cf13bc6c4e59f21d07b0cea3c1fe1c07c6e8aa8f083bcc -euc '
        umask 077
        dir=/tmp/sdu-cloud/pgbouncer
        mkdir -p "$dir"
        tar -xzf /bundle.tar.gz -C "$dir"
        sed -e "s/@POOL_MODE@/$SDU_DEMO_POOLER_MODE/" -e "s/portal_cloud_smoke/sdu_student_demo/g" \
            -e "s/listen_addr = 127.0.0.1/listen_addr = 0.0.0.0/" /source.ini > "$dir/pgbouncer.ini"
        printf "\"portal_runtime\" \"%s\"\n" "$SDU_DEMO_POOLER_VERIFIER" > "$dir/users"
        unset SDU_DEMO_POOLER_VERIFIER
        chown -R postgres:postgres /tmp/sdu-cloud
        exec gosu postgres env LD_LIBRARY_PATH="$dir/lib" "$dir/pgbouncer" "$dir/pgbouncer.ini"')
    unset SDU_DEMO_POOLER_VERIFIER SDU_DEMO_POOLER_MODE
    port=$(docker port "$container" 6432/tcp | sed -n 's/^127\.0\.0\.1://p')
    [[ "$port" =~ ^[0-9]+$ ]]
    local ready=false
    for ((attempt=0;attempt<20;attempt++)); do
        if docker exec -e DB_RUNTIME_PASSWORD "$container" sh -c \
            'PGPASSWORD="$DB_RUNTIME_PASSWORD" psql -XqAt -h 127.0.0.1 -p 6432 -U portal_runtime -d sdu_student_demo -c "SELECT 1"' >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    [[ "$ready" == true ]]
    export SPRING_FLYWAY_URL="$primary_url"
    export SPRING_FLYWAY_ENABLED=false
    export DB_URL="jdbc:postgresql://127.0.0.1:$port/$POSTGRES_DB"
}

verify_demo_pooler() {
    local driver
    driver=$(unzip -Z1 "$state"/sdu-course-registration-backend-*.jar | awk '/^BOOT-INF\/lib\/postgresql-[0-9].*\.jar$/')
    [[ -n "$driver" ]]
    unzip -p "$state"/sdu-course-registration-backend-*.jar "$driver" > "$state/pgjdbc.jar"
    "$JAVA_HOME/bin/java" --class-path "$state/pgjdbc.jar" "$root/infrastructure/scaling/pgbouncer/VerifyJdbc.java" "$pooler_mode"
}
