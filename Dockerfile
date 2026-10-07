FROM repo.cylo.net/qbittorrent@sha256:bfb8197efda742ec58d88543c75885c18aadc32143442564d3595bcf9a6a7ffd

RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx-light python3-argon2 && \
    chown -R abc:abc /var/log/nginx && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fL --retry 3 \
      https://github.com/autobrr/qui/releases/download/v1.30.0/qui_1.30.0_linux_x86_64.tar.gz \
      -o /tmp/qui.tar.gz && \
    echo '52b1b348c27225743a1d93176532b93c8f0b133db5bbc317dae2c8775b227a37  /tmp/qui.tar.gz' | sha256sum -c - && \
    tar -xzf /tmp/qui.tar.gz -C /usr/local/bin qui && \
    rm /tmp/qui.tar.gz && chmod 755 /usr/local/bin/qui

ENV QUI__HOST=127.0.0.1 QUI__PORT=7476 QUI__LOG_LEVEL=WARN \
    QUI__SESSION_COOKIE_SECURE=true QUI__CHECK_FOR_UPDATES=false

COPY root/defaults/qBittorrent.conf root/defaults/watched_folders.json root/defaults/qbtcheck root/defaults/appbox-qui.py root/defaults/qui-nginx.conf /defaults/
COPY root/etc/ /etc/
RUN chmod 755 /etc/cont-init.d/30-config /etc/cont-init.d/35-qui \
    /etc/services.d/qbittorrent/run /etc/services.d/qbtcheck/run \
    /etc/services.d/qui/run /etc/services.d/qui-proxy/run /defaults/qbtcheck /defaults/appbox-qui.py

EXPOSE 8080
