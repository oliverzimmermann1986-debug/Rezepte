#!/usr/bin/env bash
set -euo pipefail

# Apple documents that default keychain access comes from the signed app ID,
# keychain groups, or app groups. Disabling signing strips all three at runtime.
# https://developer.apple.com/documentation/security/errsecmissingentitlement
# https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps
# https://developer.apple.com/documentation/xcode/build-settings-reference
if [[ "$#" -ne 2 ]]; then
    echo "Usage: verify-simulator-entitlements.sh <simulator.app> <diagnostic.plist>" >&2
    exit 2
fi
app_path="$1"
diagnostic_path="$2"
plist_buddy=/usr/libexec/PlistBuddy
platform="$("$plist_buddy" -c 'Print :CFBundleSupportedPlatforms:0' "$app_path/Info.plist")"
if [[ "$platform" != "iPhoneSimulator" ]]; then
    echo "Refusing non-Simulator product: $platform" >&2
    exit 1
fi
bundle_id="$("$plist_buddy" -c 'Print :CFBundleIdentifier' "$app_path/Info.plist")"
if [[ "$bundle_id" != "de.mausbaeren.rezepte" ]]; then
    echo "Unexpected simulator test host: $bundle_id" >&2
    exit 1
fi

codesign --verify --deep --strict "$app_path"
mkdir -p "$(dirname "$diagnostic_path")"
codesign --display --entitlements - "$app_path" > "$diagnostic_path"
plutil -lint "$diagnostic_path"
application_id="$("$plist_buddy" -c 'Print :application-identifier' "$diagnostic_path" 2>/dev/null || true)"
keychain_group="$("$plist_buddy" -c 'Print :keychain-access-groups:0' "$diagnostic_path" 2>/dev/null || true)"
app_group="$("$plist_buddy" -c 'Print :com.apple.security.application-groups:0' "$diagnostic_path" 2>/dev/null || true)"

# App Groups are also valid effective keychain access groups. Do not invent a
# team prefix or replace production entitlements merely for an unsigned runner.
if [[ -z "$application_id" && -z "$keychain_group" && -z "$app_group" ]]; then
    echo "Simulator host has no effective Keychain entitlement (would fail with -34018)." >&2
    exit 1
fi
if [[ -n "$application_id" && "$application_id" != *"$bundle_id" ]]; then
    echo "Simulator application-identifier does not match $bundle_id." >&2
    exit 1
fi
if [[ "$app_group" != "group.de.mausbaeren.rezepte" ]]; then
    echo "The signed Simulator host is missing the expected Share Extension App Group." >&2
    exit 1
fi
echo "Verified signed Simulator host with Keychain and App Group entitlements: $bundle_id"
