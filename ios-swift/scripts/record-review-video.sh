#!/usr/bin/env bash
set -euo pipefail

: "${APP_REVIEW_PASSWORD:?APP_REVIEW_PASSWORD must be configured as a protected Codemagic variable}"

APP_REVIEW_SERVER="${APP_REVIEW_SERVER:-https://rezepte-review.mausbaeren.me}"
APP_REVIEW_USERNAME="${APP_REVIEW_USERNAME:-app-review}"
BUILD_ROOT="${CM_BUILD_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
DERIVED_DATA="$BUILD_ROOT/ios-swift/build/DerivedData"
ARTIFACT_DIR="$BUILD_ROOT/ios-swift/artifacts"
RESULT_BUNDLE="$BUILD_ROOT/ios-swift/ReviewVideoResults.xcresult"
VIDEO_PATH="$ARTIFACT_DIR/Rezeptregal-App-Review-1.2.0.mp4"

mkdir -p "$ARTIFACT_DIR"
rm -rf "$DERIVED_DATA" "$RESULT_BUNDLE"
rm -f "$VIDEO_PATH"

simulator_id="$({
    xcrun simctl list devices available | awk -F '[()]' '/iPhone 16 Pro Max/{print $2; exit}'
} || true)"
if [[ -z "$simulator_id" ]]; then
    simulator_id="$(xcrun simctl list devices available | awk -F '[()]' '/iPhone/{print $2; exit}')"
fi
if [[ -z "$simulator_id" ]]; then
    echo "No available iPhone simulator found." >&2
    exit 1
fi

xcrun simctl shutdown all >/dev/null 2>&1 || true
xcrun simctl erase "$simulator_id"
xcrun simctl boot "$simulator_id"
xcrun simctl bootstatus "$simulator_id" -b

build_status=0
xcodebuild build-for-testing \
    -project Rezepte.xcodeproj \
    -scheme RezepteReviewVideo \
    -destination "platform=iOS Simulator,id=$simulator_id" \
    -derivedDataPath "$DERIVED_DATA" \
    -only-testing:RezepteReviewUITests/AppReviewVideoUITests/testReviewTour \
    -parallel-testing-enabled NO \
    -maximum-parallel-testing-workers 1 \
    | tee "$ARTIFACT_DIR/xcodebuild-review-video-build.log" || build_status="$?"

if [[ "$build_status" -ne 0 ]]; then
    exit "$build_status"
fi

recorder_pid=""
stop_recorder() {
    if [[ -n "$recorder_pid" ]] && kill -0 "$recorder_pid" >/dev/null 2>&1; then
        kill -INT "$recorder_pid" >/dev/null 2>&1 || true
        wait "$recorder_pid" >/dev/null 2>&1 || true
    fi
    recorder_pid=""
}
trap stop_recorder EXIT

xcrun simctl io "$simulator_id" recordVideo \
    --codec=h264 \
    --force \
    "$VIDEO_PATH" \
    >"$ARTIFACT_DIR/record-video.log" 2>&1 &
recorder_pid="$!"
sleep 2

test_status=0
xcodebuild test-without-building \
    -project Rezepte.xcodeproj \
    -scheme RezepteReviewVideo \
    -destination "platform=iOS Simulator,id=$simulator_id" \
    -derivedDataPath "$DERIVED_DATA" \
    -resultBundlePath "$RESULT_BUNDLE" \
    -only-testing:RezepteReviewUITests/AppReviewVideoUITests/testReviewTour \
    -parallel-testing-enabled NO \
    -maximum-parallel-testing-workers 1 \
    | tee "$ARTIFACT_DIR/xcodebuild-review-video.log" || test_status="$?"

sleep 2
stop_recorder
trap - EXIT

if [[ "$test_status" -ne 0 ]]; then
    exit "$test_status"
fi

if ! grep -Fq "Executed 1 test, with 0 tests skipped and 0 failures" \
    "$ARTIFACT_DIR/xcodebuild-review-video.log"; then
    echo "The recorded review tour did not execute successfully." >&2
    exit 1
fi

if [[ ! -s "$VIDEO_PATH" ]]; then
    echo "Review video was not created." >&2
    exit 1
fi

echo "Review video: $VIDEO_PATH"
echo "Preview app: $DERIVED_DATA/Build/Products/Debug-iphonesimulator/Rezepte.app"
