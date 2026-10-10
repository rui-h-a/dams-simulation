"""Durable, fail-closed upper-bound reservations for a root-funded HTTP probe.

This meter performs no network or original-budget writes. A returned ticket has
already been exclusively created and fsynced. Every attempt, including failures,
retries and pending attempts, remains charged at its full requested upper bound.
These are conservative reservations, not provider invoices or scientific results.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit


class Refusal(ValueError):
    """No network permission is granted when an accounting guard fails."""


CONFIG_SCHEMA = "dams-persistent-backend-budget-meter-config-v1"
EVENT_SCHEMA = "dams-adapter-http-attempt-v1"
GIB = Decimal(1073741824)
MAX_SMALL = 1024 * 1024
MAX_FILES = 2048
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
LIMIT_NAMES = (
    "max_archive_requests", "max_management_requests", "authentication_requests_margin",
    "max_uploaded_body_bytes", "max_downloaded_body_bytes", "max_retained_encoded_bytes",
)
PRICE_NAMES = (
    "class_a_usd_per_1000_upper", "class_b_usd_per_1000_upper",
    "standard_storage_usd_per_gib_hour_upper", "outgoing_usd_per_gib_upper",
    "unknown_transport_and_cleanup_margin_usd",
)


def require(condition, message):
    if not condition:
        raise Refusal(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate JSON key")
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Refusal("invalid bounded JSON") from exc


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _money(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
            and len(value) <= 80, "explicit finite nonnegative decimal string required")
    return Decimal(value)


def _integer(value, message):
    require(type(value) is int and 0 <= value <= 2**63 - 1, message)
    return value


def _utc(value):
    require(isinstance(value, str) and len(value) <= 40, "fixed UTC timestamp required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal("invalid UTC timestamp") from exc
    require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0,
            "UTC timezone required")
    return stamp


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
            stat.S_IMODE(st.st_mode))


def _absolute(value):
    require(isinstance(value, str) and value.startswith("/") and "\x00" not in value,
            "absolute local path required")
    path = Path(value)
    require(str(path) == value and all(part not in (".", "..") for part in value.split("/")[1:]),
            "canonical lexical path required")
    return path


@contextmanager
def _directory(path):
    """Open every directory component without following a symlink."""
    path = _absolute(str(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        before = os.fstat(fd)
        require(stat.S_ISDIR(before.st_mode), "directory required")
        yield fd
        current = os.stat(path, follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (before.st_dev, before.st_ino),
                "anchored directory changed")
    except OSError as exc:
        raise Refusal("nofollow directory access refused") from exc
    finally:
        os.close(fd)


def _read_file(path, maximum=MAX_SMALL, private=False):
    path = _absolute(str(path))
    try:
        with _directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum,
                        "bounded regular input required")
                if private:
                    require(stat.S_IMODE(before.st_mode) == 0o600, "private config/journal mode must be 0600")
                blocks, total = [], 0
                while block := os.read(fd, min(65536, maximum - total + 1)):
                    total += len(block)
                    require(total <= maximum, "bounded input grew")
                    blocks.append(block)
                require(_identity(before) == _identity(os.fstat(fd))
                        == _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                        "input bytes/stat changed during read")
                return b"".join(blocks), _identity(before)
            finally:
                os.close(fd)
    except OSError as exc:
        raise Refusal("nofollow file access refused") from exc


def _ref(ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}
            and isinstance(ref["sha256"], str) and SHA.fullmatch(ref["sha256"]), "exact input SHA pin required")
    raw, identity = _read_file(ref["path"])
    require(_sha(raw) == ref["sha256"], "fixed input SHA differs")
    return raw, identity


class Meter:
    """Meter(config_path, *, config_sha256, clock=time.time).

    Config contains task_id, state_dir,
    state_anchor {dev:int, ino:int, initial_ctime_ns:int},
    created_at_utc, global_deadline_utc,
    cumulative_cap_usd, baseline_held_usd, suballocation_usd; four path/SHA refs
    current_budget/original_budget/source_manifest/source_job; source_files refs;
    prices and limits matching the actual allocation; per_request_upload_bytes,
    per_request_download_bytes; and allowed_paths {backend_id:{method:[paths]}}.
    Optional generation_media_paths {backend_id:[exact object base paths]} permits
    only GET ?alt=media&generation=N&ifGenerationMatch=N, positive decimal N.
    generation_delete_paths permits only DELETE ?generation=N&ifGenerationMatch=N
    for its separately enrolled exact object bases. This is a meter fence, not
    authority to delete data; root must independently verify preservation first.
    It does not attest provider generations; the binary adapter must do that.

    before() refuses new requests after the original deadline. after() can close
    an already-reserved ticket after that deadline without granting new spending.
    Root precreates the private state directory and seals its native anchor.
    A durable head detects journal-tail truncation and remembers observed expiry
    and the greatest observed UTC time across reopen. No reservation is released.
    Crash-partial/inconsistent files cause a fail-closed refusal.
    """

    def __init__(self, config_path, *, config_sha256, clock=time.time):
        require(isinstance(config_sha256, str) and SHA.fullmatch(config_sha256), "config SHA required")
        self.config_path = str(_absolute(str(config_path)))
        self.config_sha256 = config_sha256
        self.clock = clock
        self._last_now = None
        raw, self.config_identity = _read_file(self.config_path, private=True)
        require(_sha(raw) == config_sha256, "root config SHA differs")
        self.config = _json(raw)
        self._validate_config()
        self.input_identities = {}
        self._check_inputs(first=True)
        self.root = _absolute(self.config["state_dir"])
        self._initialize_state()

    def _validate_config(self):
        c = self.config
        required = {"schema", "task_id", "state_dir", "state_anchor", "created_at_utc", "global_deadline_utc",
                    "cumulative_cap_usd", "baseline_held_usd", "suballocation_usd", "current_budget",
                    "original_budget", "source_manifest", "source_job", "source_files", "prices", "limits",
                    "per_request_upload_bytes", "per_request_download_bytes", "allowed_paths"}
        require(isinstance(c, dict) and required <= set(c)
                and set(c) <= required | {"generation_media_paths", "generation_delete_paths"}, "exact meter config fields required")
        require(c["schema"] == CONFIG_SCHEMA and isinstance(c["task_id"], str)
                and NAME.fullmatch(c["task_id"]), "root task identity differs")
        self.task_id = c["task_id"]
        require(_money(c["cumulative_cap_usd"]) > 0, "positive original authorization cap required")
        self.deadline = _utc(c["global_deadline_utc"]).timestamp()
        self.created = _utc(c["created_at_utc"]).timestamp()
        require(self.created < self.deadline and _money(c["suballocation_usd"]) > 0,
                "bounded root suballocation required")
        require(_money(c["baseline_held_usd"]) + _money(c["suballocation_usd"]) <= _money(c["cumulative_cap_usd"]),
                "original cumulative authorization exceeded")
        _absolute(c["state_dir"])
        require(isinstance(c["state_anchor"], dict)
                and set(c["state_anchor"]) == {"dev", "ino", "initial_ctime_ns"}
                and all(type(value) is int and value > 0 for value in c["state_anchor"].values()),
                "root-created state directory native anchor required")
        require(isinstance(c["source_files"], list) and 1 <= len(c["source_files"]) <= 16,
                "finite engineering source files required")
        require(len({r.get("path") for r in c["source_files"] if isinstance(r, dict)}) == len(c["source_files"]),
                "duplicate source pin")
        require(isinstance(c["prices"], dict) and set(c["prices"]) == set(PRICE_NAMES), "exact conservative prices required")
        self.prices = {key: _money(c["prices"][key]) for key in PRICE_NAMES}
        require(all(value > 0 for value in self.prices.values()), "positive prices/unknown-wire reserve required")
        require(isinstance(c["limits"], dict) and set(c["limits"]) == set(LIMIT_NAMES), "exact allocation limits required")
        for value in c["limits"].values():
            _integer(value, "typed byte/request limit required")
        require(0 < c["limits"]["max_archive_requests"] <= MAX_FILES
                and c["limits"]["max_management_requests"] > 0
                and c["limits"]["authentication_requests_margin"] > 0
                and c["limits"]["max_uploaded_body_bytes"] > 0
                and c["limits"]["max_downloaded_body_bytes"] > 0
                and c["limits"]["max_retained_encoded_bytes"] > 0,
                "explicit root request/byte boundaries differ")
        for name, total in (("per_request_upload_bytes", "max_uploaded_body_bytes"),
                            ("per_request_download_bytes", "max_downloaded_body_bytes")):
            value = _integer(c[name], "typed per-request bound required")
            require(0 < value <= c["limits"][total], "per-request bound exceeds cumulative bound")
        paths = c["allowed_paths"]
        require(isinstance(paths, dict) and 1 <= len(paths) <= 4, "finite root backend paths required")
        total = 0
        for backend, methods in paths.items():
            require(isinstance(backend, str) and NAME.fullmatch(backend)
                    and isinstance(methods, dict) and methods and set(methods) <= {"GET", "POST", "DELETE"},
                    "fixed backend/method paths required")
            for method, entries in methods.items():
                require(isinstance(entries, list) and entries and all(isinstance(item, str) for item in entries)
                        and len(set(entries)) == len(entries), "unique exact paths required")
                for path in entries:
                    self._path_safety(path)
                total += len(entries)
        for field in ("generation_media_paths", "generation_delete_paths"):
            dynamic = c.get(field, {})
            require(isinstance(dynamic, dict) and set(dynamic) <= set(paths), "unknown generation-path backend")
            for entries in dynamic.values():
                require(isinstance(entries, list) and all(isinstance(item, str) for item in entries)
                        and len(set(entries)) == len(entries), "unique generation objects required")
                for path in entries:
                    self._path_safety(path)
                    require(not urlsplit(path).query and re.fullmatch(r"/storage/v1/b/[^/]+/o/[^/]+", path),
                            "exact GCS object base path required")
                total += len(entries)
        require(total <= 2048, "root path roster exceeds finite bound")

    @staticmethod
    def _path_safety(path):
        require(isinstance(path, str) and 0 < len(path) <= 4096 and path.startswith("/")
                and not path.startswith("//") and not any(ord(ch) <= 32 or ord(ch) >= 127 for ch in path),
                "literal local HTTP resource path required")
        split = urlsplit(path)
        require(not split.scheme and not split.netloc and not split.fragment
                and ".." not in split.path.split("/") and "\\" not in path,
                "unsafe HTTP root path")
        try:
            query = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise Refusal("invalid resource query") from exc
        secret_names = {"token", "access_token", "authorization", "password", "secret", "key", "api_key", "credential"}
        require(all(key.lower() not in secret_names for key, _ in query), "secret-bearing query refused")

    def _check_inputs(self, first=False):
        raw, identity = _read_file(self.config_path, private=True)
        require(_sha(raw) == self.config_sha256 and identity == self.config_identity, "root config drift")
        contents = {}
        refs = {key: self.config[key] for key in ("current_budget", "original_budget", "source_manifest", "source_job")}
        refs.update({f"source:{index}": ref for index, ref in enumerate(self.config["source_files"])})
        for key, ref in refs.items():
            data, ident = _ref(ref)
            if first:
                self.input_identities[key] = ident
            else:
                require(self.input_identities[key] == ident, "fixed input native identity drift")
            contents[key] = data
        old, budget = _json(contents["original_budget"]), _json(contents["current_budget"])
        require(isinstance(old, dict) and isinstance(budget, dict) and isinstance(old.get("tasks"), dict)
                and isinstance(budget.get("tasks"), dict), "original budget schema differs")
        require(budget.get("authorization_id") == old.get("authorization_id")
                and _money(str(budget.get("cumulative_cap_usd"))) == _money(str(old.get("cumulative_cap_usd")))
                == _money(self.config["cumulative_cap_usd"])
                and budget.get("global_deadline_utc") == old.get("global_deadline_utc") == self.config["global_deadline_utc"]
                and budget.get("no_optimistic_refunds") is True, "original budget authority/deadline differs")
        require(self.task_id not in old["tasks"] and set(budget["tasks"]) == set(old["tasks"]) | {self.task_id}
                and all(budget["tasks"][key] == value for key, value in old["tasks"].items()),
                "prior original holds reset or task allocation differs")
        require(isinstance(old.get("history"), list) and isinstance(budget.get("history"), list)
                and budget["history"][:len(old["history"])] == old["history"]
                and all(budget.get(key) == value for key, value in old.items() if key not in {"tasks", "history"}),
                "original budget history/fields changed")
        task = budget["tasks"][self.task_id]
        require(isinstance(task, dict) and task.get("scientific_samples") == 0
                and type(task.get("scientific_samples")) is int and task.get("new_compute") is False
                and task.get("production_preservation_admitted") is False
                and task.get("original_holds_retained") is True and task.get("no_optimistic_refund") is True
                and task.get("global_deadline_utc") == self.config["global_deadline_utc"], "engineering-only allocation binding differs")
        require(_money(str(task.get("reserved_cost_usd_upper"))) == _money(self.config["suballocation_usd"])
                == _money(str(task.get("reserved_cost_usd_upper_not_refunded")))
                and _money(str(task.get("prior_total_envelopes_and_holds_usd"))) == _money(self.config["baseline_held_usd"]),
                "root actual reservation/prior holds differ")
        require(all(self.config["limits"][key] <= _integer(task.get(key), "root allocation limit missing")
                    for key in LIMIT_NAMES), "config limits exceed actual root allocation")
        require(all(self.prices[key] >= _money(str(task.get(key))) for key in PRICE_NAMES),
                "config rate below actual reserved price")
        reserved = _utc(task.get("reserved_utc"))
        require(reserved.timestamp() <= self.created < self.deadline, "allocation/config clock order differs")
        delta = _utc(self.config["global_deadline_utc"]) - reserved
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1000000)
        self.retention_hours = (seconds / Decimal(3600)).to_integral_value(rounding=ROUND_CEILING)
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_CEILING
            self.fixed_hold = (self.prices["unknown_transport_and_cleanup_margin_usd"]
                               + Decimal(self.config["limits"]["max_management_requests"]
                                         + self.config["limits"]["authentication_requests_margin"])
                               * self.prices["class_a_usd_per_1000_upper"] / Decimal(1000)
                               + Decimal(self.config["limits"]["max_retained_encoded_bytes"]) / GIB
                               * self.retention_hours * self.prices["standard_storage_usd_per_gib_hour_upper"])
        require(self.fixed_hold <= _money(self.config["suballocation_usd"]), "fixed uncertainty/retention hold exceeds allocation")

    def _now(self, minimum=None, new_request=False):
        now = self.clock()
        require(type(now) in (int, float) and math.isfinite(now) and now >= self.created,
                "invalid or pre-config accounting clock")
        require((self._last_now is None or now >= self._last_now)
                and (minimum is None or now >= minimum), "accounting clock moved backwards")
        self._last_now = now
        return now

    @staticmethod
    def _stamp(now):
        return datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")

    def _binding(self):
        return {"schema": "dams-http-meter-binding-v1", "config_sha256": self.config_sha256,
                "task_id": self.task_id, "global_deadline_utc": self.config["global_deadline_utc"],
                "state_anchor": self.config["state_anchor"],
                "fixed_hold_usd_upper": str(self.fixed_hold), "retention_hours_upper": str(self.retention_hours)}

    def _anchor(self, root):
        native = os.fstat(root)
        require({"dev": native.st_dev, "ino": native.st_ino}
                == {key: self.config["state_anchor"][key] for key in ("dev", "ino")},
                "root state directory replaced/reset")

    def _head(self):
        return {"schema": "dams-http-meter-head-v1", "config_sha256": self.config_sha256,
                "state_anchor": self.config["state_anchor"], "ticket_count": 0,
                "last_ticket_sha256": None, "results": {}, "cumulative": self._cumulative([]),
                "max_observed_utc": self.config["created_at_utc"], "deadline_observed": False}

    def _write_head(self, root, head):
        # Tickets/results remain immutable. Only this fsynced high-water metadata
        # is atomically replaced. Any crash-left temporary file refuses reopening.
        name = ".head-" + secrets.token_hex(16) + ".tmp"
        self._write(root, name, head)
        self._anchor(root)
        os.replace(name, "head.json", src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)

    def _observe(self, root, head, minimum, new_request=False):
        maximum = _utc(head["max_observed_utc"]).timestamp()
        now = self._now(max(minimum, maximum))
        expiry = head["deadline_observed"] or now >= self.deadline
        if now > maximum or expiry != head["deadline_observed"]:
            head = dict(head, max_observed_utc=self._stamp(now), deadline_observed=expiry)
            self._write_head(root, head)
        # The observed deadline is durable before this refusal, so another Meter
        # process cannot restore permission by reopening with a rolled-back clock.
        if new_request:
            require(not head["deadline_observed"] and now < self.deadline, "original global deadline reached")
        return now, head

    def _initialize_state(self):
        try:
            with _directory(self.root) as root:
                self._anchor(root)
                require(stat.S_IMODE(os.fstat(root).st_mode) == 0o700, "private state directory mode must be 0700")
                original_names = set(os.listdir(root))
                if not original_names:
                    require(os.fstat(root).st_ctime_ns == self.config["state_anchor"]["initial_ctime_ns"],
                            "empty state directory differs from root initial creation; reset refused")
                else:
                    require({"binding.json", "head.json", "lock", "tickets", "results"} <= original_names,
                            "existing state lacks durable original binding/head; reset refused")
                for name in ("tickets", "results"):
                    try:
                        os.mkdir(name, 0o700, dir_fd=root)
                    except FileExistsError:
                        pass
                    with _directory(self.root / name) as child:
                        require(stat.S_IMODE(os.fstat(child).st_mode) == 0o700,
                                "private journal directory mode must be 0700")
                os.fsync(root)
                try:
                    fd = os.open("lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root)
                    os.fsync(fd)
                    os.close(fd)
                    os.fsync(root)
                except FileExistsError:
                    pass
            with self._locked() as root:
                names = set(os.listdir(root))
                require(names <= {"lock", "binding.json", "head.json", "tickets", "results"}, "unexpected state files")
                if "binding.json" not in names:
                    require("head.json" not in names and not os.listdir(self.root / "tickets")
                            and not os.listdir(self.root / "results"),
                            "missing original binding with existing ledger")
                    self._write(root, "binding.json", self._binding())
                    self._write(root, "head.json", self._head())
                self._replay(root)
        except OSError as exc:
            raise Refusal("durable state initialization refused") from exc

    @contextmanager
    def _locked(self):
        with _directory(self.root) as root:
            self._anchor(root)
            fd = os.open("lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            try:
                identity = os.fstat(fd)
                require(stat.S_ISREG(identity.st_mode) and stat.S_IMODE(identity.st_mode) == 0o600,
                        "private regular accounting lock required")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refusal("another HTTP accounting owner holds the lock") from exc
                require((identity.st_dev, identity.st_ino) ==
                        (os.stat("lock", dir_fd=root, follow_symlinks=False).st_dev,
                         os.stat("lock", dir_fd=root, follow_symlinks=False).st_ino), "accounting lock replaced")
                self._check_inputs()
                yield root
                self._anchor(root)
                require((identity.st_dev, identity.st_ino) ==
                        (os.stat("lock", dir_fd=root, follow_symlinks=False).st_dev,
                         os.stat("lock", dir_fd=root, follow_symlinks=False).st_ino), "accounting lock changed")
            finally:
                os.close(fd)

    def _write(self, parent, name, value):
        """Partial/crash output is retained; it never grants a returned ticket."""
        raw = _encoded(value)
        require(len(raw) <= MAX_SMALL and "/" not in name, "bounded journal basename required")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                require(written > 0, "durable journal short write")
                offset += written
            os.fsync(fd)
            written_stat = os.fstat(fd)
            require(written_stat.st_size == len(raw)
                    and stat.S_IMODE(written_stat.st_mode) == 0o600
                    and _identity(written_stat) == _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    "durable journal file identity/bytes differ")
        finally:
            os.close(fd)
        os.fsync(parent)
        return _sha(raw)

    def _event(self, event):
        required = {"schema", "backend_id", "method", "path", "request_body_upper_bytes", "response_body_limit_bytes"}
        require(isinstance(event, dict) and required <= set(event)
                and set(event) <= required | {"request_body_sha256"}, "unknown/secret HTTP event field")
        require(event["schema"] == EVENT_SCHEMA, "HTTP event schema differs")
        backend, method, path = event["backend_id"], event["method"], event["path"]
        require(isinstance(backend, str) and backend in self.config["allowed_paths"]
                and method in ("GET", "POST", "DELETE"), "unknown backend/method")
        self._path_safety(path)
        permitted = path in self.config["allowed_paths"][backend].get(method, [])
        if not permitted and method in ("GET", "DELETE"):
            split = urlsplit(path)
            field = "generation_media_paths" if method == "GET" else "generation_delete_paths"
            if split.path in self.config.get(field, {}).get(backend, []):
                query = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
                if len(query) == (3 if method == "GET" else 2):
                    values = dict(query)
                    generation = values.get("generation", "")
                    expected = {"generation", "ifGenerationMatch"} | ({"alt"} if method == "GET" else set())
                    canonical = ({"alt": "media"} if method == "GET" else {})
                    canonical.update({"generation": generation, "ifGenerationMatch": generation})
                    permitted = (set(values) == expected
                                 and (method != "GET" or values.get("alt") == "media")
                                 and re.fullmatch(r"[1-9][0-9]{0,39}", generation) is not None
                                 and values.get("ifGenerationMatch") == generation
                                 and split.query == urlencode(canonical))
        require(permitted, "HTTP path outside immutable root admission")
        uploaded = _integer(event["request_body_upper_bytes"], "typed request-body upper bound required")
        downloaded = _integer(event["response_body_limit_bytes"], "typed response-body limit required")
        require(uploaded <= self.config["per_request_upload_bytes"]
                and downloaded <= self.config["per_request_download_bytes"], "per-request byte limit exceeded")
        require(method == "POST" or uploaded == 0, "GET/DELETE body refused")
        if "request_body_sha256" in event:
            value = event["request_body_sha256"]
            require(value is None or isinstance(value, str) and SHA.fullmatch(value), "invalid body digest")
            require(uploaded == 0 or value is not None, "nonempty body requires supplied digest")
        return dict(event)

    def _attempt_cost(self, event):
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_CEILING
            price = self.prices["class_b_usd_per_1000_upper"] if event["method"] == "GET" else self.prices["class_a_usd_per_1000_upper"]
            return price / Decimal(1000) + Decimal(event["response_body_limit_bytes"]) / GIB * self.prices["outgoing_usd_per_gib_upper"]

    def _cumulative(self, events):
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_CEILING
            cost = self.fixed_hold + sum((self._attempt_cost(e) for e in events), Decimal(0))
        totals = {"requests": len(events), "uploaded_body_bytes_upper": sum(e["request_body_upper_bytes"] for e in events),
                  "downloaded_body_bytes_upper": sum(e["response_body_limit_bytes"] for e in events),
                  "held_cost_usd_upper": str(cost)}
        limits = self.config["limits"]
        require(totals["requests"] <= limits["max_archive_requests"]
                and totals["uploaded_body_bytes_upper"] <= limits["max_uploaded_body_bytes"]
                and totals["downloaded_body_bytes_upper"] <= limits["max_downloaded_body_bytes"]
                and cost <= _money(self.config["suballocation_usd"]), "cumulative request/byte/cost reservation exceeded")
        return totals

    @staticmethod
    def _result(result, event):
        require(isinstance(result, dict) and set(result) == {"status", "response_body_bytes", "normal_eof", "failed"},
                "exact bounded result fields required")
        status = result["status"]
        require(status is None or type(status) is int and 100 <= status <= 599, "typed HTTP status required")
        count = _integer(result["response_body_bytes"], "typed actual response byte count required")
        require(count <= event["response_body_limit_bytes"] and type(result["normal_eof"]) is bool
                and type(result["failed"]) is bool, "response result exceeds original attempt bound")
        require(result["failed"] or status is not None and 200 <= status < 300 and result["normal_eof"],
                "incomplete/non-success response cannot be marked successful")
        require(status is not None or result["failed"] and not result["normal_eof"], "missing status must remain failed")
        return dict(result)

    def _replay(self, root):
        raw, _ = _read_file(self.root / "binding.json", private=True)
        require(raw == _encoded(self._binding()), "immutable accounting binding differs")
        require(set(os.listdir(root)) == {"lock", "binding.json", "head.json", "tickets", "results"}, "unexpected accounting root entry")
        head_raw, _ = _read_file(self.root / "head.json", private=True)
        head = _json(head_raw)
        require(isinstance(head, dict) and head_raw == _encoded(head)
                and set(head) == set(self._head()) and head["schema"] == "dams-http-meter-head-v1"
                and head["config_sha256"] == self.config_sha256
                and head["state_anchor"] == self.config["state_anchor"]
                and type(head["ticket_count"]) is int and type(head["deadline_observed"]) is bool
                and isinstance(head["results"], dict), "durable original high-water head differs")
        events, records, previous, latest = [], [], None, self.created
        with _directory(self.root / "tickets") as directory:
            names = sorted(os.listdir(directory))
            require(len(names) <= MAX_FILES and names == [f"{n:08d}.json" for n in range(1, len(names) + 1)],
                    "ticket sequence gap/unexpected entry")
            for sequence, name in enumerate(names, 1):
                raw, _ = _read_file(self.root / "tickets" / name, private=True)
                record = _json(raw)
                require(raw == _encoded(record) and set(record) == {"schema", "sequence", "previous_ticket_sha256", "config_sha256", "event", "timestamp_utc", "cumulative"}
                        and record["schema"] == "dams-http-meter-ticket-v1" and type(record["sequence"]) is int
                        and record["sequence"] == sequence and record["config_sha256"] == self.config_sha256
                        and record["previous_ticket_sha256"] == previous, "immutable ticket chain differs")
                event = self._event(record["event"])
                timestamp = _utc(record["timestamp_utc"]).timestamp()
                require(latest <= timestamp < self.deadline, "ticket timestamp/deadline differs")
                latest = timestamp
                events.append(event)
                require(record["cumulative"] == self._cumulative(events), "durable original counters differ")
                previous = _sha(raw)
                records.append((record, previous))
        completed, result_hashes = set(), {}
        with _directory(self.root / "results") as directory:
            names = sorted(os.listdir(directory))
            require(len(names) <= len(records), "result has no reserved request")
            for name in names:
                require(re.fullmatch(r"[0-9]{8}\.json", name), "unexpected result entry")
                sequence = int(name[:-5])
                require(1 <= sequence <= len(records) and sequence not in completed, "result sequence differs")
                raw, _ = _read_file(self.root / "results" / name, private=True)
                result = _json(raw)
                record, digest = records[sequence - 1]
                require(raw == _encoded(result) and set(result) == {"schema", "sequence", "ticket_sha256", "config_sha256", "result", "timestamp_utc", "closure_after_deadline"}
                        and result["schema"] == "dams-http-meter-result-v1" and type(result["sequence"]) is int
                        and result["sequence"] == sequence and result["ticket_sha256"] == digest
                        and result["config_sha256"] == self.config_sha256, "immutable result binding differs")
                self._result(result["result"], record["event"])
                timestamp = _utc(result["timestamp_utc"]).timestamp()
                require(timestamp >= _utc(record["timestamp_utc"]).timestamp()
                        and type(result["closure_after_deadline"]) is bool
                        and result["closure_after_deadline"] is (timestamp >= self.deadline), "result closure clock differs")
                latest = max(latest, timestamp)
                completed.add(sequence)
                result_hashes[str(sequence)] = _sha(raw)
        require(head["ticket_count"] == len(records) and head["last_ticket_sha256"] == previous
                and head["results"] == result_hashes and head["cumulative"] == self._cumulative(events),
                "durable high-water detects truncated/reset journal or counters")
        maximum = _utc(head["max_observed_utc"]).timestamp()
        require(maximum >= latest and head["deadline_observed"] is (maximum >= self.deadline),
                "durable time/expiry fence differs")
        return records, completed, latest, head

    def before(self, event):
        """Reserve an immutable full-bound attempt before caller starts network."""
        with self._locked() as root:
            records, _, latest, head = self._replay(root)
            now, head = self._observe(root, head, latest, new_request=True)
            event = self._event(event)
            cumulative = self._cumulative([record["event"] for record, _ in records] + [event])
            sequence = len(records) + 1
            record = {"schema": "dams-http-meter-ticket-v1", "sequence": sequence,
                      "previous_ticket_sha256": records[-1][1] if records else None,
                      "config_sha256": self.config_sha256, "event": event,
                      "timestamp_utc": self._stamp(now), "cumulative": cumulative}
            self._check_inputs()
            with _directory(self.root / "tickets") as directory:
                digest = self._write(directory, f"{sequence:08d}.json", record)
            self._write_head(root, dict(head, ticket_count=sequence, last_ticket_sha256=digest, cumulative=cumulative))
            self._check_inputs()
            return {"sequence": sequence, "ticket_sha256": digest, "config_sha256": self.config_sha256}

    def after(self, ticket, result):
        """Record actual result once; never refund shorter/failed/pending bodies."""
        require(isinstance(ticket, dict) and set(ticket) == {"sequence", "ticket_sha256", "config_sha256"}
                and type(ticket["sequence"]) is int, "exact returned ticket required")
        with self._locked() as root:
            records, completed, latest, head = self._replay(root)
            sequence = ticket["sequence"]
            require(1 <= sequence <= len(records) and sequence not in completed, "unknown/already-closed ticket")
            record, digest = records[sequence - 1]
            require(ticket["ticket_sha256"] == digest and ticket["config_sha256"] == self.config_sha256,
                    "returned ticket SHA differs")
            result = self._result(result, record["event"])
            now, head = self._observe(root, head, latest)
            value = {"schema": "dams-http-meter-result-v1", "sequence": sequence, "ticket_sha256": digest,
                     "config_sha256": self.config_sha256, "result": result,
                     "timestamp_utc": self._stamp(now), "closure_after_deadline": now >= self.deadline}
            self._check_inputs()
            with _directory(self.root / "results") as directory:
                result_sha = self._write(directory, f"{sequence:08d}.json", value)
            self._write_head(root, dict(head, results={**head["results"], str(sequence): result_sha}))
            self._check_inputs()

    def snapshot(self):
        """Counters readback retains a durable clock observation; no release API."""
        with self._locked() as root:
            records, completed, latest, head = self._replay(root)
            self._observe(root, head, latest)
            return {"task_id": self.task_id, "config_sha256": self.config_sha256,
                    **self._cumulative([record["event"] for record, _ in records]),
                    "completed_attempts": len(completed), "pending_attempts": len(records) - len(completed),
                    "fixed_hold_usd_upper": str(self.fixed_hold), "retention_hours_upper": str(self.retention_hours),
                    "unknown_transport_and_cleanup_margin_usd": str(self.prices["unknown_transport_and_cleanup_margin_usd"]),
                    "provider_invoice": False, "scientific_samples": 0, "new_compute": False}
