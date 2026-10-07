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
import urllib.parse

IMAGE = "repo.cylo.net/qbittorrent:5.2.4_2.0.15.0-1"
OLD = "repo.cylo.net/qbittorrent@sha256:bfb8197efda742ec58d88543c75885c18aadc32143442564d3595bcf9a6a7ffd"
if len(sys.argv) != 2 or sys.argv[1] != IMAGE:
    raise SystemExit("Unexpected image reference")
PASSWORD = "GateA1! " + secrets.token_urlsafe(24) + " ' \" $"
callback_count = 0
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
    run(["docker", "exec", "-i", name, "/usr/bin/curl", "-fsS", "--max-time", "10",
         "--cookie-jar", "/tmp/release-gate-cookie", "--data-binary", "@-",
         "http://127.0.0.1:8080/api/v2/auth/login"],
        data=urllib.parse.urlencode({"username":"admin","password":PASSWORD}), timeout=20)
    args = ["docker", "exec", "-i", name, "/usr/bin/curl", "-fsS", "--max-time", "10",
            "--cookie", "/tmp/release-gate-cookie"]
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
            diagnostics = subprocess.run(["docker", "logs", "--tail", "40", name], capture_output=True, text=True)
            logs = diagnostics.stdout + diagnostics.stderr
            if "qui initialization failed" in logs:
                raise RuntimeError("qui initialization failed; see redacted logs: " + logs[-2000:].replace(PASSWORD, "[REDACTED]"))
        time.sleep(2)
    raise RuntimeError("qBittorrent readiness timed out")

def start(label, image, vol, version, interface="0"):
    global callback_count
    name = prefix + "-" + label
    containers.append(name)
    run(["docker", "run", "-d", "--platform", "linux/amd64", "--name", name,
         "--label", f"appbox.release-gate={prefix}", "-v", vol + ":/torrents",
         "-v", stage.name + "/curl:/usr/local/bin/curl:ro", "-v", stage.name + ":/release-gate",
         "-e", "INSTANCE_ID=41416-release-gate", "-e", "PUID=1000", "-e", "PGID=1000",
         "-e", "LISTENING_PORT=6881", "-e", "TRACKER_PORT=9000", "-e", "WEBUI_PORT=8080",
         "-e", "ENABLE_VUETORRENT=" + interface, "-e", "PASSWORD=" + PASSWORD,
         image], timeout=180)
    ready(name, version)
    callback_count += 1
    for _ in range(30):
        if callback_lines() == callback_count:
            return name
        time.sleep(1)
    raise RuntimeError("Installation callback did not complete")

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
    login = ["docker", "exec", "-i", name, "/usr/bin/curl", "-sS", "--max-time", "10",
             "-o", "/dev/null", "-w", "%{http_code}",
             "--data-binary", "@-", "http://" + ip + ":8080/api/v2/auth/login"]
    if run(login, data="username=admin&password=incorrect-release-gate-password") not in ("401", "403"):
        raise RuntimeError("Incorrect password was accepted")
    if run(login, data=urllib.parse.urlencode({"username":"admin","password":PASSWORD})) not in ("200", "204"):
        raise RuntimeError("Configured password login failed")
    if run(["docker", "exec", name, "stat", "-c", "%u:%g", "/torrents/config/qBittorrent/qBittorrent.conf"]) != "1000:1000":
        raise RuntimeError("Configuration ownership mismatch")
    run(["docker", "exec", name, "/usr/bin/curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:8080/"])
    run(["docker", "exec", name, "python3", "-c",
         "import os,qbittorrentapi; c=qbittorrentapi.Client(host='localhost:8080',username='admin',password=os.environ['PASSWORD']); c.auth_log_in(); assert c.app.version=='v5.2.4'"])

def callback_lines():
    path = Path(stage.name, "callback.log")
    return len(path.read_text().splitlines()) if path.exists() else 0

def stop(name):
    run(["docker", "stop", "-t", "30", name], timeout=45)
    run(["docker", "rm", name])
    containers.remove(name)


