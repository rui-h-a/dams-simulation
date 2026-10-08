"""Unique run directories, atomic writes and provenance without credentials."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    """Bounded-memory hashes also cover multi-gigabyte states/checkpoints."""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_stream(path: Path, writer, *, max_bytes: int | None = None) -> None:
    """Atomically replace a streamed JSON file without retaining its bytes in RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    class BoundedWriter:
        def __init__(self, stream): self.stream, self.size = stream, 0
        def write(self, data):
            if isinstance(data, str): data = data.encode()
            self.size += len(data)
            if max_bytes is not None and self.size > max_bytes:
                raise RuntimeError("streamed output resource limit exceeded")
            return self.stream.write(data)
    try:
        with os.fdopen(handle, "wb") as stream:
            writer(BoundedWriter(stream))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)



def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: object) -> None:
    atomic_bytes(path, canonical(value)+b"\n")


def atomic_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        atomic_bytes(path, b"")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(path, out.getvalue().encode())


def source_hash() -> str:
    root = Path(__file__).parent
    data = b"".join(p.name.encode()+b"\0"+p.read_bytes() for p in sorted(root.glob("*.py")))
    return digest(data)


def provenance() -> dict:
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, True
        packaged = os.environ.get("DAMS_PACKAGED_COMMIT")
        manifest_path = os.environ.get("DAMS_SOURCE_MANIFEST")
        if packaged or manifest_path:
            if not packaged or len(packaged) != 40 or any(c not in "0123456789abcdef" for c in packaged) or not manifest_path:
                raise ValueError("packaged source requires commit and a checked source manifest")
            manifest_file = Path(manifest_path)
            package = json.loads(manifest_file.read_text())
            hashes = package.get("source_files_sha256")
            if package.get("commit") != packaged or not isinstance(hashes, dict) or not hashes:
                raise ValueError("packaged source manifest/commit mismatch")
            for name, expected in hashes.items():
                file = root/name
                if not file.resolve().is_relative_to(root.resolve()) or not file.is_file() or file.is_symlink() or file_digest(file) != expected:
                    raise ValueError("packaged source file integrity mismatch: "+name)
            required = {str(p.relative_to(root)) for p in (root/"dams_sim").glob("*.py")}
            if not required.issubset(hashes):
                raise ValueError("packaged source manifest omits a core module")
            commit, dirty = packaged, False
    return {"source_sha256": source_hash(), "git_commit": commit, "git_dirty": dirty,
            "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
            "dependencies": "Python standard library only", "container_image": None,
            "random_stream": "SHA256(seed,world,process,event,entity)/53-bit uniform; Box-Muller normal",
            "created_utc": datetime.now(timezone.utc).isoformat()}


def unique_run(root: Path, kind: str) -> Path:
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + kind + "-" + uuid.uuid4().hex[:12]
    path = root/name
    path.mkdir(parents=True, exist_ok=False)
    return path


def output_hashes(path: Path) -> dict[str, str]:
    return {str(p.relative_to(path)): file_digest(p) for p in sorted(path.rglob("*")) if p.is_file() and p.name != "manifest.json"}


def verify_outputs(path: Path, manifest: dict, *, required: tuple[str, ...] = ()) -> None:
    """Verify recorded bytes before using cached evidence or restarting.

    Hashes detect accidental change against the retained manifest. An unsigned
    manifest is not authentication against an attacker who can replace both.
    """
    hashes = manifest.get("output_sha256")
    if not isinstance(hashes, dict) or not hashes or any(name not in hashes for name in required):
        raise ValueError("required output integrity record is missing")
    for name, expected in hashes.items():
        file = path/name
        if not file.resolve().is_relative_to(path.resolve()) or not file.is_file():
            raise ValueError("recorded output is missing or outside the run directory")
        if file_digest(file) != expected:
            raise ValueError(f"output integrity mismatch: {name}")
