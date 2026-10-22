#!/usr/bin/env bash
# Tests for the Appbox installed callback, run against a local mock API (needs
# bash, curl and python3; no Docker):
#   - root/usr/local/bin/appbox-installed-callback retries network errors,
#     408, 429 and 5xx, stops after one try on any other status and never logs
#     the callback token carried in INSTANCE_ID;
#   - the appbox-callback s6 service sends a pending callback once and clears
#     the flag, and 30-config no longer calls the API itself;
#   - appbox-qui.py hands the qui-mode callback to the helper and clears its
#     pending flag after a rejection instead of retrying.
#
# Usage: tests/test-installed-callback.sh
set -u

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
helper="${repo_root}/root/usr/local/bin/appbox-installed-callback"
token_id='999?callback_token=querytoken0123456789'
header_token='headertoken0123456789'

# Answers GET with 200 (readiness) and each POST with the next status from a
# comma-separated list, logging what it received.
mock_server='
import http.server, json, os, sys
port, codes, log = int(sys.argv[1]), [int(c) for c in sys.argv[2].split(",")], sys.argv[3]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def do_POST(self):
        seen = sum(1 for _ in open(log)) if os.path.exists(log) else 0
        code = codes[min(seen, len(codes) - 1)]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        with open(log, "a") as f:
            f.write(json.dumps({"path": self.path, "code": code, "body": body,
                                "token": self.headers.get("X-Cylo-Callback-Token", "")}) + "\n")
        self.send_response(code)
        self.end_headers()
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'

failures=0
work=""
server=""

pass() { echo "ok   $*"; }
fail() {
    echo "FAIL $*"
    [[ -f "${work}/output" ]] && sed 's/^/     /' "${work}/output"
    failures=$((failures + 1))
}

start_mock() {
    work="$(mktemp -d)"
    port=$((20000 + RANDOM % 20000))
    python3 -c "${mock_server}" "${port}" "$1" "${work}/requests" &
    server=$!
    for _ in $(seq 1 50); do
        curl -s -o /dev/null "http://127.0.0.1:${port}/" && return 0
        sleep 0.1
    done
    echo "mock API did not start" >&2
    exit 2
}

stop_mock() {
    kill "${server}" 2>/dev/null
    wait "${server}" 2>/dev/null
    rm -rf "${work}"
}