def verify_qui(name, *, rename=False, renamed=False, torrent_hash=None):
    script = r"""
import http.cookiejar, json, os, urllib.request, urllib.error, time
class Jar(http.cookiejar.CookieJar):
    def set_cookie(self,cookie):
        cookie.secure=False
        super().set_cookie(cookie)
base='http://127.0.0.1:8080'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(Jar()))
def req(path,data=None,method=None):
    headers={'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}
    body=json.dumps(data).encode() if data is not None else None
    response=opener.open(urllib.request.Request(base+path,body,headers,method=method),timeout=20)
    return json.loads(response.read())
try:
    req('/api/instances/')
    raise AssertionError('Anonymous qui access was accepted')
except urllib.error.HTTPError as error:
    assert error.code in (401,403)
assert req('/api/auth/check-setup')['setupRequired'] is False
try:
    req('/api/auth/login',{'username':'admin','password':'incorrect-test-password'})
    raise AssertionError('Incorrect qui password was accepted')
except urllib.error.HTTPError as error:
    assert error.code==401
req('/api/auth/login',{'username':'admin','password':os.environ['PASSWORD']})
items=req('/api/instances/')
assert len(items)==1
instance=items[0]
assert instance['host']=='http://127.0.0.1:9080'
assert instance['hasLocalFilesystemAccess'] is True
if os.environ.get('QUI_GATE_RENAME')=='1':
    req('/api/instances/'+str(instance['id'])+'/',{'name':'Preserved qui name','host':instance['host'],'username':'admin','password':os.environ['PASSWORD']},'PUT')
if os.environ.get('QUI_GATE_RENAMED')=='1':
    assert instance['name']=='Preserved qui name'
for _ in range(45):
    result=req('/api/instances/'+str(instance['id'])+'/test',{},'POST')
    if result['connected'] is True:
        break
    time.sleep(2)
else:
    raise AssertionError('qui connection did not recover: ' + result.get('error','unknown').replace(os.environ['PASSWORD'],'[REDACTED]'))
if os.environ.get('QUI_GATE_HASH'):
    for _ in range(30):
        response=req('/api/instances/'+str(instance['id'])+'/torrents')
        if os.environ['QUI_GATE_HASH'] in json.dumps(response):
            break
        time.sleep(1)
    else:
        raise AssertionError('qui could not read the preserved torrent')
print('qui authentication, automatic qBittorrent connection and persistence verified')
"""
    env = ["-e", "QUI_GATE_RENAME=" + ("1" if rename else "0"),
           "-e", "QUI_GATE_RENAMED=" + ("1" if renamed else "0"),
           "-e", "QUI_GATE_HASH=" + (torrent_hash or "")]
    run(["docker", "exec", "-i"] + env + [name, "python3", "-"], data=script, timeout=200)
    for process in ("qui", "nginx"):
        uids = run(["docker", "exec", name, "ps", "-C", process, "-o", "uid="]).split()
        if not uids or set(uids) != {"1000"}:
            raise RuntimeError(process + " is not UID 1000")
    if run(["docker", "exec", name, "stat", "-c", "%u:%g", "/torrents/config/qui/qui.db"]) != "1000:1000":
        raise RuntimeError("qui database ownership mismatch")
    if "1.30.0" not in run(["docker", "exec", name, "/usr/local/bin/qui", "--version"]):
        raise RuntimeError("Unexpected qui version")

# Both root init and the UID 1000 qui helper use the synthetic callback sink.
callback_log = Path(stage.name, "callback.log")
callback_log.touch()
callback_log.chmod(0o666)

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

