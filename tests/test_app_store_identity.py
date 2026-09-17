import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools.check_app_store_identity import (
    APP_ID, APP_NAME, SHARE_ID, SHARE_NAME, IdentityError, main, read_strings, verify_app, verify_ipa,
)


ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "ios-swift"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_bundle_names_and_persistent_identities():
    for path, name in [
        ("Rezepte/Resources/Info.plist", APP_NAME), ("RezepteShare/Info.plist", SHARE_NAME),
    ]:
        info = plistlib.loads((SWIFT / path).read_bytes())
        assert info["CFBundleDisplayName"] == name
        assert info["CFBundleName"] == name
        assert info["CFBundleIdentifier"] == "$(PRODUCT_BUNDLE_IDENTIFIER)"
    project = read("ios-swift/project.yml")
    main_target = project.split("  Rezepte:\n", 1)[1].split("  RezepteShare:\n", 1)[0]
    share_target = project.split("  RezepteShare:\n", 1)[1].split("  RezepteTests:\n", 1)[0]
    assert re.search(rf"PRODUCT_BUNDLE_IDENTIFIER: {re.escape(APP_ID)}$", main_target, re.M)
    assert re.search(rf"PRODUCT_BUNDLE_IDENTIFIER: {re.escape(SHARE_ID)}$", share_target, re.M)
    assert "ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon" in main_target
    for path in ["Rezepte/Resources/Rezepte.entitlements", "RezepteShare/RezepteShare.entitlements"]:
        assert plistlib.loads((SWIFT / path).read_bytes()) == {
            "com.apple.security.application-groups": ["group.de.mausbaeren.rezepte"]
        }
    assert 'service = "de.mausbaeren.rezepte"' in read("ios-swift/Rezepte/Security/KeychainStore.swift")
    assert 'appGroup = "group.de.mausbaeren.rezepte"' in read("ios-swift/Shared/SharedImportQueue.swift")
    assert 'key = "shared-import-urls"' in read("ios-swift/Shared/SharedImportQueue.swift")


def test_all_source_localized_bundle_names_match_the_canonical_name():
    for folder, name in [(SWIFT / "Rezepte", APP_NAME), (SWIFT / "RezepteShare", SHARE_NAME)]:
        for path in folder.rglob("InfoPlist.strings"):
            localized = read_strings(path.read_bytes())
            for key in ["CFBundleDisplayName", "CFBundleName"]:
                if key in localized:
                    assert localized[key] == name, path


def test_submitted_icon_sources_remain_byte_identical():
    # SHA-256 of the submitted build 2307 sources at c93567b.
    icon_hash = "41743c7a7962114096ca8ea41d2f03364f66dae0277a3fc3dd6da4e18f2c7687"
    for path in [
        "ios-swift/Rezepte/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png",
        "native-ios/assets/images/icon.png",
    ]:
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == icon_hash


def test_legacy_expo_identity_keeps_bundle_scheme_keychain_and_server_urls():
    config = json.loads(read("native-ios/app.json"))["expo"]
    assert config["name"] == APP_NAME
    assert config["ios"]["bundleIdentifier"] == APP_ID
    assert config["scheme"] == "rezepte"
    assert config["icon"] == "./assets/images/icon.png"
    assert config["extra"]["keychainService"] == APP_ID
    assert config["extra"]["apiUrl"] == "https://rezepte.mausbaeren.me"
    assert config["extra"]["allowedApiUrls"] == [
        "https://rezepte.mausbaeren.me", "https://rezepte-review.mausbaeren.me",
    ]
    share = next(plugin[1] for plugin in config["plugins"] if isinstance(plugin, list) and plugin[0] == "expo-share-intent")
    assert share["iosShareExtensionName"] == SHARE_NAME


