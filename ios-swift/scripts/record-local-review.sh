#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fixture_root="$(mktemp -d "${TMPDIR:-/tmp}/rezeptregal-visual.XXXXXX")"
server_pid=""
cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" >/dev/null 2>&1; then
        kill "$server_pid" >/dev/null 2>&1 || true
        wait "$server_pid" >/dev/null 2>&1 || true
    fi
    # This exact path was created by mktemp for the local run only.
    rm -rf "$fixture_root"
}
trap cleanup EXIT

export APP_REVIEW_PASSWORD="$(openssl rand -hex 24)"
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    echo "::add-mask::$APP_REVIEW_PASSWORD"
fi
export APP_REVIEW_USERNAME="app-review"
export APP_REVIEW_SERVER="https://localhost:18443"
export APP_REVIEW_CA_CERT="$fixture_root/localhost.crt"
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
    -config "$SCRIPT_DIR/localhost-review-openssl.cnf" \
    -keyout "$fixture_root/localhost.key" -out "$APP_REVIEW_CA_CERT" \
    >"$fixture_root/certificate.log" 2>&1

mkdir -p "$BUILD_ROOT/ios-swift/artifacts/local-fixture"
python3 "$SCRIPT_DIR/local-review-server.py" \
    --directory "$fixture_root/data" --port 18443 \
    --certificate "$APP_REVIEW_CA_CERT" --key "$fixture_root/localhost.key" \
    >"$BUILD_ROOT/ios-swift/artifacts/local-fixture/server.log" 2>&1 &
server_pid="$!"
ready=false
for _ in {1..30}; do
    if curl --fail --silent --cacert "$APP_REVIEW_CA_CERT" \
        --max-time 2 "$APP_REVIEW_SERVER/healthz" >/dev/null; then
        ready=true
        break
    fi
    if ! kill -0 "$server_pid" >/dev/null 2>&1; then
        echo "The isolated local fixture server did not start. Inspect server.log." >&2
        exit 1
    fi
    sleep 1
done
if [[ "$ready" != "true" ]]; then
    echo "The isolated HTTPS fixture did not become ready." >&2
    exit 1
fi
cd "$BUILD_ROOT/ios-swift"
xcodegen generate
bash "$SCRIPT_DIR/record-review-video.sh"