for interface in ("qui", "0", "1"):
    fresh_volume = volume("fresh-data-" + interface)
    fresh = start("fresh-" + interface, IMAGE, fresh_volume, "v5.2.4", interface)
    verify(fresh)
    expected_vue = interface == "1"
    if json.loads(api(fresh, "app/preferences"))["alternative_webui_enabled"] != expected_vue:
        raise RuntimeError("Web interface selection did not match")
    if interface == "qui":
        verify_qui(fresh, rename=True)
        original_state = run(["docker", "exec", fresh, "sha256sum", "/torrents/config/qui/.appbox-state.json"]).split()[0]
    run(["docker", "restart", "-t", "30", fresh], timeout=45)
    ready(fresh, "v5.2.4")
    verify(fresh)
    if interface == "qui":
        verify_qui(fresh, renamed=True)
        restarted_state = run(["docker", "exec", fresh, "sha256sum", "/torrents/config/qui/.appbox-state.json"]).split()[0]
        if original_state != restarted_state:
            raise RuntimeError("Restart replaced qui state")
    if callback_lines() != callback_count:
        raise RuntimeError("Restart repeated the install callback")
    if interface == "qui":
        stop(fresh)
        PASSWORD = "ChangedA1! " + secrets.token_urlsafe(24) + " ' \" $"
        fresh = start("changed-password", IMAGE, fresh_volume, "v5.2.4", "qui")
        verify(fresh)
        verify_qui(fresh, renamed=True)
        changed_state = run(["docker", "exec", fresh, "sha256sum", "/torrents/config/qui/.appbox-state.json"]).split()[0]
        if changed_state == original_state:
            raise RuntimeError("App password change did not update qui state")
        print("PASS: app password change synchronized qui and its qBittorrent connection", flush=True)
    stop(fresh)
    print("PASS: fresh " + interface + ", restart, authentication, UID 1000, callback", flush=True)

run(["docker", "pull", "--platform", "linux/amd64", OLD], timeout=300)
upgrade_vol = volume("upgrade-data")
baseline = start("baseline", OLD, upgrade_vol, "v5.2.4")
api(baseline, "app/setPreferences", 'json={"max_active_downloads":17}')
payload = b"release gate synthetic payload\n"
piece = hashlib.sha1(payload).digest()
info = b'd6:lengthi' + str(len(payload)).encode() + b'e4:name23:release-gate-marker.txt12:piece lengthi16384e6:pieces20:' + piece + b'e'
info_hash = hashlib.sha1(info).hexdigest()
torrent = b'd4:info' + info + b'e'
Path(stage.name, "fixture.torrent").write_bytes(torrent)
run(["docker", "exec", baseline, "/usr/bin/curl", "-fsS", "--max-time", "10",
     "--cookie", "/tmp/release-gate-cookie", "-F", "torrents=@/release-gate/fixture.torrent", "-F", "stopped=true",
     "-F", "savepath=/torrents/completed", "http://127.0.0.1:8080/api/v2/torrents/add"])
for _ in range(30):
    if any(t["hash"] == info_hash for t in json.loads(api(baseline, "torrents/info"))):
        break
    time.sleep(1)
else:
    raise RuntimeError("Synthetic torrent was not added")
run(["docker", "exec", "--user", "1000:1000", baseline, "touch", "/torrents/completed/release-gate-preserved"])
stop(baseline)
upgrade = start("upgrade", IMAGE, upgrade_vol, "v5.2.4", "qui")
verify(upgrade)
if json.loads(api(upgrade, "app/preferences"))["max_active_downloads"] != 17:
    raise RuntimeError("Upgrade replaced existing preferences")
if not any(t["hash"] == info_hash for t in json.loads(api(upgrade, "torrents/info"))):
    raise RuntimeError("Upgrade lost the existing torrent")
run(["docker", "exec", upgrade, "test", "-f", "/torrents/completed/release-gate-preserved"])
verify_qui(upgrade, torrent_hash=info_hash)
stop(upgrade)
rollback = start("rollback", OLD, upgrade_vol, "v5.2.4")
verify(rollback)
if not any(t["hash"] == info_hash for t in json.loads(api(rollback, "torrents/info"))):
    raise RuntimeError("Downgrade lost the existing torrent")
run(["docker", "exec", rollback, "test", "-f", "/torrents/config/qui/qui.db"])
stop(rollback)
print("PASS: upgrade from 5.2.4 to qui, torrent/preferences/files preserved, downgrade preserved state", flush=True)
run([sys.executable, str(Path(__file__).with_name("registry-check.py")), IMAGE])
print("PASS: registry precondition; image is ready to publish", flush=True)
