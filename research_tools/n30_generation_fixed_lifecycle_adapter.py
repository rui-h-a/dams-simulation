"""Read-only case view over a separately root-admitted, metered GCSBackend.

No backend construction, token, auth, provider admission, upload, vault, ledger,
promotion or eviction is implemented here. Root supplies the unchanged existing
backend, exact original archive bytes, retained generation pins and a live guard.
The caller still owns physical failure-domain, budget and lifecycle admission.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time

SHA = re.compile(r"[0-9a-f]{64}\Z")
GENERATION = re.compile(r"[1-9][0-9]{0,39}\Z")
MAX_INDEX = 4 * 1024**2
MAX_OBJECT = 8 * 1024**2
READ_BLOCK = 65536


class AdapterRefusal(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise AdapterRefusal(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate metadata key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: require(False, "nonfinite metadata"))
    except (ValueError, UnicodeDecodeError):
        raise AdapterRefusal("invalid bounded JSON metadata") from None


def fixed_pin(pin, key, maximum):
    require(isinstance(pin, dict) and set(pin) == {
        "schema", "key", "bytes", "sha256", "generation"}, "fixed pin fields differ")
    require(pin["schema"] == "dams-compute-object-pin-v1" and pin["key"] == key
            and type(pin["bytes"]) is int and 0 < pin["bytes"] <= maximum
            and isinstance(pin["sha256"], str) and SHA.fullmatch(pin["sha256"])
            and isinstance(pin["generation"], str) and GENERATION.fullmatch(pin["generation"]),
            "fixed pin key/length/SHA/generation differs")
    return dict(pin)


class GenerationFixedCaseView:
    """Lifecycle read_manifest/read_object adapter; never caches provider bytes.

    deadline and allowance belong to one explicitly bounded root operation.
    Counts never reset. They are local I/O guards, not the original Store or
    RequestPool ledger and not an additional spending authorization.
    """
    def __init__(self, backend, *, case_id, archive_bytes, archive_sha256,
                 remote_pins, guard, deadline_monotonic,
                 max_total_bytes, max_read_calls):
        require(isinstance(case_id, str) and SHA.fullmatch(case_id), "case ID differs")
        require(type(archive_bytes) is bytes and 0 < len(archive_bytes) <= MAX_INDEX
                and isinstance(archive_sha256, str) and SHA.fullmatch(archive_sha256)
                and digest(archive_bytes) == archive_sha256, "original archive pin differs")
        require(callable(guard) and callable(getattr(backend, "read", None))
                and callable(getattr(backend, "_pin", None)), "existing root reader/guard required")
        require(type(deadline_monotonic) in (int, float) and math.isfinite(deadline_monotonic)
                and time.monotonic() < deadline_monotonic, "finite current deadline required")
        require(type(max_total_bytes) is int and 0 < max_total_bytes <= 1024**3
                and type(max_read_calls) is int and 0 < max_read_calls <= 65536,
                "bounded root read allowance required")
        archive = parse(archive_bytes)
        require(isinstance(archive, dict) and type(archive.get("schema")) is int
                and archive["schema"] == 1 and archive.get("case_id") == case_id
                and archive.get("codec") == "deflate-chunks-v1"
                and archive.get("restore_test") == "every-file-full-decode-to-bounded-sink-with-strict-EOF"
                and isinstance(archive.get("definition_sha256"), str)
                and SHA.fullmatch(archive["definition_sha256"])
                and isinstance(archive.get("files"), list) and len(archive["files"]) == 13,
                "original complete13 archive shape differs")
        objects = {}
        for row in archive["files"]:
            require(isinstance(row, dict) and isinstance(row.get("descriptor"), dict)
                    and isinstance(row["descriptor"].get("chunks"), list), "archive chunk roster differs")
            for chunk in row["descriptor"]["chunks"]:
                require(isinstance(chunk, dict) and isinstance(chunk.get("encoded_sha256"), str)
                        and SHA.fullmatch(chunk["encoded_sha256"])
                        and type(chunk.get("encoded_bytes")) is int
                        and 0 < chunk["encoded_bytes"] <= MAX_OBJECT, "encoded descriptor pin differs")
                sha, size = chunk["encoded_sha256"], chunk["encoded_bytes"]
                require(sha not in objects or objects[sha] == size, "encoded object size collision")
                objects[sha] = size
        key = case_id + "/index.json"
        require(isinstance(remote_pins, dict) and set(remote_pins) == {
            key, *("objects/" + sha + ".z" for sha in objects)}, "exact selected case pin roster required")
        pins = {key: fixed_pin(remote_pins[key], key, MAX_INDEX)}
        for sha, size in objects.items():
            object_key = "objects/" + sha + ".z"
            pin = fixed_pin(remote_pins[object_key], object_key, MAX_OBJECT)
            require(pin["bytes"] == size and pin["sha256"] == sha,
                    "archive object and durable generation pin differ")
            pins[object_key] = pin
        try:
            binding = dict(backend.binding)
        except (AttributeError, TypeError, ValueError):
            raise AdapterRefusal("existing immutable backend binding required") from None
        require(set(binding) == {"backup_id", "domain", "locator", "enrollment_sha256"}
                and binding["domain"] == "provider:google-cloud-storage"
                and isinstance(binding["locator"], str) and binding["locator"].startswith("gs://")
                and 5 < len(binding["locator"]) <= 4096
                and isinstance(binding["enrollment_sha256"], str)
                and SHA.fullmatch(binding["enrollment_sha256"]), "root GCS binding differs")
        self.backend = backend
        self.case_id = case_id
        self.archive_bytes = archive_bytes
        self.archive_sha256 = archive_sha256
        self.definition_sha256 = archive["definition_sha256"]
        self.pins = pins
        self.index_key = key
        self.objects = objects
        self.guard = guard
        self.deadline_monotonic = deadline_monotonic
        self.max_total_bytes = max_total_bytes
        self.max_read_calls = max_read_calls
        self.binding = binding
        self.failure_domain = binding["domain"]
        self.immutable_locator = (binding["locator"] + "/" + key
            + "#generation=" + pins[key]["generation"]
            + ";metadata_sha256=" + pins[key]["sha256"]
            + ";archive_sha256=" + archive_sha256)
        self.read_calls = 0
        self.read_bytes = 0

    def _live(self):
        try:
            self.guard()
            binding = dict(self.backend.binding)
        except BaseException:
            raise AdapterRefusal("original root guard or backend binding refused") from None
        require(binding == self.binding, "backend binding drift")
        require(time.monotonic() < self.deadline_monotonic, "root read deadline elapsed")

    def _check_pin(self, key):
        try:
            pin = fixed_pin(self.backend._pin(key), key,
                            MAX_INDEX if key == self.index_key else MAX_OBJECT)
        except BaseException:
            raise AdapterRefusal("existing durable generation pin refused") from None
        require(pin == self.pins[key], "existing durable generation pin drift")

    def _read_fixed(self, key):
        self._live()
        self._check_pin(key)
        require(self.read_calls < self.max_read_calls, "root read-call allowance exhausted")
        expected = self.pins[key]
        require(self.read_bytes + expected["bytes"] <= self.max_total_bytes,
                "root total read allowance insufficient")
        self.read_calls += 1
        stream = None
        try:
            # Existing GCSBackend.read performs generation headers/EOF checks and
            # its own original metered request. No transport or meter is rebuilt.
            stream = self.backend.read(key)
            require(callable(getattr(stream, "read", None))
                    and callable(getattr(stream, "close", None)), "bounded stream required")
            result = bytearray()
            while True:
                self._live()
                count = min(READ_BLOCK, expected["bytes"] - len(result) + 1)
                block = stream.read(count)
                require(type(block) is bytes and len(block) <= count,
                        "binary stream returned an unbounded block")
                self.read_bytes += len(block)
                require(self.read_bytes <= self.max_total_bytes, "root total read allowance exhausted")
                if not block:
                    break
                result.extend(block)
                require(len(result) <= expected["bytes"], "binary response has a trailing byte")
            require(len(result) == expected["bytes"] and digest(result) == expected["sha256"],
                    "complete binary EOF length/SHA differs")
            self._live()
            self._check_pin(key)
        except BaseException:
            if stream is not None:
                # Original GCS stream.close must charge failed/incomplete reads,
                # not report a successful ticket or attempt a second request.
                try:
                    stream.failed = True
                except (AttributeError, TypeError):
                    pass
            raise AdapterRefusal("generation-fixed full binary read refused") from None
        finally:
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    raise AdapterRefusal("original metered stream closure refused") from None
        # Successful close may run original transport/meter finalizers. Recheck
        # retained authority and pins only after that single close has returned.
        # A binary-success ticket is not a current lifecycle admission; refusing
        # here neither refunds the original request nor dispatches another one.
        self._live()
        self._check_pin(key)
        require(self.read_calls <= self.max_read_calls,
                "root read-call allowance exhausted")
        require(self.read_bytes <= self.max_total_bytes,
                "root total read allowance exhausted")
        return bytes(result)

    def read_manifest(self):
        metadata = parse(self._read_fixed(self.index_key))
        require(isinstance(metadata, dict)
                and metadata.get("schema") == "dams-n30-independent-case-index-v1"
                and isinstance(metadata.get("case"), dict)
                and metadata["case"].get("case_id") == self.case_id,
                "generation-fixed index belongs to another case")
        try:
            archive = base64.b64decode(metadata["archive_base64"], validate=True)
            definition = base64.b64decode(metadata["definition_base64"], validate=True)
        except (KeyError, ValueError, TypeError):
            raise AdapterRefusal("generation-fixed index base64 differs") from None
        require(archive == self.archive_bytes and digest(archive) == self.archive_sha256,
                "embedded archive is not the exact original production bytes")
        require(digest(definition) == self.definition_sha256,
                "embedded definition SHA differs from original archive")
        value = parse(definition)
        require(isinstance(value, dict) and value.get("case_id") == self.case_id,
                "embedded definition belongs to another case")
        return archive

    def read_object(self, encoded_sha256, expected_bytes):
        require(isinstance(encoded_sha256, str) and SHA.fullmatch(encoded_sha256)
                and encoded_sha256 in self.objects, "object outside fixed complete case")
        require(type(expected_bytes) is int and expected_bytes == self.objects[encoded_sha256],
                "requested object size differs from original descriptor")
        return self._read_fixed("objects/" + encoded_sha256 + ".z")
