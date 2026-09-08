#!/usr/bin/env bash
set -euo pipefail

: "${APP_REVIEW_PASSWORD:?Configure APP_REVIEW_PASSWORD as a protected variable}"
export APP_REVIEW_SERVER="${APP_REVIEW_SERVER:-https://rezepte-review.mausbaeren.me}"
export APP_REVIEW_USERNAME="${APP_REVIEW_USERNAME:-app-review}"
export APP_REVIEW_LOCAL_FIXTURE="${APP_REVIEW_LOCAL_FIXTURE:-0}"
export APP_REVIEW_PASSWORD

if [[ "$APP_REVIEW_LOCAL_FIXTURE" == "1" ]]; then
    python3 -c 'import os,sys,urllib.parse; u=urllib.parse.urlsplit(os.environ["APP_REVIEW_SERVER"]); sys.exit(0 if u.scheme == "https" and u.hostname == "localhost" and u.username is None and u.password is None else "Local mutation checks require an HTTPS localhost fixture.")'
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_ROOT="${CM_BUILD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
IOS_ROOT="$BUILD_ROOT/ios-swift"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
VERSION="${APP_REVIEW_VERSION:-1.3.0}"
ARTIFACT_DIR="$IOS_ROOT/artifacts/review-$RUN_ID"
DERIVED_DATA="$IOS_ROOT/build/Review-$RUN_ID/DerivedData"
RESULT_BUNDLE="$ARTIFACT_DIR/ReviewResults.xcresult"
SCREENSHOT_DIR="$ARTIFACT_DIR/screenshots"
VIDEO_PATH="$ARTIFACT_DIR/Rezeptregal-App-Review-$VERSION.mp4"
DEVICE_TYPE="${APP_REVIEW_DEVICE_TYPE:-com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro-Max}"

mkdir -p "$ARTIFACT_DIR" "$SCREENSHOT_DIR"

# This public read-only probe prevents an outdated server from producing a
# superficially successful tour in which the new screens are hidden.
curl_tls_options=()
if [[ -n "${APP_REVIEW_CA_CERT:-}" ]]; then
    curl_tls_options=(--cacert "$APP_REVIEW_CA_CERT")
fi
curl --fail --silent --show-error --max-time 30 "${curl_tls_options[@]}" \
    "${APP_REVIEW_SERVER%/}/api/system/info" \
    | python3 -c 'import json,sys; data=json.load(sys.stdin); required={"cooking-memory-v1","import-review-v1","cooking-progress-revision-v1"}; missing=required-set(data.get("capabilities",[])); print("Review server version:",data.get("version","unknown")); sys.exit("Missing review capabilities: " + ", ".join(sorted(missing)) if missing else 0)'

