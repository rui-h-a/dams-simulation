"""Verify the retained evidence packages with the standard library."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files_sha256"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
            raise ValueError(f"Unexpected manifest path: {name}")
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"File checksum differs: {name}")

    checks = {}
    for name, expected in manifest["result_sha256"].items():
        if name not in manifest["files_sha256"]:
            raise ValueError(f"Verifier is absent from the file manifest: {name}")
        output = subprocess.check_output([sys.executable, "-I", "-S", "-B", str(ROOT / name)])
        actual = sha256(output).hexdigest()
        if actual != expected:
            raise ValueError(f"Recomputed output differs: {name}")
        checks[name] = actual

    print(json.dumps({"files_verified": len(manifest["files_sha256"]), "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
