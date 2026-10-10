"""Finite engineering fixtures; never touch a formal case or a Model."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research_tools"))
import storage_lifecycle as sl


class ImmutableFixtureBackend:
    """Memory stands in for a root-selected immutable remote binary locator."""
    def __init__(self, exported, vault, key="offdevice-1"):
        self.failure_domain = key; self.immutable_locator = "fixture-immutable:" + key
        self.manifest = sl._json(exported["archive"])
        self.objects = {sha: (vault / "objects" / sha).read_bytes() for sha in exported["objects"]}
        self.reads = []
    def read_manifest(self):
        self.reads.append("manifest"); return self.manifest
    def read_object(self, sha, expected_bytes):
        self.reads.append(sha); return self.objects[sha]


class StorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Runner sets TMPDIR to its owned external validation namespace.
        self.tmp = tempfile.TemporaryDirectory(prefix="lifecycle-test-")
        self.root = Path(self.tmp.name).resolve(); self.cases = self.root / "science"
        self.proofs = self.root / "proofs"; self.vault = self.root / "vault"
        self.cases.mkdir(); self.proofs.mkdir(); self.folder = self.cases / "case-a"; self.folder.mkdir()
        self.policy = sl.Policy("fixture-source-device", minimum_free_bytes=0, low_watermark_free_bytes=0,
                                max_archive_bytes=64 * 1024**2, retention_seconds=0, max_io_bytes_per_second=0)
        self.lc = sl.Lifecycle(self.cases, self.vault, self.policy, gate_roots=[self.proofs])
        self.data = b"raw evidence\x00" * 1000
        (self.folder / "raw.bin").write_bytes(self.data)
        (self.folder / "derived.bin").write_bytes(self.data)
        (self.proofs / "source.py").write_bytes(b"source fixture, not a scientific claim\n")
        self.write_json(self.proofs / "manifest.json", {"case_id": "a", "status": "complete"})
        self.write_json(self.proofs / "result.json", {"case_id": "a", "valid": True})
        self.write_json(self.proofs / "owner.json", {"case_id": "a", "status": "closed", "active_writer_count": 0,
                                                  "writer_fence_verified": True})
        self.gates = [
            {"role": "source", "pin": self.pin(self.proofs / "source.py")},
            {"role": "manifest", "pin": self.pin(self.proofs / "manifest.json"), "required": {"status": "complete", "case_id": "a"}},
            {"role": "result", "pin": self.pin(self.proofs / "result.json"), "required": {"valid": True, "case_id": "a"}},
            {"role": "ownership", "pin": self.pin(self.proofs / "owner.json"), "required": {"status": "closed"}},
        ]
        self.files = [self.row("raw.bin", "original"), self.row("derived.bin", "regenerable", True)]

    def tearDown(self): self.tmp.cleanup()

    @staticmethod
    def write_json(path, value): path.write_bytes(sl._json(value))

    @staticmethod
    def pin(path):
        data = path.read_bytes()
        return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def row(self, name, category, eligible=False):
        pin = self.pin(self.folder / name)
        return {"relative": name, "sha256": pin["sha256"], "bytes": pin["bytes"],
                "category": category, "cleanup_eligible": eligible}

    def register(self): return self.lc.register_closed_case("a", "case-a", self.files, self.gates)

    def archive(self): self.register(); return self.lc.archive_case("a")

    def admit(self, count=1):
        export = self.lc.export_index("a"); pins = []; backends = []
        for index in range(count):
            key = "remote-%s" % index; backend = ImmutableFixtureBackend(export, self.vault, key)
            self.lc.backends[key] = backend; backends.append(backend)
            witness = {"schema": 1, "issuer": "root-admitted-independent-backup-v1", "case_id": "a", "backend_id": key,
                       "primary_failure_domain": self.policy.primary_failure_domain, "failure_domain": backend.failure_domain,
                       "immutable_locator": backend.immutable_locator, "manifest_sha256": export["archive_sha256"],
                       "manifest_bytes": export["archive_bytes"],
                       "expires_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            path = self.proofs / (key + ".json"); self.write_json(path, witness); pins.append(self.pin(path))
        result = self.lc.admit_backups("a", pins)
        return pins, backends, result

    def test_exact_codec_dedup_restore_and_original_retained(self):
        result = self.archive()
        self.assertEqual(result["new_objects"], 1); self.assertEqual(result["reused_objects"], 1)
        self.assertEqual(self.lc.verify_archive("a")["files"], 2)
        output = self.root / "restored"
        restore = self.lc.restore_case("a", output)
        self.assertEqual(restore["raw_bytes"], 2 * len(self.data))
        for name in ("raw.bin", "derived.bin"): self.assertEqual((output / name).read_bytes(), self.data)
        pins, backends, result = self.admit(2)
        for backend in backends:
            self.assertEqual(backend.reads.count("manifest"), 1)
            self.assertEqual(len(backend.reads), 3)  # two file readbacks, even shared CAS object
        cleanup = self.lc.cleanup_case("a")
        self.assertEqual(cleanup["deleted_files"], 1)
        self.assertFalse((self.folder / "derived.bin").exists())
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)
        self.assertEqual(self.lc.cleanup_case("a")["deleted_files"], 0)  # no invented second unlink
        self.assertEqual(self.lc.verify_backend("a", "remote-1")["raw_bytes"], len(self.data) * 2)

    def test_empty_file_and_multichunk_strict_roundtrip(self):
        (self.folder / "empty").write_bytes(b""); (self.folder / "large").write_bytes(b"A" * (sl.codec.CHUNK_BYTES + 123))
        self.files.extend([self.row("empty", "original"), self.row("large", "original")]); self.archive()
        restored = self.root / "roundtrip"; self.lc.restore_case("a", restored)
        self.assertEqual((restored / "empty").read_bytes(), b"")
        self.assertEqual((restored / "large").read_bytes(), (self.folder / "large").read_bytes())

    def test_cannot_classify_original_for_cleanup(self):
        self.files[0]["cleanup_eligible"] = True
        with self.assertRaises(sl.GuardError): self.register()
        self.assertTrue((self.folder / "raw.bin").exists())

    def test_cross_root_and_parent_traversal_rejected(self):
        with self.assertRaises(sl.GuardError): self.lc.register_closed_case("a", "../proofs", self.files, self.gates)
        self.files[1]["relative"] = "../outside"
        with self.assertRaises(sl.GuardError): self.register()
        outside = self.root / "unadmitted"; outside.write_bytes(b"x")
        self.gates[0]["pin"] = self.pin(outside)
        with self.assertRaises(sl.GuardError): self.register()

    def test_symlink_file_and_ancestor_rejected(self):
        (self.folder / "derived.bin").unlink(); (self.folder / "derived.bin").symlink_to(self.folder / "raw.bin")
        with self.assertRaises(sl.GuardError): self.register()
        alias = self.cases / "alias"; alias.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(sl.GuardError): self.lc.register_closed_case("a", "alias", self.files, self.gates)

    def test_active_writer_lease_blocks_maintenance(self):
        self.register()
        with self.lc.writer_lease("a"):
            with self.assertRaises(sl.GuardError): self.lc.archive_case("a")
        self.assertFalse((self.vault / "cases/a/archive.json").exists())

    def test_incomplete_or_unfenced_owner_and_invalid_result_rejected(self):
        for name, value, gate_index in [
            ("owner.json", {"case_id": "a", "status": "closed", "active_writer_count": 1, "writer_fence_verified": True}, 3),
            ("owner.json", {"case_id": "a", "status": "running", "active_writer_count": 0, "writer_fence_verified": True}, 3),
            ("result.json", {"case_id": "a", "valid": False}, 2),
        ]:
            path = self.proofs / name; self.write_json(path, value); self.gates[gate_index]["pin"] = self.pin(path)
            with self.assertRaises(sl.GuardError): self.register()

    def test_source_and_manifest_gates_are_fresh(self):
        self.register(); (self.proofs / "source.py").write_bytes(b"changed")
        with self.assertRaises(sl.GuardError): self.lc.archive_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_space_failure_retains_every_raw_byte(self):
        self.register()
        with mock.patch.object(sl.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10, 10, 0)):
            with self.assertRaises(sl.GuardError): self.lc.archive_case("a")
        for name in ("raw.bin", "derived.bin"): self.assertEqual((self.folder / name).read_bytes(), self.data)

    def test_archive_corruption_prevents_cleanup(self):
        self.archive(); self.admit()
        obj = next((self.vault / "objects").iterdir()); obj.chmod(0o600); obj.write_bytes(b"corrupt")
        with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_zlib_trailing_data_even_with_updated_encoded_sha_is_rejected(self):
        self.archive()
        dest = self.vault / "cases/a"; archive = json.loads((dest / "archive.json").read_bytes())
        chunk = archive["files"][0]["descriptor"]["chunks"][0]
        old = (self.vault / "objects" / chunk["encoded_sha256"]).read_bytes(); bad = old + b"trailing"
        sha = hashlib.sha256(bad).hexdigest(); (self.vault / "objects" / sha).write_bytes(bad)
        chunk["encoded_sha256"] = sha; chunk["encoded_bytes"] = len(bad)
        archive["files"][0]["descriptor"]["encoded_bytes"] = len(bad)
        self.write_json(dest / "archive.json", archive)
        with self.assertRaises(sl.GuardError): self.lc.verify_archive("a")

    def test_boolean_backup_witness_without_actual_reader_is_insufficient(self):
        self.archive()
        bogus = self.proofs / "bogus.json"
        self.write_json(bogus, {"schema": 1, "case_id": "a", "full_readback_verified": True, "restore_verified": True})
        with self.assertRaises(sl.GuardError): self.lc.admit_backups("a", [self.pin(bogus)])
        with self.assertRaises(OSError): self.lc.cleanup_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_all_backends_read_and_corrupt_second_replica_refuses_cleanup(self):
        self.archive(); pins, backends, _ = self.admit(2)
        backends[1].objects[next(iter(backends[1].objects))] = b"bad"
        with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_same_filesystem_replica_cannot_claim_an_independent_domain(self):
        self.archive(); export = self.lc.export_index("a")
        backend = sl.FilesystemBackend(self.vault / "cases/a/archive.json", self.vault / "objects", "pretend-different")
        self.lc.backends["fake"] = backend; pinpath = self.proofs / "fake.json"
        self.write_json(pinpath, {"schema": 1, "issuer": "root-admitted-independent-backup-v1", "case_id": "a", "backend_id": "fake",
            "primary_failure_domain": self.policy.primary_failure_domain, "failure_domain": backend.failure_domain,
            "immutable_locator": backend.immutable_locator, "manifest_sha256": export["archive_sha256"],
            "manifest_bytes": export["archive_bytes"], "expires_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        with self.assertRaises(sl.GuardError): self.lc.admit_backups("a", [self.pin(pinpath)])

    def test_recovery_requires_and_retains_distinct_exact_copy(self):
        self.files[1]["category"] = "recovery"; self.archive(); self.admit()
        with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_positive_recovery_duplicate_cleanup_with_current_checkpoint_kept(self):
        self.files[1]["category"] = "recovery"; self.files[1]["retained_copy"] = self.pin(self.folder / "raw.bin")
        self.archive(); self.admit(); self.lc.cleanup_case("a")
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)
        self.assertFalse((self.folder / "derived.bin").exists())

    def test_retention_ttl_defers_cleanup(self):
        self.policy = sl.Policy("fixture-source-device", minimum_free_bytes=0, low_watermark_free_bytes=0,
                                max_archive_bytes=64 * 1024**2, retention_seconds=86400)
        self.lc = sl.Lifecycle(self.cases, self.vault, self.policy, gate_roots=[self.proofs])
        self.archive(); self.admit(); self.assertEqual(self.lc.cleanup_case("a")["status"], "RETAINED_TTL")

    def test_crash_during_archive_reuses_immutable_partial_objects(self):
        self.register(); actual = self.lc._write
        def crash(path, value, **kwargs):
            if path.name == "file-000000.json": raise RuntimeError("fixture crash")
            return actual(path, value, **kwargs)
        with mock.patch.object(self.lc, "_write", side_effect=crash):
            with self.assertRaises(RuntimeError): self.lc.archive_case("a")
        self.assertFalse((self.vault / "cases/a/archive.json").exists())
        result = self.lc.archive_case("a"); self.assertEqual(result["new_objects"], 0)
        self.assertGreaterEqual(result["reused_objects"], 2)

    def test_crash_after_quarantine_rename_recovers_journal_without_raw_loss(self):
        self.archive(); self.admit(); actual = self.lc._write
        def crash(path, value, **kwargs):
            if path.name == "state.json" and any(t["phase"] == "quarantined" for t in value.get("cleanup_transactions", [])):
                raise RuntimeError("fixture crash after rename")
            return actual(path, value, **kwargs)
        with mock.patch.object(self.lc, "_write", side_effect=crash):
            with self.assertRaises(RuntimeError): self.lc.cleanup_case("a")
        self.assertFalse((self.folder / "derived.bin").exists())
        self.assertEqual(next((self.vault / "quarantine").iterdir()).read_bytes(), self.data)
        self.assertEqual(self.lc.cleanup_case("a")["deleted_files"], 1)
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)

    def test_toctou_swap_is_quarantined_not_unlinked(self):
        self.archive(); self.admit(); rename = sl._rename_noreplace; saved = self.folder / "saved-original"
        def swap(srcfd, src, dstfd, dst):
            if src == "derived.bin":
                sl.os.rename(self.folder / "derived.bin", saved)
                (self.folder / "derived.bin").write_bytes(b"unexpected unique input")
            return rename(srcfd, src, dstfd, dst)
        with mock.patch.object(sl, "_rename_noreplace", side_effect=swap):
            with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertEqual(saved.read_bytes(), self.data)
        self.assertEqual(next((self.vault / "quarantine").iterdir()).read_bytes(), b"unexpected unique input")
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)

    def test_source_payload_change_same_size_is_rejected(self):
        self.register(); original = (self.folder / "derived.bin").stat()
        (self.folder / "derived.bin").write_bytes(b"x" * len(self.data)); os.utime(self.folder / "derived.bin", ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(sl.GuardError): self.lc.archive_case("a")

    def test_internal_scratch_uses_only_bounded_reservation_and_external_fallback(self):
        internal = self.root / "internal-scratch"; internal.mkdir()
        self.lc.internal = internal
        actual = shutil.disk_usage
        def usage(path):
            if Path(path) == internal: return shutil._ntuple_diskusage(100, 90, 10)
            return actual(path)
        with mock.patch.object(sl.shutil, "disk_usage", side_effect=usage), self.lc._scratch() as (scratch, floor):
            self.assertEqual(scratch.parent, self.vault / "scratch")
        with mock.patch.object(sl.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(2**40, 0, 2**40)), self.lc._scratch() as (scratch, floor):
            self.assertEqual(scratch.parent, internal)

    def test_restore_never_overwrites_case_or_existing_path(self):
        self.archive()
        for target in (self.folder, self.cases / "another", self.vault / "new"):
            with self.assertRaises(sl.GuardError): self.lc.restore_case("a", target)

    def test_metadata_quota_and_policy_nans_fail_closed(self):
        with self.assertRaises(sl.GuardError): sl.Policy("x", max_operation_seconds=float("nan")).validate()
        self.policy = sl.Policy("fixture-source-device", minimum_free_bytes=0, low_watermark_free_bytes=0, max_archive_bytes=1)
        self.lc = sl.Lifecycle(self.cases, self.vault, self.policy, gate_roots=[self.proofs])
        with self.assertRaises(sl.GuardError): self.register()

    def test_periodic_service_is_nonblocking_and_explicit_roster_only(self):
        self.archive(); self.admit()
        service = sl.MaintenanceService(self.lc, lambda: ["a"], interval_seconds=1000)
        try:
            self.assertIsNone(service.tick()); result = service.pending.result(timeout=10)
            self.assertEqual(result["cases"][0]["deleted_files"], 1)
            self.assertEqual(service.tick()["cases"][0]["status"], "VERIFIED_REDUNDANT_DERIVED_CLEANUP_PASS")
        finally: service.close(wait=True)

    def promotion_pin(self, **changes):
        export = self.lc.export_index("a")
        value = {"case_id": "a", "status": "archive-authoritative", "archive_sha256": export["archive_sha256"],
                 "whole_raw_roster_sha256": hashlib.sha256(sl._json(self.files)).hexdigest(),
                 "hot_parent_dependency_count": 0, "active_writer_count": 0, "all_scientific_raw_gates_passed": True,
                 "source_guard_passed": True, "manifest_guard_passed": True, "raw_physical_eviction_authorized": True,
                 "restore_recipe": "storage-lifecycle-restore-case-v1", **changes}
        path = self.proofs / "promotion.json"; self.write_json(path, value); return self.pin(path)

    def test_closed_original_content_promotion_evicts_raw_and_exactly_hydrates(self):
        self.archive(); self.admit(2)
        result = self.lc.promote_archive("a", self.promotion_pin())
        self.assertFalse(result["physical_raw_evicted"])
        self.assertEqual(self.lc.evict_closed_raw("a")["raw_paths_evicted"], 1)
        self.assertFalse((self.folder / "raw.bin").exists())
        self.assertEqual(self.lc.evict_closed_raw("a")["raw_paths_evicted"], 0)
        restored = self.root / "hydrated"
        result = self.lc.hydrate_case("a", restored)
        self.assertEqual(result["new_scientific_samples"], 0)
        self.assertEqual((restored / "raw.bin").read_bytes(), self.data)

    def test_hot_parent_or_unaccepted_case_cannot_promote(self):
        self.archive(); self.admit()
        for changes in ({"hot_parent_dependency_count": 1}, {"all_scientific_raw_gates_passed": False},
                        {"active_writer_count": 1}, {"whole_raw_roster_sha256": "0" * 64},
                        {"raw_physical_eviction_authorized": False}):
            with self.assertRaises(sl.GuardError): self.lc.promote_archive("a", self.promotion_pin(**changes))
        self.assertTrue((self.folder / "raw.bin").exists())
        self.assertFalse((self.vault / "cases/a/promotion.json").exists())

    def test_promotion_requires_actual_independent_restore_not_a_json_authorization(self):
        self.archive()
        with self.assertRaises(OSError): self.lc.promote_archive("a", self.promotion_pin())
        with self.assertRaises(OSError): self.lc.evict_closed_raw("a")
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)

    def test_active_reader_lease_blocks_raw_eviction(self):
        self.archive(); self.admit(); self.lc.promote_archive("a", self.promotion_pin())
        with self.lc.reader_lease("a"):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a")
        self.assertTrue((self.folder / "raw.bin").exists())

    def test_original_gate_metadata_is_kept_even_after_promotion(self):
        p = self.folder / "manifest.json"; shutil.copyfile(self.proofs / "manifest.json", p)
        self.gates[1]["pin"] = self.pin(p); self.files.append(self.row("manifest.json", "original"))
        self.archive(); self.admit(); self.lc.promote_archive("a", self.promotion_pin()); self.lc.evict_closed_raw("a")
        self.assertTrue(p.exists())

    def test_restore_from_independent_backend_after_local_cas_corruption(self):
        self.archive(); self.admit(); self.lc.promote_archive("a", self.promotion_pin()); self.lc.evict_closed_raw("a")
        obj = next((self.vault / "objects").iterdir()); obj.chmod(0o600); obj.write_bytes(b"corrupt primary")
        output = self.root / "from-independent-replica"
        self.lc.hydrate_case("a", output, backend_id="remote-0")
        self.assertEqual((output / "raw.bin").read_bytes(), self.data)

    def test_atomic_hydration_no_replace_protects_racing_unique_destination(self):
        self.archive(); self.admit(); destination = self.root / "hydration-race"; original = sl._rename_noreplace
        def race(srcfd, src, dstfd, dst):
            if dst == destination.name: destination.write_bytes(b"unique user bytes")
            return original(srcfd, src, dstfd, dst)
        with mock.patch.object(sl, "_rename_noreplace", side_effect=race):
            with self.assertRaises(OSError): self.lc.hydrate_case("a", destination)
        self.assertEqual(destination.read_bytes(), b"unique user bytes")

    def test_cancel_has_resumable_state_and_does_not_delete_originals(self):
        self.register(); self.lc._cancel.set()
        with self.assertRaises(sl.GuardError): self.lc.archive_case("a")
        self.assertEqual((self.folder / "raw.bin").read_bytes(), self.data)
        self.lc._cancel.clear(); self.assertEqual(self.lc.archive_case("a")["status"], "ARCHIVE_ALL_FILES_RESTORE_TEST_PASS")

    def test_private_vault_object_symlink_cannot_escape_backup_or_archive_read(self):
        self.archive(); obj = next((self.vault / "objects").iterdir()); data = obj.read_bytes()
        outside = self.root / "outside-encoded"; outside.write_bytes(data)
        obj.unlink(); obj.symlink_to(outside)
        with self.assertRaises(sl.GuardError): self.lc.verify_archive("a")
        self.assertEqual(outside.read_bytes(), data)

    def test_eligible_retained_target_is_kept_when_recovery_file_is_cleaned(self):
        (self.folder / "keep.bin").write_bytes(self.data)
        self.files[1]["category"] = "recovery"
        self.files[1]["retained_copy"] = self.pin(self.folder / "keep.bin")
        self.files.append(self.row("keep.bin", "regenerable", True))
        self.archive(); self.admit(); result = self.lc.cleanup_case("a")
        self.assertEqual(result["deleted_files"], 1)
        self.assertFalse((self.folder / "derived.bin").exists())
        self.assertEqual((self.folder / "keep.bin").read_bytes(), self.data)

    def test_resume_cannot_unlink_previously_planned_retained_target(self):
        (self.folder / "keep.bin").write_bytes(self.data)
        self.files[1]["category"] = "recovery"; self.files[1]["retained_copy"] = self.pin(self.folder / "keep.bin")
        self.files.append(self.row("keep.bin", "regenerable", True))
        self.archive(); self.admit(); path = self.vault / "cases/a/state.json"
        state = json.loads(path.read_bytes()); state["cleanup_transactions"].append({"relative": "keep.bin",
            "quarantine_name": "txn-old-planned-retained", "identity": self.lc._hash(self.folder / "keep.bin")["identity"],
            "phase": "planned", "bytes": len(self.data)})
        self.write_json(path, state)
        with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertEqual((self.folder / "keep.bin").read_bytes(), self.data)
        self.assertTrue((self.folder / "derived.bin").exists())

    def test_every_backend_object_read_is_io_throttled(self):
        self.archive(); export = self.lc.export_index("a"); backend = ImmutableFixtureBackend(export, self.vault, "remote-rate")
        key = "remote-rate"; self.lc.backends[key] = backend; events = []
        actual_read = backend.read_object; actual_io = self.lc._io
        def read(sha, expected): events.append(("backend", expected)); return actual_read(sha, expected)
        def throttled(size): events.append(("io", size)); return actual_io(size)
        backend.read_object = read
        witness = {"schema": 1, "issuer": "root-admitted-independent-backup-v1", "case_id": "a", "backend_id": key,
            "primary_failure_domain": self.policy.primary_failure_domain, "failure_domain": backend.failure_domain,
            "immutable_locator": backend.immutable_locator, "manifest_sha256": export["archive_sha256"],
            "manifest_bytes": export["archive_bytes"], "expires_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
        path = self.proofs / "rate.json"; self.write_json(path, witness)
        with mock.patch.object(self.lc, "_io", side_effect=throttled): self.lc.admit_backups("a", [self.pin(path)])
        reads = [i for i, event in enumerate(events) if event[0] == "backend"]
        self.assertEqual(len(reads), 2)
        for i in reads: self.assertEqual(events[i + 1], ("io", events[i][1]))

    def test_filesystem_backup_same_vault_device_is_not_independent_when_cases_differ(self):
        self.archive(); export = self.lc.export_index("a")
        backend = sl.FilesystemBackend(self.vault / "cases/a/archive.json", self.vault / "objects", "pretend-different")
        self.lc.backends["vault-alias"] = backend; path = self.proofs / "vault-alias.json"
        self.write_json(path, {"schema": 1, "issuer": "root-admitted-independent-backup-v1", "case_id": "a", "backend_id": "vault-alias",
            "primary_failure_domain": self.policy.primary_failure_domain, "failure_domain": backend.failure_domain,
            "immutable_locator": backend.immutable_locator, "manifest_sha256": export["archive_sha256"],
            "manifest_bytes": export["archive_bytes"], "expires_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        actual_stat = Path.stat
        def separate_case_device(p, *args, **kwargs):
            original = actual_stat(p, *args, **kwargs)
            if p == self.cases:
                values = list(original); values[2] += 1
                return os.stat_result(values)
            return original
        with mock.patch.object(Path, "stat", separate_case_device):
            with self.assertRaises(sl.GuardError): self.lc.admit_backups("a", [self.pin(path)])

    def test_regenerable_retained_copy_must_match_actual_whole_bytes(self):
        different = self.folder / "not-the-same-copy"; different.write_bytes(b"different")
        self.files[1]["retained_copy"] = self.pin(different)
        with self.assertRaises(sl.GuardError): self.register()

    def test_regenerable_retained_copy_is_reverified_before_cleanup(self):
        self.files[1]["retained_copy"] = self.pin(self.folder / "raw.bin")
        self.archive(); self.admit(); (self.folder / "raw.bin").write_bytes(b"x" * len(self.data))
        with self.assertRaises(sl.GuardError): self.lc.cleanup_case("a")
        self.assertTrue((self.folder / "derived.bin").exists())


if __name__ == "__main__": unittest.main()
