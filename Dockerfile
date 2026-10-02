FROM repo.cylo.net/qbittorrent@sha256:810a675f623aeb2a1fdef0e09891c2d4e8104d51af8254ca8c6d1c3bf39cef3b

RUN curl -fL --retry 3 \
    https://github.com/userdocs/qbittorrent-nox-static/releases/download/release-5.2.4_v2.0.15/x86_64-qbittorrent-nox \
    -o /usr/bin/qbittorrent-nox && \
    echo '14d29323f1c12c1e8892ac69496f8b51ddebb74b4b590fc13e343aa213fecaf4  /usr/bin/qbittorrent-nox' | sha256sum -c - && \
    chmod 755 /usr/bin/qbittorrent-nox && \
    /usr/bin/qbittorrent-nox --version | grep -Fx 'qBittorrent v5.2.4'

COPY root/defaults/qBittorrent.conf root/defaults/watched_folders.json root/defaults/qbtcheck /defaults/
COPY root/etc/ /etc/
RUN chmod 755 /etc/cont-init.d/30-config /etc/services.d/qbittorrent/run /etc/services.d/qbtcheck/run /defaults/qbtcheck

EXPOSE 8080
