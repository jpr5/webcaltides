FROM debian:bookworm@sha256:2c037a04925515fdd6ea85ea14a682d0e79931f5e9f5d07b6dbfc6ba12f9e858

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates flex bison libpng-dev xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY libtcd-2.2.7-r3.tar.xz xtide-2.16.tar.xz ./
RUN printf '%s\n' \
    'e1dde9aafb771eab57c676a99b4b79d61c6800990a0e72782bc20057a8a2d877  libtcd-2.2.7-r3.tar.xz' \
    'ed9415340ae60de1d76fb15ef142798fe227195909c97b0b1a32456d1c9b0d3e  xtide-2.16.tar.xz' \
    | sha256sum -c - \
    && tar xf libtcd-2.2.7-r3.tar.xz \
    && cd libtcd-2.2.7 \
    && ./configure --disable-shared \
    && make -j2 && make install
RUN tar xf xtide-2.16.tar.xz \
    && cd xtide-2.16 \
    && ./configure --without-x --disable-shared \
    && make -j2 tide \
    && make install-libLTLIBRARIES install-binPROGRAMS \
    && test -x /usr/local/bin/tide \
    && test ! -e /usr/local/bin/xtide \
    && test ! -e /usr/local/sbin/xttpd
ENTRYPOINT ["/usr/local/bin/tide"]
