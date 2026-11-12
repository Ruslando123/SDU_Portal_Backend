# Build the same verified release for the Linux architecture of the benchmark host.
FROM postgres:18.6-bookworm@sha256:02fb3245e038f33536cf13bc6c4e59f21d07b0cea3c1fe1c07c6e8aa8f083bcc AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gcc libc6-dev libevent-dev libssl-dev make pkg-config
RUN apt-get install -y --no-install-recommends python3
WORKDIR /build
RUN curl -fSsL https://www.pgbouncer.org/downloads/files/1.26.1/pgbouncer-1.26.1.tar.gz -o source.tar.gz \
    && echo '00886a2994b4844cd37110970df777772cfc42a74c400522c82fba509a3f42d9  source.tar.gz' | sha256sum -c - \
    && tar -xzf source.tar.gz --strip-components=1 \
    && ./configure --without-cares \
    && make -j2 pgbouncer \
    && mkdir -p /out/lib \
    && install -m 755 pgbouncer /out/pgbouncer \
    && ldd /out/pgbouncer | awk '/libevent.* => / {print $3}' | xargs -r -I{} cp -L {} /out/lib/
FROM scratch AS artifact
COPY --from=build /out/ /
