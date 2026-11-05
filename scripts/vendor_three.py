#!/usr/bin/env python3.15
"""Re-vendor three.js after a bump of `three` in package.json (design Sec 16.2).

Downloads the version package.json pins from registry.npmjs.org, checks it
against the sha512 integrity package-lock.json records, then rewrites the
three vendored files and VERSION in dashboard/vendor/three. Stdlib only, so
it needs no environment: `python3.15 scripts/vendor_three.py`.
"""
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "dashboard" / "vendor" / "three"
# Tarball member -> vendored file name. Members are looked up by these exact
# names, so no name from the archive itself ever becomes a path on disk.
MEMBERS = {
    "package/build/three.module.js": "three.module.js",
    "package/build/three.core.js": "three.core.js",
    "package/LICENSE": "LICENSE",
}


def pinned() -> tuple[str, str]:
    """The version package.json pins and the integrity the lockfile records for it."""
    version = json.loads((REPO / "package.json").read_text())["dependencies"]["three"]
    locked = json.loads((REPO / "package-lock.json").read_text())["packages"]["node_modules/three"]
    if locked["version"] != version:
        sys.exit("package-lock.json locks three %s but package.json pins %s; run "
                 "`npm install --package-lock-only --ignore-scripts` first" % (locked["version"], version))
    return version, locked["integrity"]


def download(url: str, integrity: str) -> bytes:
    algorithm, _, expected = integrity.partition("-")
    if algorithm != "sha512":
        sys.exit("expected a sha512 integrity in package-lock.json, got %r" % integrity)
    with urllib.request.urlopen(url, timeout=60) as response:
        tarball = response.read()
    if base64.b64encode(hashlib.sha512(tarball).digest()).decode() != expected:
        sys.exit("%s does not match the integrity in package-lock.json" % url)
    return tarball


def extract(tarball: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        files = {}
        for member_name, name in MEMBERS.items():
            member = tar.getmember(member_name)
            if not member.isreg():
                sys.exit("%s in the tarball is not a regular file" % member_name)
            files[name] = tar.extractfile(member).read()
        return files


def main() -> None:
    version, integrity = pinned()
    url = "https://registry.npmjs.org/three/-/three-%s.tgz" % version
    files = extract(download(url, integrity))
    lines = ["three %s" % version, "source %s (%s)" % (url, integrity)]
    for name, data in files.items():
        (VENDOR / name).write_bytes(data)
        lines.append("%s  %s" % (hashlib.sha256(data).hexdigest(), name))
    (VENDOR / "VERSION").write_text("\n".join(lines) + "\n")
    print("vendored three %s into %s" % (version, VENDOR))


if __name__ == "__main__":
    main()