simulator_id=""
recorder_pid=""
hang_monitor_pid=""
stop_hang_monitor() {
    if [[ -n "$hang_monitor_pid" ]] && kill -0 "$hang_monitor_pid" >/dev/null 2>&1; then
        kill -TERM "$hang_monitor_pid" >/dev/null 2>&1 || true
        wait "$hang_monitor_pid" >/dev/null 2>&1 || true
    fi
    hang_monitor_pid=""
}
sample_hung_simulator_app() {
    # XCTest's remote spindump is unsupported on some simulator runtimes.
    # Sample only after an observed idle timeout, while the app is still alive;
    # post-test sampling would merely see the teardown SIGTERM instead.
    local tour_log="$ARTIFACT_DIR/xcodebuild-review-tour.log"
    local app_pid=""
    local app_command=""
    local noticed_timeout=0
    while true; do
        if [[ -f "$tour_log" ]] && grep -Fq 'App event loop idle notification not received' "$tour_log"; then
            if [[ "$noticed_timeout" == 0 ]]; then
                mkdir -p "$ARTIFACT_DIR/diagnostics"
                printf 'Observed XCTest idle timeout; locating this simulator app.\n' \
                    >"$ARTIFACT_DIR/diagnostics/main-thread-hang.sample.log"
                noticed_timeout=1
            fi
            app_pid="$(ps -axww -o pid=,command= | awk -v device="/Devices/$simulator_id/" \
                'index($2, device) && $2 ~ /\/Rezepte\.app\/Rezepte$/ {print $1; exit}')" || true
            if [[ "$app_pid" =~ ^[0-9]+$ ]]; then
                app_command="$(ps -p "$app_pid" -ww -o command= | awk '{print $1}')" || true
                # Never sample another simulator, the user's running app or a
                # similarly named process. Do not persist process arguments.
                if [[ "$app_command" == *"/Devices/$simulator_id/"* && "$app_command" == *"/Rezepte.app/Rezepte" ]]; then
                    mkdir -p "$ARTIFACT_DIR/diagnostics"
                    /usr/bin/sample "$app_pid" 5 10 \
                        -file "$ARTIFACT_DIR/diagnostics/main-thread-hang.sample.txt" \
                        >>"$ARTIFACT_DIR/diagnostics/main-thread-hang.sample.log" 2>&1 || true
                    return
                fi
            fi
        fi
        sleep 2
    done
}
stop_recorder() {
    if [[ -n "$recorder_pid" ]] && kill -0 "$recorder_pid" >/dev/null 2>&1; then
        kill -INT "$recorder_pid" >/dev/null 2>&1 || true
        wait "$recorder_pid" >/dev/null 2>&1 || true
    fi
    recorder_pid=""
}
cleanup() {
    stop_hang_monitor
    stop_recorder
    if [[ -n "$simulator_id" ]]; then
        xcrun simctl shutdown "$simulator_id" >/dev/null 2>&1 || true
        xcrun simctl delete "$simulator_id" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# Create and remove only this run's simulator. Do not erase or shut down the
# developer's existing devices, and preserve previous result bundles.
simulator_id="$(xcrun simctl create "Rezeptregal Review $RUN_ID" "$DEVICE_TYPE")"
xcrun simctl boot "$simulator_id"
xcrun simctl bootstatus "$simulator_id" -b
if [[ -n "${APP_REVIEW_CA_CERT:-}" ]]; then
    xcrun simctl keychain "$simulator_id" add-root-cert "$APP_REVIEW_CA_CERT"
fi
xcrun simctl status_bar "$simulator_id" override \
    --time "9:41" --batteryState charged --batteryLevel 100

# Keep the real Keychain and App Group APIs active in the Simulator. These
# command-local settings never affect a distribution archive or its profiles.
simulator_signing=(
    CODE_SIGNING_ALLOWED=YES
    CODE_SIGN_IDENTITY=-
    "CODE_SIGN_LOCAL_EXECUTION_IDENTITY=Ad Hoc"
    CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES
    CODE_SIGN_STYLE=Manual
    DEVELOPMENT_TEAM=
    PROVISIONING_PROFILE_SPECIFIER=
)

xcodebuild build-for-testing \
    -project "$IOS_ROOT/Rezepte.xcodeproj" \
    -scheme RezepteReviewVideo \
    -destination "platform=iOS Simulator,id=$simulator_id" \
    -derivedDataPath "$DERIVED_DATA" \
    -only-testing:RezepteReviewUITests/AppReviewVideoUITests/testReviewTour \
    -parallel-testing-enabled NO \
    -maximum-parallel-testing-workers 1 \
    "${simulator_signing[@]}" \
    | tee "$ARTIFACT_DIR/xcodebuild-review-build.log"

bash "$SCRIPT_DIR/verify-simulator-entitlements.sh" \
    "$DERIVED_DATA/Build/Products/Debug-iphonesimulator/Rezepte.app" \
    "$ARTIFACT_DIR/SimulatorEntitlements.plist"

xcrun simctl io "$simulator_id" recordVideo \
    --codec=h264 "$VIDEO_PATH" \
    >"$ARTIFACT_DIR/record-video.log" 2>&1 &
recorder_pid="$!"
sleep 2

touch "$ARTIFACT_DIR/test-start.marker"
sample_hung_simulator_app &
hang_monitor_pid="$!"
test_status=0
xcodebuild test-without-building \
    -project "$IOS_ROOT/Rezepte.xcodeproj" \
    -scheme RezepteReviewVideo \
    -destination "platform=iOS Simulator,id=$simulator_id" \
    -derivedDataPath "$DERIVED_DATA" \
    -resultBundlePath "$RESULT_BUNDLE" \
    -only-testing:RezepteReviewUITests/AppReviewVideoUITests/testReviewTour \
    -parallel-testing-enabled NO \
    -maximum-parallel-testing-workers 1 \
    "${simulator_signing[@]}" \
    | tee "$ARTIFACT_DIR/xcodebuild-review-tour.log" || test_status="$?"

stop_hang_monitor
sleep 2
stop_recorder

# Preserve genuine screenshots and failure attachments even when XCTest fails.
# Keep the original test exit status; an export failure must not hide it.
attachment_export_status=0
if [[ -d "$RESULT_BUNDLE" ]]; then
    xcrun xcresulttool export attachments \
        --path "$RESULT_BUNDLE" --output-path "$SCREENSHOT_DIR" \
        >"$ARTIFACT_DIR/attachment-export.log" 2>&1 || attachment_export_status="$?"
else
    attachment_export_status=1
    echo "No XCTest result bundle was created for attachment export." >&2
fi

if [[ "$test_status" -ne 0 ]]; then
    # Collect before deleting this run's simulator. This is diagnosis only;
    # all failures below preserve XCTest's original nonzero result.
    diagnostic_dir="$ARTIFACT_DIR/diagnostics"
    mkdir -p "$diagnostic_dir/xcresult" "$diagnostic_dir/host-crashes" "$diagnostic_dir/simulator-crashes"
    xcrun xcresulttool export diagnostics \
        --path "$RESULT_BUNDLE" --output-path "$diagnostic_dir/xcresult" \
        >"$diagnostic_dir/xcresult-export.log" 2>&1 || true
    xcrun simctl io "$simulator_id" screenshot "$diagnostic_dir/failure-screen.png" \
        >"$diagnostic_dir/failure-screen.log" 2>&1 || true
    xcrun simctl spawn "$simulator_id" log show --style compact --last 20m \
        --predicate 'process == "Rezepte" OR eventMessage CONTAINS "de.mausbaeren.rezepte"' \
        >"$diagnostic_dir/app-system.log" 2>&1 || true
    host_reports="$HOME/Library/Logs/DiagnosticReports"
    if [[ -d "$host_reports" ]]; then
        find "$host_reports" -maxdepth 2 -type f -newer "$ARTIFACT_DIR/test-start.marker" \
            \( -name 'Rezepte*.ips' -o -name 'Rezepte*.crash' \) \
            -exec cp -p {} "$diagnostic_dir/host-crashes/" \; \
            >"$diagnostic_dir/host-crash-copy.log" 2>&1 || true
    fi
    simulator_reports="$HOME/Library/Developer/CoreSimulator/Devices/$simulator_id/data/Library/Logs/CrashReporter"
    if [[ -d "$simulator_reports" ]]; then
        find "$simulator_reports" -maxdepth 3 -type f -newer "$ARTIFACT_DIR/test-start.marker" \
            \( -name '*.ips' -o -name '*.crash' \) \
            -exec cp -p {} "$diagnostic_dir/simulator-crashes/" \; \
            >"$diagnostic_dir/simulator-crash-copy.log" 2>&1 || true
    fi
fi

{
    printf 'version=%s\n' "$VERSION"
    printf 'commit=%s\n' "$(git -C "$BUILD_ROOT" rev-parse HEAD)"
    printf 'device_type=%s\n' "$DEVICE_TYPE"
    printf 'capture_utc=%s\n' "$RUN_ID"
    printf 'local_fixture=%s\n' "$APP_REVIEW_LOCAL_FIXTURE"
    printf 'test_exit_status=%s\n' "$test_status"
    printf 'attachment_export_exit_status=%s\n' "$attachment_export_status"
    printf 'working_tree_changes=%s\n' "$(git -C "$BUILD_ROOT" status --porcelain | wc -l | tr -d ' ')"
} >"$ARTIFACT_DIR/capture-source.txt"

if [[ "$test_status" -ne 0 ]]; then
    exit "$test_status"
fi
if [[ "$attachment_export_status" -ne 0 ]]; then
    echo "Native screenshot export failed. Inspect attachment-export.log." >&2
    exit "$attachment_export_status"
fi
if ! grep -Eq "Executed 1 test, with (0 tests skipped and )?0 failures" \
    "$ARTIFACT_DIR/xcodebuild-review-tour.log"; then
    echo "The recorded tour did not execute successfully." >&2
    exit 1
fi
if grep -Eq "Executed .* with [1-9][0-9]* tests? skipped" \
    "$ARTIFACT_DIR/xcodebuild-review-tour.log"; then
    echo "The recorded tour skipped a test." >&2
    exit 1
fi
if [[ ! -s "$VIDEO_PATH" ]]; then
    echo "Review video was not created." >&2
    exit 1
fi

png_count="$(find "$SCREENSHOT_DIR" -type f -iname '*.png' | wc -l | tr -d ' ')"
expected_screenshots=11
if [[ "$APP_REVIEW_LOCAL_FIXTURE" == "1" ]]; then
    expected_screenshots=20
fi
if [[ "$png_count" -lt "$expected_screenshots" ]]; then
    echo "Expected all $expected_screenshots native capture scenes; found $png_count PNG files." >&2
    exit 1
fi

echo "Review video: $VIDEO_PATH"
echo "Native screenshots and attachment manifest: $SCREENSHOT_DIR"
echo "Preview app: $DERIVED_DATA/Build/Products/Debug-iphonesimulator/Rezepte.app"
echo "Visual inspection and disconnected-device/sync checks are still required."
