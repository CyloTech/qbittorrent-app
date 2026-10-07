#!/usr/bin/python3
"""Configure the bundled qui instance before exposing its Web UI."""
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

DATA = Path('/torrents/config/qui')
STATE = DATA / '.appbox-state.json'
READY = Path('/run/qui-proxy/ready')
PENDING = Path('/run/qui-proxy/callback-pending')
QBT = 'http://127.0.0.1:9080'
QUI = 'http://127.0.0.1:7476'
PASSWORD = os.environ['PASSWORD']
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2,
                        hash_len=32, salt_len=16)


class LoopbackCookies(http.cookiejar.CookieJar):
    def set_cookie(self, cookie):
        # This client only contacts loopback; the public qui cookie stays Secure.
        cookie.secure = False
        super().set_cookie(cookie)


client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPCookieProcessor(LoopbackCookies()))


def request(base, path, data=None, *, method=None, form=False):
    headers = {'X-Requested-With': 'XMLHttpRequest'}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            body = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, body, headers, method=method)
    with client.open(req, timeout=10) as response:
        payload = response.read()
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return payload.decode()


def wait_for_qbt():
    for _ in range(150):
        try:
            request(QBT, '/api/v2/auth/login',
                    {'username': 'admin', 'password': PASSWORD}, form=True)
            if request(QBT, '/api/v2/app/version') != 'v5.2.4':
                raise RuntimeError('Unexpected qBittorrent version')
            return
        except (OSError, urllib.error.URLError):
            time.sleep(2)
    raise RuntimeError('qBittorrent did not become ready')


def wait_for_services():
    wait_for_qbt()
    for _ in range(150):
        try:
            request(QUI, '/api/auth/check-setup')
            return
        except (OSError, urllib.error.URLError):
            time.sleep(2)
    raise RuntimeError('qui did not become ready')


def reset_qui_password():
    # The schema and hash format are pinned to qui 1.30.0. Only change the
    # Appbox-created account when the platform password itself has changed.
    with sqlite3.connect(DATA / 'qui.db', timeout=30) as db:
        changed = db.execute('UPDATE "user" SET password_hash=? WHERE id=1 AND username=?',
                             (HASHER.hash(PASSWORD), 'admin')).rowcount
        if changed != 1:
            raise RuntimeError('The managed qui account does not match')
        db.execute('DELETE FROM sessions')


def configure():
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    if state:
        if not isinstance(state.get('instance_id'), int):
            raise RuntimeError('Invalid qui state')
        try:
            HASHER.verify(state['password_hash'], PASSWORD)
            # Retain the account and user settings across restarts and upgrades.
            return
        except VerifyMismatchError:
            reset_qui_password()

    if request(QUI, '/api/auth/check-setup')['setupRequired']:
        request(QUI, '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    else:
        request(QUI, '/api/auth/login', {'username': 'admin', 'password': PASSWORD})

    instances = request(QUI, '/api/instances/')
    local = [instance for instance in instances if instance['host'] == QBT]
    if len(local) > 1:
        raise RuntimeError('Multiple managed qBittorrent connections exist')
    if state and (not local or local[0]['id'] != state['instance_id']):
        raise RuntimeError('The managed qBittorrent connection changed')

    body = {'name': 'qBittorrent', 'host': QBT, 'username': 'admin',
            'password': PASSWORD, 'hasLocalFilesystemAccess': True}
    if local:
        body['name'] = local[0]['name']
        instance = request(QUI, f"/api/instances/{local[0]['id']}/", body, method='PUT')
    else:
        instance = request(QUI, '/api/instances/', body)
    result = request(QUI, f"/api/instances/{instance['id']}/test", {}, method='POST')
    if result.get('connected') is not True:
        raise RuntimeError('qui could not connect to qBittorrent')
    state = {'instance_id': instance['id'], 'password_hash': HASHER.hash(PASSWORD)}
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.chmod(0o600)
    temporary.replace(STATE)


def installed_callback():
    if not PENDING.exists():
        return
    instance_id = os.environ.get('INSTANCE_ID', '')
    if not instance_id or not all(c.isdigit() for c in instance_id):
        # Release gates use an explicitly mocked non-production callback ID.
        if instance_id != '41416-release-gate':
            raise RuntimeError('Invalid Appbox instance ID')
    url = 'https://api.cylo.io/v1/apps/installed/' + instance_id
    for _ in range(120):
        result = subprocess.run(['curl', '-fsS', '--max-time', '10', '-X', 'POST',
                                 '-H', 'Accept: application/json',
                                 '-H', 'Content-Type: application/json', url],
                                capture_output=True, timeout=15)
        if result.returncode == 0:
            PENDING.unlink()
            return
        time.sleep(5)
    raise RuntimeError('Appbox installation callback did not succeed')


def main():
    wait_for_services()
    configure()
    READY.touch()
    for _ in range(60):
        try:
            request('http://127.0.0.1:8080', '/healthz/readiness')
            break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise RuntimeError('Web interface proxy did not become ready')
    installed_callback()
    print('qui is configured and ready', flush=True)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--wait-qbt']:
            wait_for_qbt()
        elif len(sys.argv) == 1:
            main()
        else:
            raise RuntimeError('Unexpected arguments')
    except Exception as error:
        # HTTP errors can contain credentials; retain only the error type/status.
        code = getattr(error, 'code', None)
        print(f'qui initialization failed ({type(error).__name__}, status={code})', flush=True)
        raise SystemExit(1)
