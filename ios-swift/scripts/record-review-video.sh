#!/usr/bin/env bash
set -euo pipefail

: "${APP_REVIEW_PASSWORD:?Configure APP_REVIEW_PASSWORD as a protected variable}"
export APP_REVIEW_SERVER="${APP_REVIEW_SERVER:-https://rezepte-review.mausbaeren.me}"
export APP_REVIEW_USERNAME="${APP_REVIEW_USERNAME:-app-review}"
export APP_REVIEW_PASSWORD

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
    | python3 -c 'import json,sys; data=json.load(sys.stdin); required={"cooking-memory-v1","import-review-v1"}; missing=required-set(data.get("capabilities",[])); print("Review server version:",data.get("version","unknown")); sys.exit("Missing review capabilities: " + ", ".join(sorted(missing)) if missing else 0)'

simulator_id=""
recorder_pid=""
stop_recorder() {
    if [[ -n "$recorder_pid" ]] && kill -0 "$recorder_pid" >/dev/null 2>&1; then
        kill -INT "$recorder_pid" >/dev/null 2>&1 || true
        wait "$recorder_pid" >/dev/null 2>&1 || true
    fi
    recorder_pid=""
}
cleanup() {
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

xcodebuild build-for-testing \
    -project "$IOS_ROOT/Rezepte.xcodeproj" \
    -scheme RezepteReviewVideo \
    -destination "platform=iOS Simulator,id=$simulator_id" \
    -derivedDataPath "$DERIVED_DATA" \
    -only-testing:RezepteReviewUITests/AppReviewVideoUITests/testReviewTour \
    -parallel-testing-enabled NO \
    -maximum-parallel-testing-workers 1 \
    | tee "$ARTIFACT_DIR/xcodebuild-review-build.log"

xcrun simctl io "$simulator_id" recordVideo \
    --codec=h264 "$VIDEO_PATH" \
    >"$ARTIFACT_DIR/record-video.log" 2>&1 &
recorder_pid="$!"
sleep 2

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
    | tee "$ARTIFACT_DIR/xcodebuild-review-tour.log" || test_status="$?"

sleep 2
stop_recorder
if [[ "$test_status" -ne 0 ]]; then
    exit "$test_status"
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

# Xcode 16+ exports the actual XCTest attachments and their manifest. These
# are real screenshots; no rendered replacement assets are generated.
xcrun xcresulttool export attachments \
    --path "$RESULT_BUNDLE" --output-path "$SCREENSHOT_DIR"
png_count="$(find "$SCREENSHOT_DIR" -type f -iname '*.png' | wc -l | tr -d ' ')"
if [[ "$png_count" -lt 11 ]]; then
    echo "Expected all 11 native capture scenes; found $png_count PNG files." >&2
    exit 1
fi

{
    printf 'version=%s\n' "$VERSION"
    printf 'commit=%s\n' "$(git -C "$BUILD_ROOT" rev-parse HEAD)"
    printf 'device_type=%s\n' "$DEVICE_TYPE"
    printf 'capture_utc=%s\n' "$RUN_ID"
    printf 'working_tree_changes=%s\n' "$(git -C "$BUILD_ROOT" status --porcelain | wc -l | tr -d ' ')"
} >"$ARTIFACT_DIR/capture-source.txt"

echo "Review video: $VIDEO_PATH"
echo "Native screenshots and attachment manifest: $SCREENSHOT_DIR"
echo "Preview app: $DERIVED_DATA/Build/Products/Debug-iphonesimulator/Rezepte.app"
echo "Visual review and the separate offline/resume checks are still required."
