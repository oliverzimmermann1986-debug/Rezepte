"""Check the resolved identity of a built iOS app or IPA before upload.

Uses only the Python standard library and never extracts an IPA. This checks
bundle metadata, including localized overrides; it does not validate signing,
provisioning, icons, App Store Connect metadata, or native runtime behavior.
"""

from __future__ import annotations

import argparse
import plistlib
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable


APP_ID = "de.mausbaeren.rezepte"
SHARE_ID = "de.mausbaeren.rezepte.share"
APP_NAME = "Rezeptregal"
SHARE_NAME = "Rezeptregal teilen"


class IdentityError(ValueError):
    """A bundle's identity is missing, ambiguous, or unexpected."""


def read_strings(data: bytes) -> dict:
    """Read compiled XML/binary plists and source UTF-8/UTF-16 .strings files."""
    try:
        result = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        try:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            source = data.decode(encoding)
        except UnicodeError as exc:
            raise IdentityError("InfoPlist.strings is not valid UTF-8/UTF-16") from exc
        token = re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_][\w.]*|[=;]')
        tokens = []
        offset = 0
        while offset < len(source):
            match = token.match(source, offset)
            if not match:
                raise IdentityError("Malformed InfoPlist.strings")
            value = match.group()
            offset = match.end()
            if not value.isspace() and not value.startswith(("//", "/*")):
                tokens.append(value)
        if len(tokens) % 4:
            raise IdentityError("Incomplete InfoPlist.strings entry")

        def unquote(value: str) -> str:
            if not value.startswith('"'):
                return value
            escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}

            def unescape(match: re.Match) -> str:
                escaped = match.group()[1:]
                if escaped[0] in ("U", "u") and len(escaped) == 5:
                    return chr(int(escaped[1:], 16))
                if escaped not in escapes:
                    raise IdentityError("Unsupported InfoPlist.strings escape")
                return escapes[escaped]

            return re.sub(r"\\(?:[Uu][0-9A-Fa-f]{4}|.)", unescape, value[1:-1])

        result = {}
        for index in range(0, len(tokens), 4):
            key, equals, value, semicolon = tokens[index:index + 4]
            if equals != "=" or semicolon != ";":
                raise IdentityError("Malformed InfoPlist.strings entry")
            key = unquote(key)
            if key in result:
                raise IdentityError(f"Duplicate localized key: {key}")
            result[key] = unquote(value)
    if not isinstance(result, dict):
        raise IdentityError("InfoPlist.strings must contain a dictionary")
    return result


def _verify_bundle(
    prefix: str, names: list[str], read: Callable[[str], bytes], *, extension: bool
) -> list[str]:
    expected = {
        "CFBundleIdentifier": SHARE_ID if extension else APP_ID,
        "CFBundleDisplayName": SHARE_NAME if extension else APP_NAME,
        "CFBundleName": SHARE_NAME if extension else APP_NAME,
        "CFBundlePackageType": "XPC!" if extension else "APPL",
    }
    info_path = prefix + "Info.plist"
    try:
        info = plistlib.loads(read(info_path))
    except (KeyError, OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise IdentityError(f"Missing or invalid plist: {info_path}") from exc
    if not isinstance(info, dict):
        raise IdentityError(f"Expected a plist dictionary: {info_path}")
    for key, value in expected.items():
        if info.get(key) != value:
            raise IdentityError(f"{info_path}: {key} expected {value!r}, got {info.get(key)!r}")
    if extension and info.get("NSExtension", {}).get("NSExtensionPointIdentifier") != "com.apple.share-services":
        raise IdentityError(f"{info_path}: not the expected Share extension")
    checked = [info_path]
    for name in names:
        if not name.startswith(prefix):
            continue
        relative = PurePosixPath(name[len(prefix):])
        if len(relative.parts) != 2 or not relative.parts[0].endswith(".lproj") or relative.name != "InfoPlist.strings":
            continue
        localized = read_strings(read(name))
        for key, value in expected.items():
            if key in localized and localized[key] != value:
                raise IdentityError(f"{name}: {key} expected {value!r}, got {localized[key]!r}")
        checked.append(name)
    return checked


def _verify_payload(prefix: str, names: list[str], read: Callable[[str], bytes]) -> list[str]:
    checked = _verify_bundle(prefix, names, read, extension=False)
    extensions = {
        "/".join(PurePosixPath(name).parts[:-1]) + "/"
        for name in names
        if name.startswith(prefix + "PlugIns/")
        and len(PurePosixPath(name[len(prefix):]).parts) == 3
        and PurePosixPath(name).parent.suffix == ".appex"
        and PurePosixPath(name).name == "Info.plist"
    }
    if len(extensions) != 1:
        raise IdentityError(f"Expected exactly one embedded Share extension, found {len(extensions)}")
    checked.extend(_verify_bundle(extensions.pop(), names, read, extension=True))
    return checked


def verify_app(app: Path) -> list[str]:
    app = app.resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise IdentityError("--app must point to an extracted .app directory")
    names = [path.relative_to(app).as_posix() for path in app.rglob("*") if path.is_file()]
    return _verify_payload("", names, lambda name: (app / name).read_bytes())


def verify_ipa(ipa: Path) -> list[str]:
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise IdentityError("IPA contains duplicate archive paths")
        apps = {
            f"Payload/{PurePosixPath(name).parts[1]}/"
            for name in names
            if len(PurePosixPath(name).parts) == 3
            and PurePosixPath(name).parts[0] == "Payload"
            and PurePosixPath(name).parts[1].endswith(".app")
            and PurePosixPath(name).name == "Info.plist"
        }
        if len(apps) != 1:
            raise IdentityError(f"Expected exactly one Payload app, found {len(apps)}")
        return _verify_payload(apps.pop(), names, archive.read)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app", type=Path)
    source.add_argument("--ipa", type=Path)
    args = parser.parse_args(argv)
    try:
        checked = verify_app(args.app) if args.app else verify_ipa(args.ipa)
    except (IdentityError, OSError, zipfile.BadZipFile) as exc:
        print(f"App Store identity FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"App Store identity OK: {APP_NAME}; {SHARE_NAME}; {len(checked)} metadata files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