@pytest.mark.parametrize("variant,suffix,name", [
    ("production", "", APP_NAME), ("development", ".dev", APP_NAME + " Dev"),
    ("preview", ".preview", APP_NAME + " Preview"),
])
def test_generated_expo_config_preserves_identity(variant, suffix, name):
    node = shutil.which("node")
    candidates = [ROOT / "native-ios/node_modules/typescript", ROOT.parent / "native-ios/node_modules/typescript"]
    typescript = next((path for path in candidates if path.is_dir()), None)
    if not node or not typescript:
        pytest.skip("Generated Expo config check requires existing Node/TypeScript dependencies; no install performed")
    script = """
const fs = require('fs');
const ts = require(process.argv[1]);
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).expo;
const js = ts.transpileModule(fs.readFileSync(process.argv[3], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText;
const generated = { exports: {} };
new Function('module', 'exports', js)(generated, generated.exports);
process.stdout.write(JSON.stringify(generated.exports.default({ config })));
"""
    result = subprocess.run(
        [node, "-e", script, str(typescript), str(ROOT / "native-ios/app.json"), str(ROOT / "native-ios/app.config.ts")],
        capture_output=True, text=True, check=True, env={**os.environ, "APP_VARIANT": variant},
    )
    config = json.loads(result.stdout)
    assert config["name"] == name
    assert config["ios"]["bundleIdentifier"] == APP_ID + suffix
    assert config["extra"]["keychainService"] == APP_ID + suffix
    assert config["scheme"] == "rezepte"


def test_native_branding_and_support_before_and_after_login():
    for directory in [SWIFT / "Rezepte", SWIFT / "RezepteShare"]:
        for path in directory.rglob("*.swift"):
            assert "Quellenküche" not in path.read_text(encoding="utf-8"), path
    api = read("ios-swift/Rezepte/Networking/APIClient.swift")
    assert 'Self.publicSupportURL(server: "")' in api
    support_builder = api.split('static func publicSupportURL(server _: String)', 1)[1].split('func login(', 1)[0]
    assert 'https://support.zimlab.org/?module=rezeptregal' in support_builder
    assert 'normalizedServerURL' not in support_builder
    assert 'appendingPathComponent' not in support_builder
    login = read("ios-swift/Rezepte/Views/LoginView.swift")
    support = login.split("private func openSupport()", 1)[1].split("private func signIn()", 1)[0]
    assert "APIClient.publicSupportURL(server: server)" in support
    assert "configure(" not in support and "token" not in support
    assert '.accessibilityIdentifier("support.login")' in login
    settings = read("ios-swift/Rezepte/Views/Settings/SettingsView.swift")
    assert "session.api.supportURL()" in settings
    assert '.accessibilityIdentifier("support.settings")' in settings
    legacy_login = read("native-ios/src/app/login.tsx")
    assert "async function openSupport()" in legacy_login
    assert "openExternalUrl(publicSupportUrl(server))" in legacy_login
    assert 'accessibilityRole="link" accessibilityHint="Öffnet das öffentliche Zimlab-Supportformular' in legacy_login


def test_legacy_support_url_is_canonical_and_never_forwards_server_or_credentials():
    node = shutil.which("node")
    candidates = [ROOT / "native-ios/node_modules/typescript", ROOT.parent / "native-ios/node_modules/typescript"]
    typescript = next((path for path in candidates if path.is_dir()), None)
    if not node or not typescript:
        pytest.skip("Support URL execution requires existing Node/TypeScript dependencies; no install performed")
    script = """
const fs = require('fs');
const ts = require(process.argv[1]);
const js = ts.transpileModule(fs.readFileSync(process.argv[2], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText;
const generated = { exports: {} };
new Function('module', 'exports', js)(generated, generated.exports);
const cases = ['', '  ', 'not a URL', 'https://', 'http://example.de', 'https://user:secret@example.de',
  ' https://example.de/rezepte/?token=secret#private '];
process.stdout.write(JSON.stringify(cases.map(generated.exports.publicSupportUrl)));
"""
    result = subprocess.run(
        [node, "-e", script, str(typescript), str(ROOT / "native-ios/src/lib/support-url.ts")],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == ["https://support.zimlab.org/?module=rezeptregal"] * 7


def test_native_imports_require_admin_and_rejected_shares_are_removed():
    tabs = read("ios-swift/Rezepte/Views/MainTabView.swift")
    assert "if session.fullAccess {\n                InboxView()" in tabs
    inbox = read("ios-swift/Rezepte/Views/Inbox/InboxView.swift")
    for function in ["importURL()", "uploadPhoto(_ item: PhotosPickerItem)", "uploadFile(_ url: URL)"]:
        body = inbox.split(f"private func {function} async", 1)[1].split("\n    private ", 1)[0]
        assert "guard session.fullAccess else" in body
        assert body.index("guard session.fullAccess") < body.index("session.api.import")
    session = read("ios-swift/Rezepte/Session/SessionStore.swift")
    drain = session.split("func drainSharedImports() async", 1)[1]
    assert drain.index("guard fullAccess, !readOnly") < drain.index("api.importURL")
    denial = drain.split("guard fullAccess, !readOnly", 1)[1].split("var imported", 1)[0]
    assert "SharedImportQueue.remove($0)" in denial and "return" in denial
    share = read("native-ios/src/components/shared-link-receiver.tsx")
    assert share.index("if (!isAdmin)") < share.index("void api<ImportResult>")
    denial = share.split("if (!isAdmin)", 1)[1].split("const source", 1)[0]
    assert "resetShareIntent()" in denial and "return;" in denial


def write_plist(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))


