"""Statische Regressionen für Release-Pipeline und Review-Isolation (AUDIT B1–B3, D3, D6)."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workflows_parse_as_yaml():
    for name in ("ios-swift.yml", "ios.yml", "quality.yml"):
        yaml.safe_load(_read(f".github/workflows/{name}"))


def test_release_upload_survives_parallel_pushes():
    swiftui = _read(".github/workflows/ios-swift.yml")
    assert "group: ios-swift-${{ github.event_name }}-${{ github.ref }}" in swiftui
    assert "cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}" in swiftui
    expo = _read(".github/workflows/ios.yml")
    assert "cancel-in-progress: false" in expo
    assert "cancel-in-progress: true" not in expo


def test_swiftui_build_number_is_unique_per_run_attempt():
    swiftui = _read(".github/workflows/ios-swift.yml")
    # Re-runs behalten GITHUB_RUN_NUMBER; erst der Attempt macht die Nummer eindeutig.
    assert "GITHUB_RUN_NUMBER * 100 + GITHUB_RUN_ATTEMPT" in swiftui
    assert 'CURRENT_PROJECT_VERSION="${{ steps.buildnum.outputs.value }}"' in swiftui
    assert 'CURRENT_PROJECT_VERSION="$GITHUB_RUN_NUMBER"' not in swiftui
    # Manueller Override für Notfälle (z. B. run_number-Reset nach Datei-Umbenennung):
    assert "build_number:" in swiftui


def test_xcodegen_version_is_pinned_and_identical_in_both_jobs():
    swiftui = _read(".github/workflows/ios-swift.yml")
    assert 'XCODEGEN_VERSION: "2.46.0"' in swiftui
    assert 'XCODEGEN_SHA256: "4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806"' in swiftui
    assert swiftui.count("shasum -a 256 -c -") == 2
    assert "brew install xcodegen" not in swiftui


def test_share_bundle_id_is_configurable_like_the_app_bundle_id():
    swiftui = _read(".github/workflows/ios-swift.yml")
    assert "IOS_SHARE_BUNDLE_ID: ${{ vars.IOS_SHARE_BUNDLE_ID || 'de.mausbaeren.rezepte.share' }}" in swiftui


def test_swiftui_ci_tracks_server_contract_changes_on_push_and_pull_requests():
    swiftui = _read(".github/workflows/ios-swift.yml")
    push_paths = swiftui.split("  push:", 1)[1].split("  pull_request:", 1)[0]
    pull_request_paths = swiftui.split("  pull_request:", 1)[1].split("\nenv:", 1)[0]
    contract_paths = (
        "native-ios/scripts/testflight-ensure.mjs",
        "app/main.py",
        "app/auth.py",
        "app/db.py",
        "app/routes/**",
        "app/recipes/**",
        "tests/test_swiftui_redesign_source.py",
    )
    for path in contract_paths:
        assert f'- "{path}"' in push_paths
        assert f'- "{path}"' in pull_request_paths


def test_testflight_upload_waits_for_processing_without_assigning_a_group():
    swiftui = _read(".github/workflows/ios-swift.yml")
    assert "ASC_APP_ID: ${{ vars.ASC_APP_ID || '6803595058' }}" in swiftui
    assert "Wait for TestFlight processing" in swiftui
    assert "node ../native-ios/scripts/testflight-ensure.mjs" in swiftui
    assert 'ASC_MARKETING_VERSION: "1.2.0"' in swiftui
    assert "ASC_UPLOAD_STARTED_AT: ${{ steps.upload.outputs.started_at }}" in swiftui
    assert 'ASC_ASSIGN_INTERNAL_GROUP: "false"' in swiftui
    assert 'ASC_ASSIGN_INTERNAL_GROUP: "true"' not in swiftui


def test_signed_archive_metadata_is_verified_before_export():
    swiftui = _read(".github/workflows/ios-swift.yml")
    assert swiftui.index("Validate archive metadata") < swiftui.index("Export signed IPA")
    assert 'EXPECTED_MARKETING_VERSION: "1.2.0"' in swiftui
    assert "EXPECTED_BUILD_NUMBER: ${{ steps.buildnum.outputs.value }}" in swiftui
    assert 'assert_bundle_metadata "Main app" "$app_path/Info.plist" "$IOS_BUNDLE_ID"' in swiftui
    assert (
        'assert_bundle_metadata "Share extension" "$share_path/Info.plist" "$IOS_SHARE_BUNDLE_ID"'
        in swiftui
    )
    for key in (
        "CFBundleIdentifier",
        "CFBundleShortVersionString",
        "CFBundleVersion",
        "IOS_BUNDLE_ID",
        "IOS_SHARE_BUNDLE_ID",
    ):
        assert key in swiftui


def test_release_versions_are_explicit_and_coherent():
    package = _read("app/__init__.py")
    project = _read("ios-swift/project.yml")
    index = _read("app/static/index.html")
    service_worker = _read("app/static/sw.js")

    assert '__version__ = "1.7.0"' in package
    assert 'MARKETING_VERSION: "1.2.0"' in project
    assert "systemInfo.version || '1.7.0'" in index
    assert "rezepte-static-v1.7.0-native-contracts" in service_worker


def test_codemagic_review_video_uses_a_secret_and_exports_preview_artifacts():
    config = _read("codemagic.yaml")
    script = _read("ios-swift/scripts/record-review-video.sh")
    project = _read("ios-swift/project.yml")
    ui_test = _read("ios-swift/RezepteReviewUITests/AppReviewVideoUITests.swift")

    assert "ios-review-video:" in config
    assert "app_review" in config
    assert "APP_REVIEW_PASSWORD" not in config
    assert "Rezepte.app" in config
    assert "Rezeptregal-App-Review-1.2.0.mp4" in config
    assert "recordVideo" in script
    assert '${APP_REVIEW_PASSWORD:?' in script
    assert "xcodebuild build-for-testing" in script
    assert "xcodebuild test-without-building" in script
    assert "-parallel-testing-enabled NO" in script
    assert "CODE_SIGNING_ALLOWED=NO" not in script
    assert "with 0 tests skipped and 0 failures" in script
    assert "RezepteReviewVideo" in project
    assert "bundle.ui-testing" in project
    assert "PRODUCT_NAME: RezepteReviewUITests" in project
    assert 'APP_REVIEW_PASSWORD: "${APP_REVIEW_PASSWORD}"' in project
    assert 'environment["APP_REVIEW_PASSWORD"]' in ui_test
    assert 'app.launchEnvironment["APP_REVIEW_PASSWORD"] = password' in ui_test
    assert "typeText(password)" not in ui_test
    assert 'reviewEnvironment["APP_REVIEW_PASSWORD"]' in _read(
        "ios-swift/Rezepte/Views/LoginView.swift"
    )
    assert "ReviewVideoResults.xcresult" not in config


def test_signing_secrets_are_checked_via_env_not_shell_interpolation():
    swiftui = _read(".github/workflows/ios-swift.yml")
    # Secret-Werte gehören nicht in interpolierten Shell-Quelltext von Bedingungen:
    assert 'if [[ -z "${{ secrets.' not in swiftui
    for name in (
        "IOS_DISTRIBUTION_P12_BASE64",
        "IOS_APPSTORE_PROFILE_BASE64",
        "IOS_SHARE_PROFILE_BASE64",
        "ASC_PRIVATE_KEY_BASE64",
    ):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in swiftui


def test_update_script_preserves_review_instance_isolation():
    update = _read("proxmox/update-local.sh")
    setup = _read("proxmox/setup-review-instance.sh")
    # Das Review-Setup hinterlässt einen Marker, den jedes Update respektiert:
    assert "/etc/scrapper/review-instance" in setup
    assert "/etc/scrapper/review-instance" in update
    assert '"$(hostname)" == "rezepte-review"' in update
    assert 'install -d -m 0755 "$(dirname "$REVIEW_MARKER")"' in update
    assert 'install -m 0644 /dev/null "$REVIEW_MARKER"' in update
    # Der Import-Timer darf auf der Review-Instanz nie bedingungslos aktiviert werden:
    assert "systemctl enable scrapper-web.service scrapper-job.timer scrapper-db-backup.timer" not in update
    assert "systemctl disable --now scrapper-job.timer" in update


def test_update_requires_current_native_client_capabilities():
    update = _read("proxmox/update-local.sh")
    assert "native-admin-config-v1" in update
    assert "recurring-shopping" in update
    assert "meal-conductor-v1" in update
    assert "source-integrity-v2" in update
    assert "substitution-lab-v1" in update


def test_update_health_poll_cannot_reuse_a_stale_payload():
    update = _read("proxmox/update-local.sh")
    poll_helper = update[
        update.index("poll_local_health()") : update.index(
            "trap cleanup_health_files EXIT"
        )
    ]

    assert "--connect-timeout 1 --max-time 2" in poll_helper
    assert '--output "$output_file" http://127.0.0.1:8000/healthz' in poll_helper
    assert "return 1" in poll_helper
    assert 'HEALTH_FILE="$(mktemp /tmp/rezepte-health.XXXXXX)"' in update
    assert (
        'REVIEW_HEALTH_FILE="$(mktemp '
        '/tmp/rezepte-health-after-review-refresh.XXXXXX)"'
    ) in update
    assert 'chmod 0600 "$HEALTH_FILE"' in update
    assert 'chmod 0600 "$REVIEW_HEALTH_FILE"' in update
    assert update.count('if ! poll_local_health "$') == 2
    assert "trap cleanup_health_files EXIT" in update
    assert 'rm -f -- "$HEALTH_FILE"' in update
    assert 'rm -f -- "$REVIEW_HEALTH_FILE"' in update
    assert "/tmp/rezepte-health.json" not in update
    assert "Der Dienst hat innerhalb des Health-Timeouts nicht geantwortet." in update
    assert "Der Review-Dienst hat nach der Demo-Migration nicht geantwortet." in update


def test_review_update_runs_guarded_atomic_demo_refresh_after_contract_gate():
    update = _read("proxmox/update-local.sh")
    refresh = "tools.refresh_app_review_demo"

    assert refresh in update
    assert update.index(refresh) > update.index("OpenAPI-Gate")
    assert 'if [[ "$IS_REVIEW_INSTANCE" == "1" ]]; then' in update
    assert update.rindex("systemctl stop scrapper-web.service") < update.index(refresh)
    assert update.index("systemctl start scrapper-web.service") > update.index(refresh)
    assert 'runuser -u "$APP_USER" -- env' in update
    assert '--backup-dir "$APP_DIR/data/backups/review-refresh"' in update
    assert '--public-url "https://rezepte-review.mausbaeren.me"' in update
