# Deliberately use the same libc and OpenSSL runtime as the Modal PostgreSQL image.
FROM postgres:18.6-bookworm@sha256:02fb3245e038f33536cf13bc6c4e59f21d07b0cea3c1fe1c07c6e8aa8f083bcc AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gcc libc6-dev libevent-dev libssl-dev make pkg-config
WORKDIR /build
RUN curl -fsSL https://www.haproxy.org/download/3.2/src/haproxy-3.2.26.tar.gz -o source.tar.gz \
    && echo '760ef35fafc1e05844e73ad07e138e9e96fddbf5de02f606249c027f754827da  source.tar.gz' | sha256sum -c - \
    && tar -xzf source.tar.gz --strip-components=1 \
    && make -j3 TARGET=linux-glibc USE_OPENSSL=1 \
    && mkdir -p /out && install -m 755 haproxy /out/haproxy
FROM scratch AS artifact
COPY --from=build /out/ /
