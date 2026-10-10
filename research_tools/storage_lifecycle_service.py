"""Bounded, restartable lifecycle runner and exact private-release replica reader.

The configuration and closed-case admissions are supplied by the research owner.
This runner never discovers arbitrary files, starts a model, uploads an asset,
or promotes original evidence. Science writers do not wait for this process.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

# Existing operational helpers use sibling absolute imports when run as files.
# Supply that same fixed, versioned helper directory for the module entry point.
sys.path.insert(0, str(Path(__file__).absolute().parent))
from . import storage_lifecycle as storage

MAX_JSON = 16 * 1024**2
MAX_ADMISSIONS = 512
HEX = re.compile(r"[0-9a-f]{64}\Z")


def read_json(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise storage.GuardError("symlink configuration refused")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON:
            raise storage.GuardError("configuration file exceeds its bound")
        data = stream.read(MAX_JSON + 1)
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)
        if len(data) > MAX_JSON or identity(before) != identity(after) or identity(after) != identity(path.stat()):
            raise storage.GuardError("configuration changed during read")
    return json.loads(data)


class ReleaseTarBackend:
    """Fresh bounded binary GET, then exact CAS reads from that verified transport.

    Every read_manifest begins a new remote readback. Cached metadata or receipt
    flags cannot replace the binary GET. Only fixed archive.json/objects members
    are admitted; a transport cache is removed at the end of the runner pass.
    gh owns authentication; no token, response header or stderr is recorded.
    """
    def __init__(self, config, cache_root, minimum_free_bytes, *, command=None):
        self.config = dict(config)
        self.cache_root = Path(cache_root).absolute()
        self.minimum_free_bytes = minimum_free_bytes
        self.failure_domain = config["failure_domain"]
        repo = config["repo"]
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo)
                or repo.split("/")[-1] in {".", ".."}):
            raise storage.GuardError("invalid fixed replica repository")
        asset = config["asset_id"]
        if type(asset) is not int or asset <= 0 or not HEX.fullmatch(config["tar_sha256"]):
            raise storage.GuardError("invalid exact replica locator/digest")
        size = config["tar_bytes"]
        if type(size) is not int or not 0 < size <= 2 * 1024**3:
            raise storage.GuardError("replica transport size exceeds its bound")
        timeout = config.get("timeout_seconds", 300)
        if type(timeout) not in (int, float) or not 0 < timeout <= 900:
            raise storage.GuardError("invalid replica callback deadline")
        self.immutable_locator = f"https://api.github.com/repos/{repo}/releases/assets/{asset}#sha256={config['tar_sha256']}"
        self.command = command or ["gh", "api", "--method", "GET", "-H",
            "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset}"]
        self.path = None
        self.members = {}
        self.io_callback = lambda size: None
        self.tick_callback = lambda: None

    def close(self):
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.members = {}

    def _fetch(self):
        self.close()
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.cache_root.is_symlink() or any(p.is_symlink() for p in self.cache_root.parents):
            raise storage.GuardError("unsafe replica cache root")
        size = self.config["tar_bytes"]
        if shutil.disk_usage(self.cache_root).free < self.minimum_free_bytes + size + 65536:
            raise storage.GuardError("replica cache would consume the research reserve")
        fd, name = tempfile.mkstemp(prefix="dams-replica-", suffix=".tar", dir=self.cache_root)
        self.path = Path(name)
        deadline = time.monotonic() + self.config.get("timeout_seconds", 300)
        digest = hashlib.sha256(); count = 0
        process = None; selector = selectors.DefaultSelector()
        try:
            with os.fdopen(fd, "wb") as output:
                process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    self.tick_callback()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise storage.GuardError("replica binary GET deadline exceeded")
                    if not selector.select(min(remaining, 0.25)):
                        continue
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        break
                    count += len(block)
                    if count > size:
                        raise storage.GuardError("replica binary GET exceeds exact size")
                    self.io_callback(len(block))
                    digest.update(block); output.write(block)
                    if shutil.disk_usage(self.cache_root).free < self.minimum_free_bytes:
                        raise storage.GuardError("replica readback reserve violated")
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                if code or count != size or digest.hexdigest() != self.config["tar_sha256"]:
                    raise storage.GuardError("actual replica transport SHA/length/exit differs")
                output.flush(); os.fsync(output.fileno())
            with tarfile.open(self.path, "r:") as archive:
                for member in archive:
                    self.tick_callback()
                    if time.monotonic() >= deadline:
                        raise storage.GuardError("replica TAR index deadline exceeded")
                    if len(self.members) >= 65536 or not member.isfile() or member.issparse() or member.name in self.members:
                        raise storage.GuardError("unsafe replica member roster")
                    if member.name == "archive.json":
                        allowed = 0 <= member.size <= MAX_JSON
                    elif member.name.startswith("objects/") and HEX.fullmatch(member.name[8:]):
                        allowed = 0 <= member.size <= storage.codec.MAX_ENCODED_CHUNK
                    else:
                        allowed = False
                    if not allowed:
                        raise storage.GuardError("unexpected replica member")
                    self.members[member.name] = (member.offset_data, member.size)
            if "archive.json" not in self.members:
                raise storage.GuardError("replica has no complete archive index")
        except BaseException:
            self.close()
            raise
        finally:
            selector.close()
            if process is not None:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                if process.stdout is not None:
                    process.stdout.close()

    def _member(self, name, limit):
        if self.path is None or name not in self.members:
            raise storage.GuardError("replica member is absent")
        offset, size = self.members[name]
        if size > limit:
            raise storage.GuardError("replica member exceeds its exact allowance")
        with self.path.open("rb") as stream:
            stream.seek(offset); data = stream.read(size)
        if len(data) != size:
            raise storage.GuardError("replica transport was truncated")
        return data

    def read_manifest(self):
        self._fetch()
        return self._member("archive.json", MAX_JSON)

    def read_object(self, encoded_sha256, expected_bytes):
        if not HEX.fullmatch(encoded_sha256):
            raise storage.GuardError("invalid encoded object key")
        return self._member("objects/" + encoded_sha256, expected_bytes)


def run_once(config_path):
    config = read_json(config_path)
    if config.get("schema") != "DAMS-storage-service-1":
        raise storage.GuardError("unknown storage service configuration")
    admissions = tuple(config["admissions"])
    if len(admissions) > MAX_ADMISSIONS or len(set(admissions)) != len(admissions):
        raise storage.GuardError("invalid finite admission roster")
    policy = storage.Policy(**config["policy"])
    backends = {key: ReleaseTarBackend(value, config["replica_cache"], policy.minimum_free_bytes)
                for key, value in config.get("release_backends", {}).items()}
    lifecycle = storage.Lifecycle(config["cases_root"], config["vault_root"], policy,
        gate_roots=config["gate_roots"], internal_scratch=config.get("internal_scratch"),
        backup_backends=backends)
    for backend in backends.values():
        backend.io_callback = lifecycle._io
        backend.tick_callback = lifecycle._tick
    results = []
    try:
        for path in admissions:
            try:
                admission = read_json(path)
                case_id = admission["case_id"]
                dest = lifecycle.vault / "cases" / storage._key(case_id)
                if not (dest / "definition.json").exists():
                    lifecycle.register_closed_case(case_id, admission["relative_case_dir"],
                        admission["files"], admission["gates"])
                _, existing, _ = lifecycle._definition(case_id)
                for key in ("case_id", "relative_case_dir", "files", "gates"):
                    if existing[key] != admission[key]:
                        raise storage.GuardError("immutable case admission drift")
                if not (dest / "archive.json").exists():
                    results.append(lifecycle.archive_case(case_id))
                eligible = any(row.get("cleanup_eligible", False) for row in admission["files"])
                if eligible:
                    pins = admission.get("backup_pins", [])
                    if not pins:
                        results.append({"case_id":case_id,"status":"RETAINED_AWAITING_INDEPENDENT_REPLICA"})
                        continue
                    state = read_json(dest / "state.json")
                    expected = sum(row.get("cleanup_eligible", False) for row in admission["files"])
                    done = sum(t.get("phase") == "deleted" for t in state["cleanup_transactions"])
                    if done < expected:
                        if not (dest / "backups.json").exists():
                            lifecycle.admit_backups(case_id, pins)
                        results.append(lifecycle.cleanup_case(case_id))
                    else:
                        lifecycle._gates(existing)
                        results.append({"case_id":case_id,"status":"ALREADY_MAINTAINED_NO_RAW_RESCAN"})
                else:
                    results.append({"case_id":case_id,"status":"ARCHIVED_ORIGINALS_RETAINED"})
            except (storage.GuardError, OSError, ValueError, KeyError) as exc:
                results.append({"admission":path,"status":"RETAINED_FAIL_CLOSED","reason":str(exc)})
        free = shutil.disk_usage(lifecycle.vault).free
        receipt = {"observed_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "free_bytes":free,"minimum_free_bytes":policy.minimum_free_bytes,
            "below_low_watermark":free < policy.low_watermark_free_bytes,
            "cases":results,"new_scientific_samples":0}
        lifecycle._write(lifecycle.vault / "monitor-state.json", receipt)
        return receipt
    finally:
        for backend in backends.values():
            backend.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--interval", type=float, default=300)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.interval <= 86400:
        parser.error("interval must be 1..86400 seconds")
    # Entirely separate process; lowest scheduling priority on both platforms.
    os.nice(19)
    while True:
        print(json.dumps(run_once(args.config), sort_keys=True), flush=True)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