# Run a command, killing it (exit 124) if it is still running after $1 seconds,
# so a callback that never stops fails the test instead of hanging it.
run_limited() {
    local limit=$1 pid ticks=0
    shift
    "$@" &
    pid=$!
    while kill -0 "${pid}" 2>/dev/null && [[ "${ticks}" -lt $((limit * 10)) ]]; do
        sleep 0.1
        ticks=$((ticks + 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null
        wait "${pid}" 2>/dev/null
        return 124
    fi
    wait "${pid}"
}

request_count() { cat "${work}/requests" 2>/dev/null | wc -l | tr -d ' '; }
token_logged() { grep -cE 'querytoken|headertoken' "${work}/output"; }

# --- helper ------------------------------------------------------------------

helper_case() {
    local codes=$1 want_exit=$2 want_requests=$3
    shift 3
    start_mock "${codes}"
    run_limited 20 env INSTANCE_ID="${token_id}" CALLBACK_TOKEN="${header_token}" \
        APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" \
        APPBOX_CALLBACK_RETRY_DELAY=1 \
        "$@" "${helper}" >"${work}/output" 2>&1
    local rc=$? requests leaked
    requests=$(request_count)
    leaked=$(token_logged)
    if [[ "${rc}" == "${want_exit}" && "${requests}" == "${want_requests}" && "${leaked}" == 0 ]]; then
        pass "helper codes=${codes} exit=${rc} requests=${requests}"
    else
        fail "helper codes=${codes} exit=${rc}/${want_exit} requests=${requests}/${want_requests} token_logged=${leaked}"
    fi
    stop_mock
}

helper_case 200 0 1
helper_case 401 1 1
helper_case 404 1 1
helper_case 409 1 1
helper_case 422 1 1
helper_case 503,502,200 0 3
helper_case 429,408,204 0 3
helper_case 500,401 1 2
helper_case 200 0 0 SKIP_APPBOX_CALLBACK=1

# Network errors are retried: nothing listens on the port for the first try.
work="$(mktemp -d)"
port=$((20000 + RANDOM % 20000))
(
    sleep 1.5
    exec python3 -c "${mock_server}" "${port}" 200 "${work}/requests"
) &
server=$!
run_limited 20 env INSTANCE_ID="${token_id}" APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" \
    APPBOX_CALLBACK_RETRY_DELAY=1 "${helper}" >"${work}/output" 2>&1
rc=$?
if [[ "${rc}" == 0 ]] && grep -q 'HTTP 000, retrying' "${work}/output" && [[ "$(request_count)" == 1 ]]; then
    pass "helper retries a network error"
else
    fail "helper network error exit=${rc} requests=$(request_count)"
fi
stop_mock

# An invalid APPBOX_CALLBACK_RETRY_DELAY falls back to the 5s default.
start_mock 503,200
run_limited 20 env INSTANCE_ID="${token_id}" APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" \
    APPBOX_CALLBACK_RETRY_DELAY='1;x' "${helper}" >"${work}/output" 2>&1
rc=$?
if [[ "${rc}" == 0 ]] && grep -q 'retrying in 5s' "${work}/output" && [[ "$(request_count)" == 2 ]]; then
    pass "helper ignores an invalid retry delay"
else
    fail "helper invalid retry delay exit=${rc} requests=$(request_count)"
fi
stop_mock

# Token header, query string and optional body reach the API.
start_mock 200
run_limited 20 env INSTANCE_ID="${token_id}" CALLBACK_TOKEN="${header_token}" \
    APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" \
    "${helper}" '{"custom_fields":[{"key":"ADMIN_PANEL","value":"https://example.test/"}]}' \
    >"${work}/output" 2>&1
if python3 - "${work}/requests" "${header_token}" <<'PY'
import json, sys
req = json.loads(open(sys.argv[1]).readline())
assert req["path"] == "/v1/apps/installed/999?callback_token=querytoken0123456789", req
assert req["token"] == sys.argv[2], req
assert json.loads(req["body"])["custom_fields"][0]["key"] == "ADMIN_PANEL", req
PY
then
    pass "helper sends path, X-Cylo-Callback-Token and body"
else
    fail "helper request contents"
fi
stop_mock


# --- appbox-callback service and cont-init ------------------------------------

service_case() {
    local codes=$1 want_requests=$2
    start_mock "${codes}"
    local sandbox="${work}/run"
    sed -e '1s|.*|#!/usr/bin/env bash|' \
        -e "s|/etc/appbox_callback_pending|${work}/callback_pending|g" \
        -e "s|/usr/local/bin/appbox-installed-callback|${helper}|" \
        -e 's|^exec s6-pause$|echo paused|' \
        "${repo_root}/root/etc/services.d/appbox-callback/run" >"${sandbox}"
    chmod +x "${sandbox}"
    touch "${work}/callback_pending"
    run_limited 20 env INSTANCE_ID="${token_id}" APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" \
        APPBOX_CALLBACK_RETRY_DELAY=1 "${sandbox}" >"${work}/output" 2>&1
    local rc=$? requests
    requests=$(request_count)
    if [[ "${rc}" == 0 && "${requests}" == "${want_requests}" && ! -f "${work}/callback_pending" ]] \
        && grep -q '^paused$' "${work}/output" && [[ "$(token_logged)" == 0 ]]; then
        pass "service codes=${codes}: requests=${requests}, flag cleared, service idles"
    else
        fail "service codes=${codes}: exit=${rc} requests=${requests}/${want_requests}"
    fi
    stop_mock
}

service_case 401 1
service_case 503,200 2

if grep -q 'apps/installed' "${repo_root}/root/etc/cont-init.d/30-config"; then
    fail "30-config still calls the installed callback"
else
    pass "30-config only marks the callback pending"
fi

# --- appbox-qui.py ------------------------------------------------------------

qui_case() {
    local codes=$1 want_requests=$2
    start_mock "${codes}"
    touch "${work}/qui_pending"
    # Background jobs get /dev/null as stdin, so the driver goes in a file.
    cat >"${work}/qui_driver.py" <<'PY'
import importlib.machinery, importlib.util, sys, types
from pathlib import Path
# appbox-qui.py imports argon2 at module level; the callback does not use it.
argon2 = types.ModuleType("argon2")
argon2.PasswordHasher = lambda **kwargs: None
exceptions = types.ModuleType("argon2.exceptions")
exceptions.VerifyMismatchError = Exception
sys.modules.update({"argon2": argon2, "argon2.exceptions": exceptions})
loader = importlib.machinery.SourceFileLoader("appbox_qui", sys.argv[1])
spec = importlib.util.spec_from_loader("appbox_qui", loader)
qui = importlib.util.module_from_spec(spec)
loader.exec_module(qui)
qui.CALLBACK_HELPER = sys.argv[2]
qui.PENDING = Path(sys.argv[3])
qui.installed_callback()
PY
    run_limited 20 env INSTANCE_ID="${token_id}" PASSWORD=unused \
        APPBOX_CALLBACK_BASE="http://127.0.0.1:${port}" APPBOX_CALLBACK_RETRY_DELAY=1 \
        python3 "${work}/qui_driver.py" "${repo_root}/root/defaults/appbox-qui.py" "${helper}" \
        "${work}/qui_pending" >"${work}/output" 2>&1
    local rc=$? requests
    requests=$(request_count)
    if [[ "${rc}" == 0 && "${requests}" == "${want_requests}" && ! -f "${work}/qui_pending" && "$(token_logged)" == 0 ]]; then
        pass "appbox-qui codes=${codes}: requests=${requests}, flag cleared"
    else
        fail "appbox-qui codes=${codes}: exit=${rc} requests=${requests}/${want_requests}"
    fi
    stop_mock
}

qui_case 401 1
qui_case 502,200 2

exit "${failures}"
