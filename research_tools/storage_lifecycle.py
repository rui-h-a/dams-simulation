"""Closed-case, exact-byte storage maintenance, separate from scientific execution.

Only an explicit admitted roster is inspected. Originals are never removed. All
writers of an enrolled case must hold ``writer_lease``; enrollment additionally
requires a pinned root-issued terminal-owner proof. Backup locators/domains are
root-admitted; actual manifest/object bytes are read from each configured backend
and fully decoded. A boolean readback claim is never sufficient. This module
does not contact a provider or manufacture a scientific validation.

Archives use the existing bounded lossless codec. A restore test decodes EVERY
file/chunk to a bounded sink, checking EOF, lengths and SHA; ``restore_case`` also
materializes an explicit fresh destination. Neither operation runs a Model.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import math
import ctypes
import sys

import cloud_archive as codec
from cloud_control import GuardError

SCHEMA = 1
PROTOCOL = "dams-storage-lifecycle-v1"
CATEGORIES = {"original", "recovery", "regenerable"}
MAX_METADATA = 16 * 1024**2


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(data) > MAX_METADATA:
        raise GuardError("lifecycle metadata exceeds its bound")
    return data


def _key(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is None:
        raise GuardError("invalid case/archive identifier")
    return value


def _path(value):
    # lexical_path rejects user-controlled symlinks before resolve can hide one.
    return codec.lexical_path(value).resolve()


def _inside(path, roots):
    if not any(path == root or root in path.parents for root in roots):
        raise GuardError("path is outside the explicit admitted roots")


@contextmanager
def _directory(path):
    """Open each ancestor with O_NOFOLLOW, retaining an anchored directory FD."""
    path = _path(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = new
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _file(path):
    path = _path(path)
    with _directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise GuardError("lifecycle input is not a regular file")
            yield fd
        finally:
            os.close(fd)


def _identity(info):
    return list(codec.stat_identity(info))


def _rename_noreplace(source_fd, source_name, dest_fd, dest_name):
    """Atomic exclusion; never emulate it with a check-then-overwrite rename."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameatx_np" if sys.platform == "darwin" else "renameat2", None)
    if function is None: raise GuardError("atomic no-replace rename is unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    flag = 4 if sys.platform == "darwin" else 1
    if function(source_fd, os.fsencode(source_name), dest_fd, os.fsencode(dest_name), flag) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


@dataclass(frozen=True)
class Policy:
    primary_failure_domain: str
    minimum_free_bytes: int = 80 * 1024**3
    low_watermark_free_bytes: int = 96 * 1024**3
    internal_safe_floor_bytes: int = 32 * 1024**3
    max_archive_bytes: int = 256 * 1024**3
    max_files: int = 4096
    max_operation_seconds: float = 3600.0
    retention_seconds: float = 86400.0
    max_io_bytes_per_second: int = 16 * 1024**2

    def validate(self):
        if not self.primary_failure_domain or len(self.primary_failure_domain) > 256:
            raise GuardError("a concrete primary failure domain is required")
        for name in ("minimum_free_bytes", "low_watermark_free_bytes", "internal_safe_floor_bytes",
                     "max_archive_bytes", "max_files", "max_io_bytes_per_second"):
            codec.natural(getattr(self, name), name, positive=name in ("max_archive_bytes", "max_files"))
        if self.low_watermark_free_bytes < self.minimum_free_bytes or self.max_files > codec.MAX_ARCHIVE_FILES:
            raise GuardError("invalid storage watermarks/file bound")
        if (not isinstance(self.max_operation_seconds, (int, float)) or not math.isfinite(self.max_operation_seconds) or self.max_operation_seconds <= 0
                or not isinstance(self.retention_seconds, (int, float)) or not math.isfinite(self.retention_seconds) or self.retention_seconds < 0):
            raise GuardError("invalid lifecycle time bound")
        return self


class FilesystemBackend:
    """Read-only explicit CAS replica; actual device equality defeats fake domains.

    Remote adapters expose the same read_manifest()/read_object(sha, bytes)
    methods plus failure_domain and immutable_locator. Root installs the adapter;
    case metadata cannot inject callbacks or choose arbitrary destinations.
    """
    def __init__(self, manifest_file, objects_root, failure_domain):
        self.manifest_file = _path(manifest_file); self.objects_root = _path(objects_root)
        self.failure_domain = failure_domain
        self.immutable_locator = str(self.manifest_file)
        self.device = self.objects_root.stat().st_dev

    @staticmethod
    def _bytes(path, limit):
        with _file(path) as fd:
            before = _identity(os.fstat(fd)); value = bytearray()
            while block := os.read(fd, min(65536, limit + 1 - len(value))):
                value.extend(block)
                if len(value) > limit: raise GuardError("backup read exceeds its bounded allowance")
            if before != _identity(os.fstat(fd)) or before != _identity(_path(path).stat()):
                raise GuardError("backup changed during readback")
        return bytes(value)

    def read_manifest(self):
        return self._bytes(self.manifest_file, MAX_METADATA)

    def read_object(self, encoded_sha256, expected_bytes):
        return self._bytes(self.objects_root / codec.sha256(encoded_sha256), expected_bytes)


class Lifecycle:
    """One owned vault; source cases, proof roots and scratch roots are explicit."""

    def __init__(self, cases_root, vault_root, policy, *, gate_roots=(), internal_scratch=None, backup_backends=None):
        self.cases = _path(cases_root)
        self.vault = _path(vault_root)
        self.policy = policy.validate()
        if not self.cases.is_dir() or self.cases == self.vault or self.cases in self.vault.parents or self.vault in self.cases.parents:
            raise GuardError("case and lifecycle vault roots must be disjoint")
        self.vault.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.gate_roots = tuple(_path(p) for p in (self.cases, *gate_roots))
        self.internal = _path(internal_scratch) if internal_scratch is not None else None
        self.backends = dict(backup_backends or {})
        for key, backend in self.backends.items():
            _key(key)
            if not getattr(backend, "failure_domain", None) or not getattr(backend, "immutable_locator", None):
                raise GuardError("backup backend needs a root-admitted immutable locator/domain")
        for name in ("cases", "objects", "leases", "quarantine", "scratch"):
            (_path(self.vault / name)).mkdir(mode=0o700, exist_ok=True)
        self._local = threading.local()
        self._cancel = threading.Event()

    def _tick(self):
        deadline = getattr(self._local, "deadline", float("inf"))
        if self._cancel.is_set() or time.monotonic() > deadline:
            raise GuardError("lifecycle operation time bound exceeded; resume from its journal")

    def _io(self, size):
        self._tick()
        if not self.policy.max_io_bytes_per_second: return
        self._local.io_bytes = getattr(self._local, "io_bytes", 0) + size
        start = getattr(self._local, "io_started", time.monotonic())
        delay = start + self._local.io_bytes / self.policy.max_io_bytes_per_second - time.monotonic()
        while delay > 0:
            if self._cancel.wait(min(delay, 0.25)): self._tick()
            self._tick(); delay = start + self._local.io_bytes / self.policy.max_io_bytes_per_second - time.monotonic()

    @contextmanager
    def _lock(self, path):
        with _directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise GuardError("unsafe lifecycle lease")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise GuardError("case/store is actively leased") from None
                yield
            finally:
                os.close(fd)

    @contextmanager
    def writer_lease(self, case_id):
        """Producer integration: acquire before touching an enrolled case."""
        with self._lock(self.vault / "leases" / (_key(case_id) + ".lock")):
            yield

    @contextmanager
    def reader_lease(self, case_id):
        """Consumers that need a hot raw path can hold a shared nonblocking lease."""
        path = self.vault / "leases" / (_key(case_id) + ".lock")
        with _directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise GuardError("unsafe reader lease")
                try: fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError: raise GuardError("case is exclusively leased") from None
                yield
            finally: os.close(fd)

    @contextmanager
    def _operation(self, case_id):
        # Nonblocking leases never make a producer wait behind a maintenance job.
        with self._lock(self.vault / "store.lock"), self.writer_lease(case_id):
            self._local.deadline = time.monotonic() + self.policy.max_operation_seconds
            self._local.backend_checks = {}
            self._local.io_bytes = 0; self._local.io_started = time.monotonic()
            self._local.archive_view = None
            try:
                yield
            finally:
                self._local.deadline = float("inf")
                self._local.backend_checks = {}
                self._local.archive_view = None

    def _hash(self, path, expected=None):
        self._tick()
        with _file(path) as fd:
            before = _identity(os.fstat(fd)); h = hashlib.sha256(); total = 0
            while block := os.read(fd, codec.CHUNK_BYTES):
                self._io(len(block)); total += len(block); h.update(block)
            after = _identity(os.fstat(fd))
        if before != after or _identity(_path(path).stat()) != after:
            raise GuardError("file changed during exact-byte verification")
        value = {"sha256": h.hexdigest(), "bytes": total, "identity": after}
        if expected and (value["sha256"] != expected["sha256"] or total != expected["bytes"]):
            raise GuardError("full file SHA/length differs from its admitted pin")
        return value

    def _read(self, path):
        with _file(path) as fd:
            before = _identity(os.fstat(fd))
            if before[2] > MAX_METADATA:
                raise GuardError("lifecycle metadata exceeds its bound")
            data = bytearray()
            while block := os.read(fd, min(65536, MAX_METADATA + 1 - len(data))):
                data.extend(block)
                if len(data) > MAX_METADATA:
                    raise GuardError("lifecycle metadata grew beyond its bound")
            if before != _identity(os.fstat(fd)) or before != _identity(_path(path).stat()):
                raise GuardError("lifecycle metadata changed while reading")
        try:
            return json.loads(data), bytes(data)
        except (ValueError, UnicodeDecodeError):
            raise GuardError("invalid lifecycle JSON") from None

    def _write(self, path, value, *, immutable=False):
        self._tick(); data = _json(value)
        with _directory(path.parent) as parent:
            name = ".tmp-" + os.urandom(12).hex()
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                if immutable:
                    try:
                        os.link(name, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    except FileExistsError:
                        _, old = self._read(path)
                        if old != data:
                            raise GuardError("immutable lifecycle metadata drift")
                else:
                    os.rename(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try: os.unlink(name, dir_fd=parent)
                except FileNotFoundError: pass

    def _pin(self, value):
        if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
            raise GuardError("invalid explicitly admitted file pin")
        path = _path(value["path"]); _inside(path, self.gate_roots)
        codec.sha256(value["sha256"]); codec.natural(value["bytes"], "pin bytes")
        self._hash(path, value)
        return path

    def _gates(self, definition):
        roles = set()
        for gate in definition["gates"]:
            path = self._pin(gate["pin"]); roles.add(gate["role"])
            required = gate.get("required", {})
            if gate["role"] != "source" and not required:
                raise GuardError("a non-source gate needs explicit accepted result predicates")
            if required:
                value, _ = self._read(path)
                for field, expected in required.items():
                    parts = field.split("/")
                    current = value
                    try:
                        for part in parts:
                            current = current[int(part)] if isinstance(current, list) else current[part]
                    except (KeyError, IndexError, ValueError, TypeError):
                        raise GuardError("required gate field is absent") from None
                    if type(current) is not type(expected) or current != expected:
                        raise GuardError("pinned scientific/ownership gate is not accepted")
            if gate["role"] == "ownership":
                value, _ = self._read(path)
                if (value.get("case_id") != definition["case_id"] or value.get("status") != "closed"
                        or type(value.get("active_writer_count")) is not int or value["active_writer_count"] != 0
                        or value.get("writer_fence_verified") is not True):
                    raise GuardError("case has no actual closed-owner fence proof")
        if roles != {"source", "manifest", "result", "ownership"}:
            raise GuardError("source/manifest/scientific-result/closed-owner gates are all required")

    def register_closed_case(self, case_id, relative_case_dir, files, gates):
        """Pin a CLOSED generation. Each file has relative/sha256/bytes/category.

        cleanup_eligible defaults false; recovery cleanup additionally needs an
        exact retained_copy pin. Root must issue the terminal fence only after
        the original producer and all children have stopped using this case.
        """
        case_id = _key(case_id); relative_case_dir = str(codec.safe_name(relative_case_dir))
        folder = _path(self.cases / relative_case_dir); _inside(folder, (self.cases,))
        if not folder.is_dir(): raise GuardError("admitted case directory is absent")
        if not isinstance(files, list) or not files or len(files) > self.policy.max_files:
            raise GuardError("invalid explicit case file roster")
        seen = set(); total = 0
        for row in files:
            if not isinstance(row, dict) or set(row) - {"relative", "sha256", "bytes", "category", "cleanup_eligible", "retained_copy"}:
                raise GuardError("invalid case file descriptor")
            name = str(codec.safe_name(row["relative"]))
            if name in seen or row["category"] not in CATEGORIES: raise GuardError("duplicate/unknown artifact category")
            seen.add(name); codec.sha256(row["sha256"]); total += codec.natural(row["bytes"], "file bytes")
            if type(row.get("cleanup_eligible", False)) is not bool: raise GuardError("invalid cleanup eligibility")
            if row["category"] == "original" and row.get("cleanup_eligible", False):
                raise GuardError("immutable original scientific evidence cannot be a cleanup candidate")
            if row.get("cleanup_eligible", False) and any(_path(g["pin"]["path"]) == _path(folder / name) for g in gates):
                raise GuardError("source/manifest/result/owner proof bytes must be retained")
        if total > self.policy.max_archive_bytes: raise GuardError("case archive exceeds its admitted total bound")
        definition = {"schema": SCHEMA, "protocol": PROTOCOL, "case_id": case_id,
                      "relative_case_dir": relative_case_dir, "files": files, "gates": gates, "policy": asdict(self.policy)}
        with self._operation(case_id):
            self._gates(definition)
            for row in files:
                self._hash(folder / row["relative"], row)
                if "retained_copy" in row:
                    retained = self._pin(row["retained_copy"])
                    if retained == _path(folder / row["relative"]): raise GuardError("a recovery copy cannot retain itself")
                    if row["retained_copy"]["sha256"] != row["sha256"] or row["retained_copy"]["bytes"] != row["bytes"]:
                        raise GuardError("declared retained copy is not the same full bytes")
            dest = self.vault / "cases" / case_id
            dest.mkdir(mode=0o700, exist_ok=True)
            self._write(dest / "definition.json", definition, immutable=True)
            state = dest / "state.json"
            if not state.exists():
                self._write(state, {"schema": SCHEMA, "phase": "registered", "registered_utc": _utc(),
                                    "registered_epoch": time.time(), "cleanup_transactions": [], "audit": []})
        return definition

    def _definition(self, case_id):
        dest = self.vault / "cases" / _key(case_id)
        definition, _ = self._read(dest / "definition.json")
        if (definition.get("schema") != SCHEMA or definition.get("protocol") != PROTOCOL
                or definition.get("case_id") != case_id or definition.get("policy") != asdict(self.policy)):
            raise GuardError("registered case/policy identity differs")
        # Recorded paths remain constrained even after a restart.
        name = str(codec.safe_name(definition["relative_case_dir"]))
        folder = _path(self.cases / name); _inside(folder, (self.cases,))
        if not isinstance(definition.get("files"), list) or not 0 < len(definition["files"]) <= self.policy.max_files:
            raise GuardError("registered roster exceeds its bound")
        names = set(); total = 0
        for row in definition["files"]:
            name = str(codec.safe_name(row["relative"]))
            if name in names or row["category"] not in CATEGORIES:
                raise GuardError("registered file roster/category is unsafe")
            names.add(name); codec.sha256(row["sha256"]); total += codec.natural(row["bytes"], "file bytes")
            if row["category"] == "original" and row.get("cleanup_eligible", False):
                raise GuardError("registered original evidence cannot be deleted")
        if total > self.policy.max_archive_bytes: raise GuardError("registered archive exceeds its bound")
        return dest, definition, folder

    def _capacity(self, root, required, floor=None):
        codec.require_capacity(root, required, self.policy.minimum_free_bytes if floor is None else floor)

    @contextmanager
    def _scratch(self):
        # No full raw file ever enters scratch; at most two bounded codec chunks.
        reserve = 2 * codec.MAX_ENCODED_CHUNK + 65536
        chosen = self.vault / "scratch"
        if self.internal is not None and self.internal.is_dir():
            if shutil.disk_usage(self.internal).free >= self.policy.internal_safe_floor_bytes + reserve:
                chosen = self.internal
                self._capacity(chosen, reserve, self.policy.internal_safe_floor_bytes)
        if chosen == self.vault / "scratch": self._capacity(chosen, reserve)
        with tempfile.TemporaryDirectory(prefix="dams-lifecycle-", dir=chosen) as folder:
            yield Path(folder), (self.policy.internal_safe_floor_bytes if chosen == self.internal else self.policy.minimum_free_bytes)

    def _put_object(self, source, chunk):
        self._tick(); name = codec.sha256(chunk["encoded_sha256"]); target = self.vault / "objects" / name
        expected = {"sha256": name, "bytes": chunk["encoded_bytes"]}
        if target.exists():
            self._hash(target, expected)
            return False
        self._capacity(self.vault, chunk["encoded_bytes"] + 8192)
        # Quota uses only this tool's flat owned CAS, not a user-file crawl.
        used = sum(p.lstat().st_size for p in (self.vault / "objects").iterdir())
        if used + chunk["encoded_bytes"] > self.policy.max_archive_bytes:
            raise GuardError("owned content store exceeds its admitted watermark")
        with _directory(target.parent) as parent:
            temp = ".put-" + os.urandom(12).hex()
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as output, _file(source) as input_fd:
                    while block := os.read(input_fd, codec.MAX_ENCODED_CHUNK): output.write(block)
                    output.flush(); os.fsync(output.fileno())
                self._hash(target.parent / temp, expected)
                os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            finally:
                try: os.unlink(temp, dir_fd=parent)
                except FileNotFoundError: pass
        return True

    def _decode_file(self, descriptor):
        codec.validate_manifest(descriptor)
        h = hashlib.sha256(); total = 0
        for chunk in descriptor["chunks"]:
            self._tick(); path = self.vault / "objects" / codec.sha256(chunk["encoded_sha256"])
            with _file(path) as fd:
                before = _identity(os.fstat(fd))
                encoded = os.read(fd, chunk["encoded_bytes"] + 1)
                if os.read(fd, 1) or before != _identity(os.fstat(fd)):
                    raise GuardError("encoded object grew/changed")
            raw = codec.decode_chunk(encoded, chunk)
            self._io(len(encoded))
            h.update(raw); total += len(raw)
        if total != descriptor["raw_bytes"] or h.hexdigest() != descriptor["raw_sha256"]:
            raise GuardError("full restore-test file SHA/bytes differs")
        return total

    def _backend(self, definition, archive, archive_sha, pin):
        path = self._pin(pin); witness, _ = self._read(path)
        backend = self.backends.get(witness.get("backend_id"))
        if (witness.get("schema") != SCHEMA or witness.get("case_id") != definition["case_id"]
                or witness.get("issuer") != "root-admitted-independent-backup-v1"
                or witness.get("primary_failure_domain") != self.policy.primary_failure_domain
                or backend is None or witness.get("failure_domain") != backend.failure_domain
                or backend.failure_domain == self.policy.primary_failure_domain
                or witness.get("immutable_locator") != backend.immutable_locator
                or (isinstance(backend, FilesystemBackend) and _path(backend.objects_root).stat().st_dev
                    in {self.cases.stat().st_dev, self.vault.stat().st_dev})
                or witness.get("manifest_sha256") != archive_sha):
            raise GuardError("backup locator/domain/manifest is not independently admitted")
        try:
            expires = datetime.fromisoformat(witness["expires_utc"])
            if expires.tzinfo is None or expires.timestamp() <= time.time():
                raise GuardError("independent backup witness expired")
        except (KeyError, TypeError, ValueError):
            raise GuardError("invalid independent backup expiry") from None
        cache = self._local.backend_checks
        key = (pin["sha256"], archive_sha)
        if key in cache: return cache[key]
        data = backend.read_manifest()
        if (not isinstance(data, bytes) or len(data) > MAX_METADATA
                or len(data) != witness.get("manifest_bytes") or hashlib.sha256(data).hexdigest() != archive_sha):
            raise GuardError("actual backup manifest full SHA/bytes differs")
        try: remote = json.loads(data)
        except (ValueError, UnicodeDecodeError): raise GuardError("invalid actual backup manifest") from None
        if remote != archive: raise GuardError("backup complete file roster differs")
        total = encoded = chunks = 0
        for row in archive["files"]:
            descriptor = codec.validate_manifest(row["descriptor"]); h = hashlib.sha256(); size = 0
            for chunk in descriptor["chunks"]:
                self._tick(); block = backend.read_object(chunk["encoded_sha256"], chunk["encoded_bytes"]); self._tick()
                if not isinstance(block, bytes): raise GuardError("backup reader must return bounded binary bytes")
                self._io(len(block))
                raw = codec.decode_chunk(block, chunk)
                h.update(raw); size += len(raw); encoded += len(block); chunks += 1
            if size != descriptor["raw_bytes"] or h.hexdigest() != descriptor["raw_sha256"]:
                raise GuardError("actual full backup restoration SHA/bytes differs")
            total += size
        # Recheck the admission receipt after full backend reads; no flag is used.
        self._pin(pin)
        receipt = {"backend_id": witness["backend_id"], "failure_domain": backend.failure_domain,
                   "immutable_locator": backend.immutable_locator, "manifest_sha256": archive_sha,
                   "status": "ACTUAL_FULL_BACKEND_RESTORE_READBACK_PASS", "files": len(archive["files"]),
                   "raw_bytes": total, "encoded_bytes_read": encoded, "chunks_read": chunks,
                   "strict_eof": True, "observed_utc": _utc()}
        cache[key] = receipt
        return receipt

    def _archive(self, dest, definition, *, decode=True):
        archive, data = self._read(dest / "archive.json")
        if (archive.get("schema") != SCHEMA or archive.get("case_id") != definition["case_id"]
                or archive.get("definition_sha256") != hashlib.sha256(_json(definition)).hexdigest()
                or len(archive.get("files", [])) != len(definition["files"])):
            raise GuardError("archive/definition closure differs")
        for row, source in zip(archive["files"], definition["files"]):
            if row["file"] != source or row["descriptor"]["raw_sha256"] != source["sha256"] or row["descriptor"]["raw_bytes"] != source["bytes"]:
                raise GuardError("archive file roster differs")
            if decode: self._decode_file(row["descriptor"])
        return archive, hashlib.sha256(data).hexdigest()

    def archive_case(self, case_id):
        with self._operation(case_id):
            dest, definition, folder = self._definition(case_id); self._gates(definition)
            state, _ = self._read(dest / "state.json"); before_free = shutil.disk_usage(self.vault).free
            state["phase"] = "archiving"; self._write(dest / "state.json", state)
            stats = {"new_objects": 0, "reused_objects": 0, "new_encoded_bytes": 0, "deduplicated_encoded_bytes": 0}
            descriptors = []
            with self._scratch() as (scratch, floor):
                for index, row in enumerate(definition["files"]):
                    self._tick(); original = self._hash(folder / row["relative"], row)
                    part = dest / ("file-%06d.json" % index)
                    if part.exists():
                        descriptor, _ = self._read(part); self._decode_file(descriptor)
                        if descriptor["raw_sha256"] != row["sha256"] or descriptor["raw_bytes"] != row["bytes"]:
                            raise GuardError("resumed file descriptor differs from admitted raw bytes")
                    else:
                        def publish(path, chunk):
                            self._io(chunk["raw_bytes"])
                            fresh = self._put_object(path, chunk)
                            stats["new_objects" if fresh else "reused_objects"] += 1
                            stats["new_encoded_bytes" if fresh else "deduplicated_encoded_bytes"] += chunk["encoded_bytes"]
                        descriptor = codec.encode_file(folder / row["relative"], publish,
                            max_raw_bytes=row["bytes"], minimum_free_bytes=floor, scratch=scratch)
                        if descriptor["raw_sha256"] != row["sha256"]:
                            raise GuardError("raw bytes changed before archive encoding")
                        self._decode_file(descriptor)
                        self._write(part, descriptor, immutable=True)
                    if self._hash(folder / row["relative"], row) != original:
                        raise GuardError("original file identity changed during archive")
                    descriptors.append({"file": row, "descriptor": descriptor})
            self._gates(definition)
            archive = {"schema": SCHEMA, "case_id": case_id, "codec": codec.CODEC,
                "definition_sha256": hashlib.sha256(_json(definition)).hexdigest(), "files": descriptors,
                "restore_test": "every-file-full-decode-to-bounded-sink-with-strict-EOF"}
            self._write(dest / "archive.json", archive, immutable=True)
            _, archive_sha = self._archive(dest, definition)
            state["phase"] = "archived"; state["archive_sha256"] = archive_sha
            state["audit"].append({"operation": "archive", "utc": _utc(), **stats,
                "raw_bytes": sum(r["bytes"] for r in definition["files"]), "files": len(descriptors),
                "free_before": before_free, "free_after": shutil.disk_usage(self.vault).free,
                "scientific_validation": "external-pinned-result-gate", "new_scientific_samples": 0})
            self._write(dest / "state.json", state)
            return {"case_id": case_id, "status": "ARCHIVE_ALL_FILES_RESTORE_TEST_PASS", "archive_sha256": archive_sha, **stats}

    def verify_archive(self, case_id):
        with self._operation(case_id):
            dest, definition, _ = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition); self._gates(definition)
            return {"status": "ARCHIVE_ALL_FILES_RESTORE_TEST_PASS", "case_id": case_id,
                    "archive_sha256": digest, "files": len(archive["files"]),
                    "raw_bytes": sum(r["file"]["bytes"] for r in archive["files"]), "strict_eof": True}

    def verify_backend(self, case_id, backend_id):
        """Actual bounded full readback/restore of every file from ONE fixed replica."""
        with self._operation(case_id):
            dest, definition, _ = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition)
            for pin in self._backup_pins(dest, case_id, digest):
                path = self._pin(pin); witness, _ = self._read(path)
                if witness.get("backend_id") == backend_id:
                    result = self._backend(definition, archive, digest, pin)
                    self._gates(definition); return result
            raise GuardError("backup backend is not in this case's pinned roster")

    def admit_backups(self, case_id, pins):
        """After export/upload: seal actual independently restored replicas.

        Separate from the scientific/archive definition to avoid a circular
        archive-SHA/backup-witness-SHA dependency. ALL configured admitted pins
        are fully read and decoded; not merely one favorable replica.
        """
        if not isinstance(pins, list) or not pins or len(pins) > 8:
            raise GuardError("bounded explicit independent backup roster required")
        with self._operation(case_id):
            dest, definition, _ = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition)
            receipts = [self._backend(definition, archive, digest, pin) for pin in pins]
            self._gates(definition)
            binding = {"schema": SCHEMA, "case_id": case_id, "archive_sha256": digest, "pins": pins}
            self._write(dest / "backups.json", binding, immutable=True)
            self._write(dest / "backup-readback.json", {"schema": SCHEMA, "case_id": case_id,
                "archive_sha256": digest, "actual_readbacks": receipts})
            return {"status": "ALL_ADMITTED_BACKENDS_ACTUALLY_RESTORED", "case_id": case_id, "backends": receipts}

    def _backup_pins(self, dest, case_id, archive_sha):
        value, _ = self._read(dest / "backups.json")
        if (value.get("schema") != SCHEMA or value.get("case_id") != case_id
                or value.get("archive_sha256") != archive_sha or not isinstance(value.get("pins"), list)
                or not 0 < len(value["pins"]) <= 8):
            raise GuardError("independent replica binding differs from this complete archive")
        return value["pins"]

    def export_index(self, case_id):
        """Exact exportable archive index; no provider or archive-copy operation."""
        with self._operation(case_id):
            dest, definition, _ = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition)
            return {"archive": archive, "archive_sha256": digest, "archive_bytes": len(_json(archive)),
                    "objects": {c["encoded_sha256"]: c["encoded_bytes"] for r in archive["files"] for c in r["descriptor"]["chunks"]}}

    def restore_case(self, case_id, destination, *, backend_id=None):
        """Fresh path only; never overwrites a scientific case or a vault file."""
        destination = _path(destination)
        if (destination.exists() or destination == self.cases or self.cases in destination.parents
                or destination == self.vault or self.vault in destination.parents):
            raise GuardError("restore destination must be fresh and outside case/vault roots")
        with self._operation(case_id):
            dest, definition, _ = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition, decode=backend_id is None)
            backend = None
            if backend_id is not None:
                for pin in self._backup_pins(dest, case_id, digest):
                    witness_path = self._pin(pin); witness, _ = self._read(witness_path)
                    if witness.get("backend_id") == backend_id:
                        self._backend(definition, archive, digest, pin); backend = self.backends[backend_id]; break
                if backend is None: raise GuardError("restore backend is not admitted")
            raw = sum(row["file"]["bytes"] for row in archive["files"])
            self._capacity(destination.parent, raw + 2 * codec.MAX_ENCODED_CHUNK)
            destination.mkdir(mode=0o700)
            for row in archive["files"]:
                target = destination / row["file"]["relative"]
                def fetch(chunk, temporary):
                    if backend is not None:
                        block = backend.read_object(chunk["encoded_sha256"], chunk["encoded_bytes"])
                        codec.decode_chunk(block, chunk); self._io(len(block))
                        with temporary.open("xb") as out: out.write(block)
                    else:
                        source = self.vault / "objects" / chunk["encoded_sha256"]
                        self._hash(source, {"sha256": chunk["encoded_sha256"], "bytes": chunk["encoded_bytes"]})
                        with _file(source) as fd, temporary.open("xb") as out:
                            while block := os.read(fd, codec.MAX_ENCODED_CHUNK): out.write(block); self._io(len(block))
                codec.restore_file(row["descriptor"], fetch, target,
                    max_raw_bytes=row["file"]["bytes"], minimum_free_bytes=self.policy.minimum_free_bytes)
                self._hash(target, row["file"])
            self._gates(definition)
            return {"status": "MATERIALIZED_ALL_FILES_FULL_SHA_PASS", "case_id": case_id,
                    "archive_sha256": digest, "files": len(archive["files"]), "raw_bytes": raw}

    def hydrate_case(self, case_id, destination, *, backend_id=None):
        """Atomically expose an exact raw layout for the unchanged raw reader."""
        destination = _path(destination)
        if destination.exists() or destination == self.cases or self.cases in destination.parents or destination == self.vault or self.vault in destination.parents:
            raise GuardError("hydration target must be fresh and outside the owned source/vault")
        staging = destination.parent / (".hydrating-" + _key(case_id) + "-" + os.urandom(12).hex())
        result = self.restore_case(case_id, staging, backend_id=backend_id)
        with self._operation(case_id):
            _, definition, _ = self._definition(case_id); self._gates(definition)
            if destination.exists(): raise GuardError("hydration target appeared before commit")
            with _directory(destination.parent) as parent:
                # link/rename cannot silently replace an existing directory: the
                # admitted fresh leaf is rechecked and root holds ownership here.
                _rename_noreplace(parent, staging.name, parent, destination.name); os.fsync(parent)
        return {**result, "status": "ATOMIC_HYDRATION_FULL_SHA_PASS", "destination": str(destination), "new_scientific_samples": 0}

    def _backup(self, definition, row):
        dest = self.vault / "cases" / definition["case_id"]
        view = getattr(self._local, "archive_view", None)
        archive, digest = view if view is not None else self._archive(dest, definition)
        receipts = []
        for pin in self._backup_pins(dest, definition["case_id"], digest):
            receipts.append(self._backend(definition, archive, digest, pin))
        if not receipts: raise GuardError("no actual full restore-readback independent-domain backup for this case")
        return receipts

    def _retained(self, row):
        pin = row.get("retained_copy")
        if row["category"] == "recovery" and not pin:
            raise GuardError("necessary recovery cleanup requires a retained exact-byte copy")
        if pin:
            if pin["sha256"] != row["sha256"] or pin["bytes"] != row["bytes"]:
                raise GuardError("declared retained copy differs from the cleanup bytes")
            self._pin(pin)

    def _finish_transaction(self, dest, definition, folder, state, txn, *, promoted=False):
        row = next((r for r in definition["files"] if r["relative"] == txn["relative"]), None)
        is_original = row and row["category"] == "original"
        if not row or (is_original and not promoted) or (not is_original and not row.get("cleanup_eligible", False)):
            raise GuardError("cleanup journal is not an admitted derived artifact")
        if is_original:
            self._promotion(dest, definition)
        qname = _key(txn["quarantine_name"]); quarantine = self.vault / "quarantine" / qname
        source = folder / row["relative"]
        protected = {_path(g["pin"]["path"]) for g in definition["gates"]}
        protected.update(_path(r["retained_copy"]["path"]) for r in definition["files"] if "retained_copy" in r)
        if _path(source) in protected:
            raise GuardError("cleanup/resume cannot remove a retained recovery/gate target")
        self._gates(definition); self._backup(definition, row); self._retained(row)
        if txn["phase"] == "deleted": return False
        if not quarantine.exists():
            if not source.exists():
                if txn["phase"] != "delete-intent": raise GuardError("ambiguous cleanup journal: both paths absent")
                txn["phase"] = "deleted"; self._write(dest / "state.json", state); return False
            actual = self._hash(source, row)
            if actual["identity"] != txn["identity"]:
                raise GuardError("cleanup source identity changed after journaling")
            # Quarantine is private and on the SAME filesystem: no copying or
            # cross-device move of an irreplaceable file is attempted.
            if source.stat().st_dev != self.vault.stat().st_dev:
                raise GuardError("transactional quarantine needs the same filesystem")
            with _directory(source.parent) as parent, _directory(quarantine.parent) as qparent:
                if _identity(os.stat(source.name, dir_fd=parent, follow_symlinks=False)) != txn["identity"]:
                    raise GuardError("source swapped before quarantine rename")
                _rename_noreplace(parent, source.name, qparent, qname)
                os.fsync(parent); os.fsync(qparent)
        elif source.exists():
            raise GuardError("ambiguous cleanup journal: both original and quarantine present")
        actual = self._hash(quarantine, row)
        # rename changes ctime; inode/dev/size/mtime must still be exact.
        if actual["identity"][:4] != txn["identity"][:4]:
            raise GuardError("quarantine identity differs; nothing is deleted")
        txn["phase"] = "quarantined"; self._write(dest / "state.json", state)
        self._gates(definition); self._backup(definition, row); self._retained(row)
        # Complete immutable CAS/backends were fully decoded at the start of
        # THIS exclusive operation; no archive writer/GC can run under the lock.
        actual = self._hash(quarantine, row)
        txn["phase"] = "delete-intent"; self._write(dest / "state.json", state)
        with _directory(quarantine.parent) as qparent:
            if _identity(os.stat(qname, dir_fd=qparent, follow_symlinks=False)) != actual["identity"]:
                raise GuardError("quarantine changed before final unlink")
            os.unlink(qname, dir_fd=qparent); os.fsync(qparent)
        txn["phase"] = "deleted"; self._write(dest / "state.json", state)
        return True

    def cleanup_case(self, case_id):
        """Only redundant eligible derived bytes; originals are unconditionally kept."""
        with self._operation(case_id):
            dest, definition, folder = self._definition(case_id); self._gates(definition)
            archive, archive_sha = self._archive(dest, definition); self._local.archive_view = (archive, archive_sha)
            state, _ = self._read(dest / "state.json")
            if state.get("archive_sha256") != archive_sha:
                raise GuardError("cleanup lacks a committed full-restore-test archive receipt")
            if time.time() - state["registered_epoch"] < self.policy.retention_seconds:
                return {"status": "RETAINED_TTL", "case_id": case_id, "deleted_files": 0}
            before = shutil.disk_usage(self.vault).free
            unlinked = []
            for txn in state["cleanup_transactions"]:
                if txn.get("kind") != "promoted-original":
                    if self._finish_transaction(dest, definition, folder, state, txn): unlinked.append(txn)
            known = {t["relative"] for t in state["cleanup_transactions"]}
            retained_paths = {_path(r["retained_copy"]["path"]) for r in definition["files"] if "retained_copy" in r}
            for row in definition["files"]:
                if (row["category"] == "original" or not row.get("cleanup_eligible", False)
                        or row["relative"] in known or _path(folder / row["relative"]) in retained_paths): continue
                self._backup(definition, row); self._retained(row)
                actual = self._hash(folder / row["relative"], row)
                txn = {"relative": row["relative"], "quarantine_name": "txn-" + case_id + "-" + os.urandom(12).hex(),
                       "identity": actual["identity"], "phase": "planned", "bytes": row["bytes"]}
                state["cleanup_transactions"].append(txn); self._write(dest / "state.json", state)
                if self._finish_transaction(dest, definition, folder, state, txn): unlinked.append(txn)
            done = [t for t in state["cleanup_transactions"] if t["phase"] == "deleted"]
            self._gates(definition); self._archive(dest, definition)
            audit = {"operation": "cleanup", "utc": _utc(), "deleted_files": len(unlinked), "cumulative_deleted_files": len(done),
                     "logical_unlinked_bytes": sum(t["bytes"] for t in unlinked), "free_before": before,
                     "free_after": shutil.disk_usage(self.vault).free, "original_files_deleted": 0,
                     "free_delta_is_observation_not_exclusive_physical_reclaim": True}
            state["phase"] = "maintained"; state["audit"].append(audit); self._write(dest / "state.json", state)
            return {"status": "VERIFIED_REDUNDANT_DERIVED_CLEANUP_PASS", "case_id": case_id, **audit}

    def _promotion(self, dest, definition):
        record, _ = self._read(dest / "promotion.json")
        gate_path = self._pin(record["root_gate_pin"]); gate, _ = self._read(gate_path)
        archive, digest = getattr(self._local, "archive_view", None) or self._archive(dest, definition)
        if (record.get("case_id") != definition["case_id"] or record.get("archive_sha256") != digest
                or gate.get("case_id") != definition["case_id"] or gate.get("status") != "archive-authoritative"
                or gate.get("archive_sha256") != digest or gate.get("whole_raw_roster_sha256") != hashlib.sha256(_json(definition["files"])).hexdigest()
                or type(gate.get("hot_parent_dependency_count")) is not int or gate["hot_parent_dependency_count"] != 0
                or type(gate.get("active_writer_count")) is not int or gate["active_writer_count"] != 0
                or gate.get("all_scientific_raw_gates_passed") is not True
                or gate.get("source_guard_passed") is not True or gate.get("manifest_guard_passed") is not True
                or gate.get("raw_physical_eviction_authorized") is not True
                or gate.get("restore_recipe") != "storage-lifecycle-restore-case-v1"):
            raise GuardError("raw eviction has no exact whole-case/zero-hot-dependency promotion")
        receipts = self._backup(definition, definition["files"][0])
        domains = {self.policy.primary_failure_domain, *(r["failure_domain"] for r in receipts)}
        if len(domains) < 2: raise GuardError("promoted raw content lacks two actual-restored failure domains")
        return record, receipts

    def promote_archive(self, case_id, root_gate_pin):
        """Explicit root admission: original CONTENT moves to authoritative CAS.

        Ordinary cleanup cannot remove original raw paths. This promotion is
        separate, fully closed, never allowed for active/hot-parent cases.
        """
        with self._operation(case_id):
            dest, definition, folder = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition); self._local.archive_view = (archive, digest)
            for row in definition["files"]: self._hash(folder / row["relative"], row)
            self._pin(root_gate_pin)
            record = {"schema": SCHEMA, "case_id": case_id, "archive_sha256": digest, "root_gate_pin": root_gate_pin,
                      "authoritative_content": "lossless-content-addressed-archive", "raw_format_changed": False}
            # Validate before sealing: temporarily evaluate the same predicates.
            pending = dest / "promotion.json"
            if pending.exists():
                self._promotion(dest, definition)
                self._write(pending, record, immutable=True)
            else:
                gate, _ = self._read(_path(root_gate_pin["path"]))
                predicates = {"case_id": case_id, "status": "archive-authoritative", "archive_sha256": digest,
                    "whole_raw_roster_sha256": hashlib.sha256(_json(definition["files"])).hexdigest(),
                    "hot_parent_dependency_count": 0, "active_writer_count": 0, "all_scientific_raw_gates_passed": True,
                    "source_guard_passed": True, "manifest_guard_passed": True, "raw_physical_eviction_authorized": True,
                    "restore_recipe": "storage-lifecycle-restore-case-v1"}
                for key, expected in predicates.items():
                    if type(gate.get(key)) is not type(expected) or gate.get(key) != expected:
                        raise GuardError("root promotion gate has an absent/nonaccepted predicate")
                self._backup(definition, definition["files"][0]); self._gates(definition)
                self._write(pending, record, immutable=True)
            _, receipts = self._promotion(dest, definition)
            return {"status": "AUTHORITATIVE_ARCHIVE_PROMOTION_PASS", "case_id": case_id,
                    "archive_sha256": digest, "actual_backends": receipts, "physical_raw_evicted": False}

    def evict_closed_raw(self, case_id):
        """Explicit promoted-original path eviction. Unique bytes are retained."""
        with self._operation(case_id):
            dest, definition, folder = self._definition(case_id); self._gates(definition)
            archive, digest = self._archive(dest, definition); self._local.archive_view = (archive, digest)
            self._promotion(dest, definition)
            state, _ = self._read(dest / "state.json")
            if state.get("archive_sha256") != digest: raise GuardError("raw eviction archive commit differs")
            if time.time() - state["registered_epoch"] < self.policy.retention_seconds:
                return {"status": "RETAINED_TTL", "case_id": case_id, "raw_paths_evicted": 0}
            before = shutil.disk_usage(self.vault).free; unlinked = []
            for txn in state["cleanup_transactions"]:
                if txn.get("kind") == "promoted-original":
                    if self._finish_transaction(dest, definition, folder, state, txn, promoted=True): unlinked.append(txn)
            known = {t["relative"] for t in state["cleanup_transactions"]}
            gate_paths = {_path(g["pin"]["path"]) for g in definition["gates"]}
            gate_paths.update(_path(r["retained_copy"]["path"]) for r in definition["files"] if "retained_copy" in r)
            for row in definition["files"]:
                if row["category"] != "original" or row["relative"] in known or _path(folder / row["relative"]) in gate_paths: continue
                actual = self._hash(folder / row["relative"], row)
                txn = {"relative": row["relative"], "quarantine_name": "txn-" + case_id + "-" + os.urandom(12).hex(),
                       "identity": actual["identity"], "phase": "planned", "bytes": row["bytes"], "kind": "promoted-original"}
                state["cleanup_transactions"].append(txn); self._write(dest / "state.json", state)
                if self._finish_transaction(dest, definition, folder, state, txn, promoted=True): unlinked.append(txn)
            self._gates(definition); self._archive(dest, definition)
            audit = {"operation": "promoted-raw-physical-eviction", "utc": _utc(), "raw_paths_evicted": len(unlinked),
                     "logical_unlinked_bytes": sum(t["bytes"] for t in unlinked), "scientific_content_deleted": False,
                     "new_scientific_samples": 0, "free_before": before, "free_after": shutil.disk_usage(self.vault).free}
            state["audit"].append(audit); self._write(dest / "state.json", state)
            return {"status": "PROMOTED_ORIGINAL_CONTENT_PRESERVED_RAW_EVICTION_PASS", "case_id": case_id, **audit}

    def maintain_once(self, case_ids):
        """Explicit roster only; fail one case closed, continue unrelated cases."""
        if not isinstance(case_ids, (tuple, list)) or len(case_ids) > self.policy.max_files:
            raise GuardError("bounded explicit maintenance roster required")
        results = []
        for case_id in case_ids:
            try:
                dest, _, _ = self._definition(case_id)
                if not (dest / "archive.json").exists(): self.archive_case(case_id)
                if (dest / "promotion.json").exists():
                    results.append(self.evict_closed_raw(case_id))
                results.append(self.cleanup_case(case_id))
            except (GuardError, OSError) as exc:
                results.append({"case_id": case_id, "status": "RETAINED_FAIL_CLOSED", "reason": str(exc)})
        receipt = {"observed_utc": _utc(), "free_bytes": shutil.disk_usage(self.vault).free,
                "below_low_watermark": shutil.disk_usage(self.vault).free < self.policy.low_watermark_free_bytes,
                "cases": results}
        self._write(self.vault / "monitor-state.json", receipt)
        return receipt


class MaintenanceService:
    """Optional one-thread monitor hook. Root supplies CLOSED IDs, no discovery."""
    def __init__(self, lifecycle, closed_case_ids, interval_seconds=300.0):
        if interval_seconds <= 0: raise GuardError("invalid maintenance interval")
        self.lifecycle = lifecycle; self.closed_case_ids = closed_case_ids; self.interval = interval_seconds
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="dams-storage")
        self.pending = None; self.next_poll = 0.0

    def tick(self):
        """Nonblocking: return a finished receipt or schedule one bounded job."""
        result = None
        if self.pending is not None:
            if not self.pending.done(): return None
            result = self.pending.result(); self.pending = None
        if time.monotonic() >= self.next_poll:
            roster = tuple(self.closed_case_ids())
            self.pending = self.pool.submit(self.lifecycle.maintain_once, roster)
            self.next_poll = time.monotonic() + self.interval
        return result

    def close(self, wait=False):
        self.lifecycle._cancel.set()
        self.pool.shutdown(wait=wait, cancel_futures=True)
