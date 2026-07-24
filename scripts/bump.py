#!/usr/bin/env python3
"""
bump.py — bump the `version:` substitution in apollo-air1-mqtt.yaml.

Mirrors ../chassis-shield-firmware/scripts/bump.py. The version string carries a
`-mqtt` suffix (e.g. "1.1.0-mqtt") to distinguish this firmware from upstream
Apollo builds; the suffix is preserved across bumps.

The version matters operationally, not just cosmetically: the device compares
the version in an incoming OTA command against its own compiled-in value and
skips the flash if they match. Shipping two different images under one version
means the second one silently won't install.

  python3 scripts/bump.py            # patch: 1.1.0-mqtt -> 1.1.1-mqtt
  python3 scripts/bump.py minor      # 1.1.0-mqtt -> 1.2.0-mqtt
  python3 scripts/bump.py major      # 1.1.0-mqtt -> 2.0.0-mqtt
  python3 scripts/bump.py 3.0.1      # explicit (suffix reapplied)
"""
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
YAML = os.path.join(ROOT, "Integrations", "ESPHome", "apollo-air1-mqtt.yaml")
PATTERN = re.compile(r'^(\s+version:\s*")([^"]+)(")', re.M)


def main():
    how = sys.argv[1] if len(sys.argv) > 1 else "patch"

    with open(YAML) as f:
        txt = f.read()
    m = PATTERN.search(txt)
    if not m:
        sys.exit(f"could not find a `version: \"...\"` substitution in {YAML}")

    current = m.group(2)
    core, sep, suffix = current.partition("-")
    parts = [int(p) if p.isdigit() else 0 for p in core.split(".")]
    parts += [0] * (3 - len(parts))
    major, minor, patch = parts[:3]

    if how == "patch":
        patch += 1
    elif how == "minor":
        minor, patch = minor + 1, 0
    elif how == "major":
        major, minor, patch = major + 1, 0, 0
    elif re.fullmatch(r'\d+\.\d+\.\d+', how):
        major, minor, patch = (int(x) for x in how.split("."))
    else:
        sys.exit(f"unknown bump '{how}' — use patch, minor, major, or X.Y.Z")

    new = f"{major}.{minor}.{patch}" + (sep + suffix if sep else "")
    if new == current:
        sys.exit(f"version unchanged ({current}) — nothing to do")

    with open(YAML, "w") as f:
        f.write(PATTERN.sub(lambda mm: mm.group(1) + new + mm.group(3), txt, count=1))
    print(f"version: {current} -> {new}")


if __name__ == "__main__":
    main()
