#!/usr/bin/env python3
"""Test the exact image with disposable volumes and a synthetic torrent."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

IMAGE = "repo.cylo.net/qbittorrent:5.2.4_2.0.15.0"
OLD = "repo.cylo.net/qbittorrent@sha256:810a675f623aeb2a1fdef0e09891c2d4e8104d51af8254ca8c6d1c3bf39cef3b"
if len(sys.argv) != 2 or sys.argv[1] != IMAGE:
    raise SystemExit("Unexpected image reference")
PASSWORD = secrets.token_urlsafe(24)
prefix = "qbittorrent-gate-" + secrets.token_hex(6)
containers, volumes = [], []
stage = tempfile.TemporaryDirectory(prefix=prefix + "-")
os.chmod(stage.name, 0o777)

def run(args, *, data=None, timeout=120):
    result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout)[-1000:].replace(PASSWORD, "[REDACTED]")
        raise RuntimeError(f"{args[0]} {args[1]} failed: {detail}")
    return result.stdout.strip()

def cleanup():
    for name in containers:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    for name in volumes:
        subprocess.run(["docker", "volume", "rm", name], capture_output=True)
    stage.cleanup()
atexit.register(cleanup)

def volume(label):
    name = prefix + "-" + label
    run(["docker", "volume", "create", "--label", f"appbox.release-gate={prefix}", name])
    volumes.append(name)
    return name

def api(name, path, data=None, extra=None):
    args = ["docker", "exec", "-i", name, "/usr/bin/curl", "-fsS", "--max-time", "10"]
    if data is not None:
        args.extend(["--data-binary", "@-"])
    args.extend(extra or [])
    args.append("http://127.0.0.1:8080/api/v2/" + path)
    return run(args, data=data, timeout=20)

def ready(name, version):
    for _ in range(150):
        try:
            if api(name, "app/version") == version:
                return
        except RuntimeError:
            pass
        time.sleep(2)
    raise RuntimeError("qBittorrent readiness timed out")

def start(label, image, vol, version, vue=False):
    name = prefix + "-" + label
    containers.append(name)
    run(["docker", "run", "-d", "--platform", "linux/amd64", "--name", name,
         "--label", f"appbox.release-gate={prefix}", "-v", vol + ":/torrents",
         "-v", stage.name + "/curl:/usr/local/bin/curl:ro", "-v", stage.name + ":/release-gate",
         "-e", "INSTANCE_ID=41416-release-gate", "-e", "PUID=1000", "-e", "PGID=1000",
         "-e", "LISTENING_PORT=6881", "-e", "TRACKER_PORT=9000", "-e", "WEBUI_PORT=8080",
         "-e", "ENABLE_VUETORRENT=" + ("1" if vue else "0"), "-e", "PASSWORD=" + PASSWORD,
         image], timeout=180)
    ready(name, version)
    return name

def verify(name):
    info = json.loads(api(name, "app/buildInfo"))
    if info["libtorrent"] != "2.0.15.0":
        raise RuntimeError("Unexpected libtorrent version: " + info["libtorrent"])
    uid = run(["docker", "exec", name, "sh", "-c",
               "pid=$(pidof qbittorrent-nox); awk '/^Uid:/{print $2}' /proc/$pid/status"])
    if uid != "1000":
        raise RuntimeError("qBittorrent process is not UID 1000")
    # Exercise authentication through the container address, outside the localhost bypass.
    ip = run(["docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name])
    login = ["docker", "exec", "-i", name, "/usr/bin/curl", "-fsS", "--max-time", "10",
             "--data-binary", "@-", "http://" + ip + ":8080/api/v2/auth/login"]
    if run(login, data="username=admin&password=incorrect-release-gate-password") != "Fails.":
        raise RuntimeError("Incorrect password was accepted")
    if run(login, data="username=admin&password=" + PASSWORD) != "Ok.":
        raise RuntimeError("Configured password login failed")
    if run(["docker", "exec", name, "stat", "-c", "%u:%g", "/torrents/config/qBittorrent/qBittorrent.conf"]) != "1000:1000":
        raise RuntimeError("Configuration ownership mismatch")
    run(["docker", "exec", name, "/usr/bin/curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:8080/"])

def stop(name):
    run(["docker", "stop", "-t", "30", name], timeout=45)
    run(["docker", "rm", name])
    containers.remove(name)

mock = Path(stage.name) / "curl"
mock.write_text('#!/bin/bash\nfor arg in "$@"; do\n'
                ' if [[ "$arg" == "https://api.cylo.io/v1/apps/installed/41416-release-gate" ]]; then\n'
                '  printf "callback\\n" >> /release-gate/callback.log\n'
                '  printf "HTTP/1.1 200 OK\\r\\n\\r\\n"\n'
                '  exit 0\n fi\n'
                ' [[ "$arg" != https://api.cylo.io/* && "$arg" != https://api.cylo.net/* ]] || exit 88\n'
                'done\nexec /usr/bin/curl "$@"\n')
mock.chmod(0o755)
if run(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", IMAGE]) != "linux/amd64":
    raise RuntimeError("Wrong image platform")
if run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "/usr/bin/qbittorrent-nox", IMAGE, "--version"]) != "qBittorrent v5.2.4":
    raise RuntimeError("Wrong binary version")

fresh = start("fresh", IMAGE, volume("fresh-data"), "v5.2.4", vue=True)
verify(fresh)
if not json.loads(api(fresh, "app/preferences"))["alternative_webui_enabled"]:
    raise RuntimeError("VueTorrent was not enabled")
run(["docker", "restart", "-t", "30", fresh], timeout=45)
ready(fresh, "v5.2.4")
verify(fresh)
if Path(stage.name, "callback.log").read_text().splitlines() != ["callback"]:
    raise RuntimeError("Fresh callback did not run exactly once")
stop(fresh)
print("PASS: amd64, 5.2.4/libtorrent 2.0.15, UID 1000, login, VueTorrent, callback, restart", flush=True)

run(["docker", "pull", "--platform", "linux/amd64", OLD], timeout=300)
upgrade_vol = volume("upgrade-data")
baseline = start("baseline", OLD, upgrade_vol, "v5.2.3")
api(baseline, "app/setPreferences", 'json={"max_active_downloads":17}')
payload = b"release gate synthetic payload\n"
piece = hashlib.sha1(payload).digest()
info = b'd6:lengthi' + str(len(payload)).encode() + b'e4:name23:release-gate-marker.txt12:piece lengthi16384e6:pieces20:' + piece + b'e'
info_hash = hashlib.sha1(info).hexdigest()
torrent = b'd4:info' + info + b'e'
Path(stage.name, "fixture.torrent").write_bytes(torrent)
run(["docker", "exec", baseline, "/usr/bin/curl", "-fsS", "--max-time", "10",
     "-F", "torrents=@/release-gate/fixture.torrent", "-F", "stopped=true",
     "-F", "savepath=/torrents/completed", "http://127.0.0.1:8080/api/v2/torrents/add"])
for _ in range(30):
    if any(t["hash"] == info_hash for t in json.loads(api(baseline, "torrents/info"))):
        break
    time.sleep(1)
else:
    raise RuntimeError("Synthetic torrent was not added")
run(["docker", "exec", "--user", "1000:1000", baseline, "touch", "/torrents/completed/release-gate-preserved"])
stop(baseline)
upgrade = start("upgrade", IMAGE, upgrade_vol, "v5.2.4")
verify(upgrade)
if json.loads(api(upgrade, "app/preferences"))["max_active_downloads"] != 17:
    raise RuntimeError("Upgrade replaced existing preferences")
if not any(t["hash"] == info_hash for t in json.loads(api(upgrade, "torrents/info"))):
    raise RuntimeError("Upgrade lost the existing torrent")
run(["docker", "exec", upgrade, "test", "-f", "/torrents/completed/release-gate-preserved"])
if Path(stage.name, "callback.log").read_text().splitlines() != ["callback", "callback", "callback"]:
    raise RuntimeError("Upgrade callback mismatch")
stop(upgrade)
print("PASS: upgrade from 5.2.3 preserving preferences, torrent, files, and login", flush=True)
run([sys.executable, str(Path(__file__).with_name("registry-check.py")), IMAGE])
print("PASS: registry precondition; image is ready to publish", flush=True)
