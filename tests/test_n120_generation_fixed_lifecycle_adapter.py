"""Tiny in-memory complete13/37-object controls; no provider, Model or formal raw."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research_tools"))
import n30_generation_fixed_lifecycle_adapter as fixed
import n120_generation_fixed_lifecycle_adapter as n120


def blob(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pin(key, data, generation="123456"):
    return {"schema": "dams-compute-object-pin-v1", "key": key,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "generation": generation}


class TinyStream:
    def __init__(self, owner, data):
        self.owner = owner; self.data = data; self.offset = 0
        self.failed = False; self.eof = False; self.closed = False

    def read(self, count):
        data = self.data[self.offset:self.offset + count]
        self.offset += len(data)
        if not data: self.eof = True
        if self.owner.unbounded: return b"x" * (count + 1)
        return data

    def close(self):
        self.closed = True
        self.owner.closures.append({"failed": self.failed, "eof": self.eof})
        if self.owner.after_close: self.owner.after_close()
        if self.owner.close_error: raise RuntimeError("fixture close refused")


class TinyBackend:
    def __init__(self, data, pins):
        self.data = copy.deepcopy(data); self.pins = copy.deepcopy(pins)
        self.binding = {"backup_id": "tiny-gcs", "domain": "provider:google-cloud-storage",
                        "locator": "gs://tiny-fixture/prefix", "enrollment_sha256": "e" * 64}
        self.reads = []; self.closures = []; self.after_close = None
        self.close_error = False; self.unbounded = False

    def _pin(self, key): return copy.deepcopy(self.pins[key])

    def read(self, key):
        self.reads.append(key)
        return TinyStream(self, self.data[key])


class N120ViewTests(unittest.TestCase):
    def setUp(self):
        self.cid = "a" * 64
        self.recovery_key = self.cid + "/index.json"
        self.manifest_key = self.cid + "/manifest.json"
        data = {}; files = []
        for i in range(13):
            raw_parts = []; chunks = []
            for j in range(3 if i < 11 else 2):
                raw = ("tiny-file-%02d-part-%02d\n" % (i, j)).encode() * 3
                encoded = zlib.compress(raw); sha = fixed.digest(encoded)
                data["objects/" + sha + ".z"] = encoded; raw_parts.append(raw)
                chunks.append({"encoded_sha256": sha, "encoded_bytes": len(encoded),
                               "raw_sha256": fixed.digest(raw), "raw_bytes": len(raw)})
            raw = b"".join(raw_parts)
            file = {"relative": "tiny-%02d.bin" % i, "bytes": len(raw),
                    "sha256": fixed.digest(raw), "category": "original", "cleanup_eligible": False}
            descriptor = {"schema": 1, "codec": "deflate-chunks-v1", "chunk_bytes": 4 * 1024**2,
                          "chunks": chunks, "raw_sha256": fixed.digest(raw), "raw_bytes": len(raw),
                          "encoded_bytes": sum(c["encoded_bytes"] for c in chunks)}
            files.append({"file": file, "descriptor": descriptor})
        self.definition_bytes = blob({"schema": 1, "case_id": self.cid,
                                      "files": [r["file"] for r in files]})
        self.archive_bytes = blob({"schema": 1, "case_id": self.cid, "codec": "deflate-chunks-v1",
            "definition_sha256": fixed.digest(self.definition_bytes), "files": files,
            "restore_test": "every-file-full-decode-to-bounded-sink-with-strict-EOF"})
        data[self.recovery_key] = blob({"schema": "DAMS-N120-existing-full13-recovery-index-1",
                                        "case_id": self.cid})
        self.recovery_pin = pin(self.recovery_key, data[self.recovery_key])
        self.index = {"schema": n120.INDEX_SCHEMA, "case": {"case_id": self.cid},
            "archive_base64": base64.b64encode(self.archive_bytes).decode(),
            "definition_base64": base64.b64encode(self.definition_bytes).decode(),
            "source_recovery_index_pin": self.recovery_pin}
        data[self.manifest_key] = blob(self.index)
        self.backend = TinyBackend(data, {k: pin(k, v) for k, v in data.items()})
        self.pins = {k: p for k, p in self.backend.pins.items() if k != self.recovery_key}
        self.guard_refused = False

    def guard(self):
        if self.guard_refused: raise RuntimeError("fixture authority ended")

    def view(self, **changes):
        return n120.N120GenerationFixedCaseView(self.backend, **{
            "case_id": self.cid, "archive_bytes": self.archive_bytes,
            "archive_sha256": fixed.digest(self.archive_bytes),
            "definition_bytes": self.definition_bytes,
            "definition_sha256": fixed.digest(self.definition_bytes),
            "recovery_index_pin": self.recovery_pin, "remote_pins": self.pins,
            "guard": self.guard, "deadline_monotonic": time.monotonic() + 30,
            "max_total_bytes": 1024**2, "max_read_calls": 100, **changes})

    def rebind_index(self, data):
        self.backend.data[self.manifest_key] = data
        p = pin(self.manifest_key, data)
        self.pins[self.manifest_key] = p; self.backend.pins[self.manifest_key] = p

    def test_complete13_existing37_objects_and_exact_manifest(self):
        view = self.view()
        self.assertIsInstance(view, fixed.GenerationFixedCaseView)
        self.assertEqual(self.backend.reads, [])
        self.assertEqual(len(view.objects), 37)
        self.assertEqual(view.read_manifest(), self.archive_bytes)
        for sha, size in view.objects.items():
            self.assertEqual(view.read_object(sha, size), self.backend.data["objects/" + sha + ".z"])
        self.assertEqual(view.read_calls, 38)
        self.assertEqual(len(self.backend.closures), 38)
        self.assertTrue(all(r == {"failed": False, "eof": True} for r in self.backend.closures))
        self.assertIn("/manifest.json#generation=123456", view.immutable_locator)
        self.assertEqual(self.backend.pins[self.recovery_key], self.recovery_pin)

    def test_old_n30_default_still_uses_index_json(self):
        old = {"schema": "dams-n30-independent-case-index-v1", "case": {"case_id": self.cid},
               "archive_base64": self.index["archive_base64"],
               "definition_base64": self.index["definition_base64"]}
        self.backend.data[self.recovery_key] = blob(old)
        self.backend.pins[self.recovery_key] = pin(self.recovery_key, blob(old))
        pins = {k: p for k, p in self.backend.pins.items() if k != self.manifest_key}
        view = fixed.GenerationFixedCaseView(self.backend, case_id=self.cid,
            archive_bytes=self.archive_bytes, archive_sha256=fixed.digest(self.archive_bytes),
            remote_pins=pins, guard=self.guard, deadline_monotonic=time.monotonic() + 30,
            max_total_bytes=1024**2, max_read_calls=100)
        self.assertEqual(view.index_key, self.recovery_key)
        self.assertEqual(view.read_manifest(), self.archive_bytes)

    def test_exact_definition_constructor_binding(self):
        for changes in ({"definition_sha256": "f" * 64}, {"definition_bytes": b"{}"},
                        {"case_id": "b" * 64}, {"case_id": False}):
            with self.subTest(changes=changes), self.assertRaises(fixed.AdapterRefusal): self.view(**changes)
        altered = blob({"schema": 1, "case_id": self.cid, "files": []})
        with self.assertRaises(fixed.AdapterRefusal):
            self.view(definition_bytes=altered, definition_sha256=fixed.digest(altered))
        self.assertEqual(self.backend.reads, [])

    def test_exact_remote_roster_required(self):
        for mutation in ("missing", "extra", "recovery", "size", "sha", "generation"):
            pins = copy.deepcopy(self.pins); key = next(k for k in pins if k.startswith("objects/"))
            if mutation == "missing": del pins[key]
            elif mutation == "extra": pins["objects/" + "f" * 64 + ".z"] = pin("objects/" + "f" * 64 + ".z", b"x")
            elif mutation == "recovery": pins[self.recovery_key] = self.recovery_pin
            elif mutation == "size": pins[key]["bytes"] += 1
            elif mutation == "sha": pins[key]["sha256"] = "f" * 64
            else: pins[key]["generation"] = "latest"
            with self.subTest(mutation=mutation), self.assertRaises(fixed.AdapterRefusal): self.view(remote_pins=pins)
        self.assertEqual(self.backend.reads, [])

    def test_schema_case_recovery_and_fields_refuse(self):
        for mutation in ("schema", "case", "recovery", "extra", "missing"):
            index = copy.deepcopy(self.index)
            if mutation == "schema": index["schema"] = "dams-n30-independent-case-index-v1"
            elif mutation == "case": index["case"]["case_id"] = "b" * 64
            elif mutation == "recovery": index["source_recovery_index_pin"]["generation"] = "789"
            elif mutation == "extra": index["unadmitted"] = True
            else: del index["definition_base64"]
            self.rebind_index(blob(index))
            with self.subTest(mutation=mutation), self.assertRaises(fixed.AdapterRefusal): self.view().read_manifest()

    def test_archive_and_definition_must_match_exact_original_bytes(self):
        for field, raw in (("archive_base64", self.archive_bytes), ("definition_base64", self.definition_bytes)):
            index = copy.deepcopy(self.index)
            index[field] = base64.b64encode(raw + b" ").decode()
            self.rebind_index(blob(index))
            with self.subTest(field=field), self.assertRaises(fixed.AdapterRefusal): self.view().read_manifest()

    def test_invalid_base64_duplicate_json_and_nonfinite_refuse(self):
        index = copy.deepcopy(self.index); index["archive_base64"] = "!invalid!"
        malformed = [blob(index), b'{"schema":1,"schema":2}', b'{"schema":NaN}']
        for data in malformed:
            self.rebind_index(data)
            with self.subTest(data=data), self.assertRaises(fixed.AdapterRefusal): self.view().read_manifest()

    def test_durable_generation_drift_refuses_before_dispatch(self):
        for key in (self.manifest_key, self.recovery_key, next(k for k in self.pins if k.startswith("objects/"))):
            view = self.view(); original = self.backend.pins[key]["generation"]
            self.backend.pins[key]["generation"] = "789"
            before = len(self.backend.reads)
            with self.subTest(key=key), self.assertRaises(fixed.AdapterRefusal):
                if key.startswith("objects/"): view.read_object(key[8:-2], self.pins[key]["bytes"])
                else: view.read_manifest()
            self.assertEqual(len(self.backend.reads), before)
            self.backend.pins[key]["generation"] = original

    def test_binary_short_trailing_corrupt_and_unbounded_refuse_and_close(self):
        original = self.backend.data[self.manifest_key]
        for data in (original[:-1], original + b"x", b"x" + original[1:]):
            view = self.view(); self.backend.data[self.manifest_key] = data
            with self.subTest(bytes=len(data)), self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
            self.assertTrue(self.backend.closures[-1]["failed"])
        self.backend.data[self.manifest_key] = original; self.backend.unbounded = True
        with self.assertRaises(fixed.AdapterRefusal): self.view().read_manifest()
        self.assertTrue(self.backend.closures[-1]["failed"])

    def test_close_error_and_post_close_authority_loss_refuse(self):
        view = self.view(); self.backend.close_error = True
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        self.backend.close_error = False
        view = self.view(); self.backend.after_close = lambda: setattr(self, "guard_refused", True)
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        self.assertTrue(self.backend.closures[-1]["eof"])

    def test_post_read_recovery_pin_drift_refuses(self):
        view = self.view()
        self.backend.after_close = lambda: self.backend.pins[self.recovery_key].update(generation="789")
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()

    def test_recovery_pin_drift_after_manifest_blocks_object_dispatch(self):
        view = self.view(); view.read_manifest()
        self.backend.pins[self.recovery_key]["generation"] = "789"
        sha, size = next(iter(view.objects.items())); before = len(self.backend.reads)
        with self.assertRaises(fixed.AdapterRefusal): view.read_object(sha, size)
        self.assertEqual(len(self.backend.reads), before)

    def test_recovery_pin_drift_during_object_close_refuses(self):
        view = self.view(); view.read_manifest()
        self.backend.after_close = lambda: self.backend.pins[self.recovery_key].update(generation="789")
        sha, size = next(iter(view.objects.items()))
        with self.assertRaises(fixed.AdapterRefusal): view.read_object(sha, size)
        self.assertTrue(self.backend.closures[-1]["eof"])

    def test_live_binding_deadline_and_allowances_are_inherited(self):
        view = self.view(); self.backend.binding["locator"] += "-drift"
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        self.backend.binding["locator"] = "gs://tiny-fixture/prefix"
        view = self.view(); view.deadline_monotonic = time.monotonic() - 1
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        view = self.view(max_total_bytes=1)
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        view = self.view(max_read_calls=1); view.read_manifest()
        with self.assertRaises(fixed.AdapterRefusal): view.read_manifest()
        self.assertEqual(view.read_calls, 1)


if __name__ == "__main__": unittest.main()
