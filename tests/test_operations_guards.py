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
    # Der Import-Timer darf auf der Review-Instanz nie bedingungslos aktiviert werden:
    assert "systemctl enable scrapper-web.service scrapper-job.timer scrapper-db-backup.timer" not in update
    assert "systemctl disable --now scrapper-job.timer" in update


def test_update_requires_current_native_client_capabilities():
    update = _read("proxmox/update-local.sh")
    assert "native-admin-config-v1" in update
    assert "recurring-shopping" in update
