#!/usr/bin/env python3
"""Read Xcode's effective Simulator entitlement payload from the signed Mach-O.

The caller must verify the app's signature first. Simulator builds embed their
entitlements in __TEXT,__entitlements; the host ad-hoc signature may be empty.
Fail closed for unsupported, truncated, absent, or conflicting slice payloads.
"""

import plistlib
from pathlib import Path
import struct
import sys


def extract(data: bytes) -> dict:
    magic = data[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        endian = ">" if magic == b"\xca\xfe\xba\xbe" else "<"
        if len(data) < 8:
            raise ValueError("Truncated universal Mach-O header")
        count = struct.unpack_from(endian + "I", data, 4)[0]
        if not count or 8 + count * 20 > len(data):
            raise ValueError("Invalid universal Mach-O slice table")
        values = []
        for index in range(count):
            _, _, offset, size, _ = struct.unpack_from(endian + "IIIII", data, 8 + index * 20)
            if offset < 8 + count * 20 or not size or offset + size > len(data):
                raise ValueError("Invalid universal Mach-O slice bounds")
            values.append(extract(data[offset:offset + size]))
        if any(value != values[0] for value in values[1:]):
            raise ValueError("Simulator slices have conflicting entitlements")
        return values[0]

    if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") or len(data) < 32:
        raise ValueError("Expected a complete 64-bit Simulator Mach-O")
    endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
    commands, command_bytes = struct.unpack_from(endian + "II", data, 16)
    command_end = 32 + command_bytes
    if command_end > len(data) or commands > command_bytes // 8:
        raise ValueError("Invalid Mach-O load command bounds")
    cursor = 32
    payloads = []
    for _ in range(commands):
        if cursor + 8 > command_end:
            raise ValueError("Truncated Mach-O load command")
        command, size = struct.unpack_from(endian + "II", data, cursor)
        if size < 8 or cursor + size > command_end:
            raise ValueError("Invalid Mach-O load command size")
        if command == 0x19:  # LC_SEGMENT_64
            if size < 72:
                raise ValueError("Truncated Mach-O segment")
            sections = struct.unpack_from(endian + "I", data, cursor + 64)[0]
            if 72 + sections * 80 > size:
                raise ValueError("Invalid Mach-O section table")
            for index in range(sections):
                section = cursor + 72 + index * 80
                name, segment = struct.unpack_from("16s16s", data, section)
                if (name.rstrip(b"\0"), segment.rstrip(b"\0")) != (b"__entitlements", b"__TEXT"):
                    continue
                length, offset = struct.unpack_from(endian + "QI", data, section + 40)
                if offset < command_end or not length or offset + length > len(data):
                    raise ValueError("Invalid Mach-O entitlement section bounds")
                value = plistlib.loads(data[offset:offset + length].rstrip(b"\0"))
                if not isinstance(value, dict):
                    raise ValueError("Simulator entitlements must be a dictionary")
                payloads.append(value)
        cursor += size
    if cursor != command_end or len(payloads) != 1:
        raise ValueError("Expected exactly one __TEXT,__entitlements section")
    return payloads[0]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: extract-simulator-entitlements.py <executable> <output.plist>")
    try:
        result = extract(Path(sys.argv[1]).read_bytes())
        Path(sys.argv[2]).write_bytes(plistlib.dumps(result))
    except (OSError, ValueError, struct.error, plistlib.InvalidFileException) as error:
        sys.exit(f"Unable to read signed Simulator entitlements: {error}")
