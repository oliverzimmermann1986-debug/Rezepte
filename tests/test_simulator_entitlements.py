"""Binary-level preflight parser tests; macOS CI still proves real signing/keychain."""

import importlib.util
from pathlib import Path
import plistlib
import struct

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "simulator_entitlements", ROOT / "ios-swift/scripts/extract-simulator-entitlements.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ENTITLEMENTS = {
    "application-identifier": "FAKETEAMID.de.mausbaeren.rezepte",
    "com.apple.security.application-groups": ["group.de.mausbaeren.rezepte"],
}


def macho(value=ENTITLEMENTS, *, endian="<", name=b"__entitlements"):
    payload = plistlib.dumps(value) + b"\0"
    header = struct.pack(endian + "IiiIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 1, 152, 0, 0)
    segment = struct.pack(endian + "II16sQQQQiiII", 0x19, 152, b"__TEXT", 0, 0, 0, 0, 5, 5, 1, 0)
    section = struct.pack(
        endian + "16s16sQQIIIIIIII", name, b"__TEXT", 0, len(payload), 184, 0, 0, 0, 0, 0, 0, 0
    )
    return header + segment + section + payload


@pytest.mark.parametrize("endian", ["<", ">"])
def test_reads_actual_macho_section_as_dictionary(endian):
    assert MODULE.extract(macho(endian=endian)) == ENTITLEMENTS


def universal(first, second):
    header = struct.pack(
        ">IIIIIIIIIIII", 0xCAFEBABE, 2,
        0x100000C, 0, 48, len(first), 0,
        0x1000007, 0, 48 + len(first), len(second), 0,
    )
    return header + first + second


def test_requires_consistent_entitlements_in_every_universal_slice():
    assert MODULE.extract(universal(macho(), macho())) == ENTITLEMENTS
    with pytest.raises(ValueError, match="conflicting"):
        MODULE.extract(universal(macho(), macho({})))


@pytest.mark.parametrize("data", [b"[Dict]\n", macho()[:100], macho(name=b"__other"), macho([])])
def test_rejects_invalid_missing_or_non_dictionary_payload(data):
    with pytest.raises((ValueError, plistlib.InvalidFileException)):
        MODULE.extract(data)


@pytest.mark.parametrize("field_offset,value", [(20, 0xFFFF), (36, 4), (96, 2), (152, 0xFFFF), (152, 0)])
def test_rejects_out_of_bounds_load_commands_and_section_data(field_offset, value):
    data = bytearray(macho())
    struct.pack_into("<I", data, field_offset, value)
    with pytest.raises(ValueError):
        MODULE.extract(bytes(data))


def test_shell_verifies_signed_product_then_exports_xml_and_actual_payload():
    source = (ROOT / "ios-swift/scripts/verify-simulator-entitlements.sh").read_text()
    assert source.index("codesign --verify --deep --strict") < source.index("extract-simulator-entitlements.py")
    assert "codesign --display --entitlements - --xml" in source
    assert '"$app_path/$executable" "$diagnostic_path"' in source
    assert '"$app_group" != "group.de.mausbaeren.rezepte"' in source
    assert '"$application_id" != *".$bundle_id"' in source