@pytest.fixture
def built_app(tmp_path):
    app = tmp_path / "Rezepte.app"
    for folder, extension in [(app, False), (app / "PlugIns/Rezeptregal teilen.appex", True)]:
        name = SHARE_NAME if extension else APP_NAME
        info = {
            "CFBundleIdentifier": SHARE_ID if extension else APP_ID,
            "CFBundleDisplayName": name, "CFBundleName": name,
            "CFBundlePackageType": "XPC!" if extension else "APPL",
        }
        if extension:
            info["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.share-services"}
        write_plist(folder / "Info.plist", info)
    return app


def test_accepts_resolved_app_and_ipa_without_extracting(built_app, tmp_path):
    assert len(verify_app(built_app)) == 2
    ipa = tmp_path / "Rezeptregal.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        for path in built_app.rglob("*"):
            if path.is_file():
                archive.write(path, f"Payload/{built_app.name}/{path.relative_to(built_app).as_posix()}")
    assert len(verify_ipa(ipa)) == 2
    assert main(["--ipa", str(ipa)]) == 0


@pytest.mark.parametrize("extension,key,bad_value", [
    (False, "CFBundleDisplayName", "Quellenküche"), (False, "CFBundleName", "Rezepte"),
    (False, "CFBundleIdentifier", "de.other.app"), (True, "CFBundleDisplayName", "Quellenküche"),
    (True, "CFBundleName", "Rezepte teilen"), (True, "CFBundleIdentifier", "de.other.share"),
])
def test_rejects_wrong_resolved_identity(built_app, extension, key, bad_value):
    path = built_app / ("PlugIns/Rezeptregal teilen.appex/Info.plist" if extension else "Info.plist")
    info = plistlib.loads(path.read_bytes())
    info[key] = bad_value
    write_plist(path, info)
    with pytest.raises(IdentityError, match=key):
        verify_app(built_app)
    assert main(["--app", str(built_app)]) == 1


@pytest.mark.parametrize("extension", [False, True])
@pytest.mark.parametrize("format", ["utf8", "utf16", "xml", "binary"])
def test_localized_override_is_checked_for_every_bundle(built_app, extension, format):
    folder = built_app / "PlugIns/Rezeptregal teilen.appex" if extension else built_app
    strings = folder / "de.lproj/InfoPlist.strings"
    strings.parent.mkdir()
    name = SHARE_NAME if extension else APP_NAME

    def encoded(value):
        if format in ("xml", "binary"):
            return plistlib.dumps({"CFBundleDisplayName": value}, fmt=plistlib.FMT_XML if format == "xml" else plistlib.FMT_BINARY)
        return f'/* identity */\n"CFBundleDisplayName" = "{value}";\n'.encode("utf-8" if format == "utf8" else "utf-16")

    strings.write_bytes(encoded(name))
    assert len(verify_app(built_app)) == 3
    strings.write_bytes(encoded("Quellenküche"))
    with pytest.raises(IdentityError, match="InfoPlist.strings"):
        verify_app(built_app)


def test_missing_share_extension_is_a_failure(built_app):
    (built_app / "PlugIns/Rezeptregal teilen.appex/Info.plist").unlink()
    with pytest.raises(IdentityError, match="exactly one embedded"):
        verify_app(built_app)


def test_malformed_or_ambiguous_strings_fail_closed():
    for data in [b'"CFBundleDisplayName" = "Rezeptregal"', b'"x" = "a"; "x" = "b";']:
        with pytest.raises(IdentityError):
            read_strings(data)
    assert read_strings(b'CFBundleDisplayName = "Rezeptregal";') == {"CFBundleDisplayName": APP_NAME}
