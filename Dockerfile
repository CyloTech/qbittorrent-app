FROM repo.cylo.net/docker-baseimage-ubuntu@sha256:7e7a0d67ab2c044df767d466653b471ca451df550e7a551dc91fc15f5ed415d5

ARG DEBIAN_FRONTEND="noninteractive"
ENV HOME="/torrents/config/" \
    XDG_CONFIG_HOME="/torrents/config/" \
    XDG_DATA_HOME="/torrents/config/"

RUN apt-get update && apt-get install -y \
    gnupg python3 p7zip-full qt6-base-dev openssl cron python3-pip \
    python3-pkg-resources unrar geoip-bin unzip curl && \
    pip3 install qbittorrent-api && \
    apt-get -y remove python3-pip && apt-get -y autoremove && \
    apt-get clean && rm -rf /tmp/* /var/lib/apt/lists/* /var/tmp/*

RUN curl -fL --retry 3 \
    https://github.com/userdocs/qbittorrent-nox-static/releases/download/release-5.2.4_v2.0.15/x86_64-qbittorrent-nox \
    -o /usr/bin/qbittorrent-nox && \
    echo '14d29323f1c12c1e8892ac69496f8b51ddebb74b4b590fc13e343aa213fecaf4  /usr/bin/qbittorrent-nox' | sha256sum -c - && \
    chmod 755 /usr/bin/qbittorrent-nox && \
    /usr/bin/qbittorrent-nox --version | grep -Fx 'qBittorrent v5.2.4'

RUN mkdir -p /vuetorrent && curl -fL --retry 3 \
    https://github.com/VueTorrent/VueTorrent/releases/download/v2.36.1/vuetorrent.zip \
    -o /vuetorrent/vuetorrent.zip && \
    echo '70b67531f0f3bc36c8bd05d82cc7ba1ee37c71c17f0b6c45f522200e35e66f37  /vuetorrent/vuetorrent.zip' | sha256sum -c -

COPY root/defaults/qBittorrent.conf root/defaults/watched_folders.json root/defaults/qbtcheck /defaults/
COPY root/etc/ /etc/
RUN chmod 755 /etc/cont-init.d/30-config /etc/services.d/qbittorrent/run /etc/services.d/qbtcheck/run /defaults/qbtcheck

EXPOSE 8080
