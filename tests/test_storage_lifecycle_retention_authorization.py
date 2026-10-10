"""Owned 13-file dummy cases only; no Model, network, provider or formal raw."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

import test_storage_lifecycle as fixtures
sl = fixtures.sl


class ExplicitRetentionTests(unittest.TestCase):
    write_json = staticmethod(fixtures.StorageLifecycleTests.write_json)
    pin = staticmethod(fixtures.StorageLifecycleTests.pin)
    row = fixtures.StorageLifecycleTests.row
    register = fixtures.StorageLifecycleTests.register
    archive = fixtures.StorageLifecycleTests.archive
    admit = fixtures.StorageLifecycleTests.admit
    promotion_pin = fixtures.StorageLifecycleTests.promotion_pin

    def setUp(self):
        fixtures.StorageLifecycleTests.setUp(self)
        self.policy = replace(self.policy, retention_seconds=86400.0)
        self.lc = sl.Lifecycle(self.cases, self.vault, self.policy, gate_roots=[self.proofs])
        (self.folder / "derived.bin").unlink()  # dummy setup file, never a formal artifact
        self.expected = {"raw.bin": self.data}
        for i in range(1, 13):
            name = "case-%02d.bin" % i
            data = ("fixture-file-%02d\n" % i).encode() * (31 + i)
            (self.folder / name).write_bytes(data); self.expected[name] = data
        self.files = [self.row(name, "original") for name in self.expected]
        self.archive()
        _, self.replicas, _ = self.admit(1)
        self.lc.promote_archive("a", self.promotion_pin())
        self.dest = self.vault / "cases/a"

    def tearDown(self): fixtures.StorageLifecycleTests.tearDown(self)

    def authorization(self, **changes):
        state = json.loads((self.dest / "state.json").read_bytes())
        now = datetime.now(timezone.utc)
        value = {"schema": 1, "issuer": "root-case-retention-authorization-v1",
                 "authorization_id": "fixture-retention-a", "scope": "shorten-operator-retention-only",
                 "case_id": "a", "definition_sha256": self.pin(self.dest / "definition.json")["sha256"],
                 "archive_sha256": self.pin(self.dest / "archive.json")["sha256"],
                 "policy_sha256": hashlib.sha256(sl._json(asdict(self.policy))).hexdigest(),
                 "registered_epoch": state["registered_epoch"], "issued_utc": now.isoformat(),
                 "not_before_utc": now.isoformat(), "expires_utc": (now + timedelta(hours=1)).isoformat(),
                 "retention_seconds": 0.0, "active_writer_count": 0, "hot_parent_dependency_count": 0,
                 "restore_recipe": "storage-lifecycle-restore-case-v1",
                 "reason": "Explicit dummy-case operator cleanup, not scientific evidence.", **changes}
        path = self.proofs / "retention.json"; self.write_json(path, value)
        return self.pin(path)

    def assert_raw_intact(self):
        for name, raw in self.expected.items(): self.assertEqual((self.folder / name).read_bytes(), raw)

    def reject(self, **changes):
        before = self.pin(self.dest / "definition.json")["sha256"]
        with self.assertRaises((sl.GuardError, OSError)):
            self.lc.evict_closed_raw("a", retention_authorization_pin=self.authorization(**changes))
        self.assert_raw_intact()
        self.assertEqual(before, self.pin(self.dest / "definition.json")["sha256"])

    def test_absent_authorization_keeps_original_ttl_and_daemon_default(self):
        self.assertEqual(self.lc.evict_closed_raw("a")["status"], "RETAINED_TTL")
        result = self.lc.maintain_once(["a"])
        self.assertTrue(all(r["status"] == "RETAINED_TTL" for r in result["cases"]))
        self.assert_raw_intact()

    def test_valid_whole13_transaction_and_both_backend_hydration(self):
        before = {n: self.pin(self.dest / n) for n in ("definition.json", "archive.json")}
        epoch = json.loads((self.dest / "state.json").read_bytes())["registered_epoch"]
        permit = self.authorization()
        result = self.lc.evict_closed_raw("a", retention_authorization_pin=permit)
        self.assertEqual(result["raw_paths_evicted"], 13)
        self.assertFalse(result["scientific_content_deleted"])
        self.assertEqual(result["new_scientific_samples"], 0)
        self.assertEqual(result["explicit_root_retention_authorization"]["authorization_sha256"], permit["sha256"])
        self.assertTrue(all(not (self.folder / n).exists() for n in self.expected))
        state = json.loads((self.dest / "state.json").read_bytes())
        self.assertEqual(state["registered_epoch"], epoch)
        self.assertEqual(len(state["cleanup_transactions"]), 13)
        self.assertTrue(all(t["kind"] == "promoted-original" and t["phase"] == "deleted" for t in state["cleanup_transactions"]))
        for n in before: self.assertEqual(self.pin(self.dest / n), before[n])
        for backend in (None, "remote-0"):
            output = self.root / ("primary-hydration" if backend is None else "remote-hydration")
            restored = self.lc.hydrate_case("a", output, backend_id=backend)
            self.assertEqual(restored["files"], 13)
            for name, raw in self.expected.items(): self.assertEqual((output / name).read_bytes(), raw)
        self.assertGreaterEqual(self.replicas[0].reads.count("manifest"), 3)
        self.assertIsNone(self.lc._local.retention_authorization)
        self.assertEqual(self.lc.evict_closed_raw("a")["status"], "RETAINED_TTL")

    def test_mismatched_external_pin_refuses(self):
        p = self.authorization(); p["sha256"] = "0" * 64
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a", retention_authorization_pin=p)
        self.assert_raw_intact()

    def test_empty_pin_refuses(self):
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a", retention_authorization_pin={})
        self.assert_raw_intact()

    def test_oversized_authorization_pin_refuses_before_hash_read(self):
        p=self.authorization(); p["bytes"]=sl.MAX_METADATA+1
        actual=self.lc._pin; authorization_reads=[]
        def pin(value):
            if value is p: authorization_reads.append(value)
            return actual(value)
        with mock.patch.object(self.lc,"_pin",side_effect=pin), self.assertRaises(sl.GuardError):
            self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assertEqual(authorization_reads,[]); self.assert_raw_intact()

    def test_expired_refuses(self): self.reject(expires_utc=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    def test_future_issue_refuses(self): self.reject(issued_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    def test_future_not_before_refuses(self): self.reject(not_before_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    def test_naive_time_refuses(self): self.reject(issued_utc=datetime.now().isoformat())
    def test_wrong_case_refuses(self): self.reject(case_id="b")
    def test_wrong_archive_refuses(self): self.reject(archive_sha256="0" * 64)
    def test_wrong_policy_refuses(self): self.reject(policy_sha256="0" * 64)
    def test_wrong_definition_refuses(self): self.reject(definition_sha256="0" * 64)
    def test_wrong_registration_refuses(self): self.reject(registered_epoch=0.0)
    def test_wrong_issuer_refuses(self): self.reject(issuer="ordinary-case-metadata")
    def test_wrong_scope_refuses(self): self.reject(scope="waive-scientific-gates")
    def test_wrong_recipe_refuses(self): self.reject(restore_recipe="not-a-hydration-recipe")
    def test_writer_predicate_refuses(self): self.reject(active_writer_count=1)
    def test_hot_dependency_predicate_refuses(self): self.reject(hot_parent_dependency_count=1)
    def test_boolean_zero_predicate_refuses(self): self.reject(active_writer_count=False)
    def test_boolean_ttl_refuses(self): self.reject(retention_seconds=False)
    def test_nan_ttl_refuses(self): self.reject(retention_seconds=float("nan"))
    def test_negative_ttl_refuses(self): self.reject(retention_seconds=-1.0)
    def test_equal_or_extended_ttl_refuses(self):
        for ttl in (86400.0, 86401.0):
            with self.subTest(ttl=ttl): self.reject(retention_seconds=ttl)
    def test_missing_field_refuses(self):
        p = self.authorization(); value=json.loads(Path(p["path"]).read_bytes()); value.pop("expires_utc")
        self.write_json(Path(p["path"]), value)
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a", retention_authorization_pin=self.pin(Path(p["path"])))
        self.assert_raw_intact()

    def test_positive_short_ttl_still_retains_young_case(self):
        result = self.lc.evict_closed_raw("a", retention_authorization_pin=self.authorization(retention_seconds=3600.0))
        self.assertEqual(result["status"], "RETAINED_TTL"); self.assert_raw_intact()

    def test_missing_promotion_refuses(self):
        (self.dest / "promotion.json").unlink()
        with self.assertRaises((sl.GuardError,OSError)): self.lc.evict_closed_raw("a",retention_authorization_pin=self.authorization())
        self.assert_raw_intact()
    def test_missing_backup_refuses(self):
        (self.dest / "backups.json").unlink()
        with self.assertRaises((sl.GuardError,OSError)): self.lc.evict_closed_raw("a",retention_authorization_pin=self.authorization())
        self.assert_raw_intact()
    def test_failed_second_domain_refuses(self):
        self.replicas[0].failure_domain = self.policy.primary_failure_domain
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=self.authorization())
        self.assert_raw_intact()
    def test_actual_corrupt_replica_readback_refuses(self):
        b=self.replicas[0]; b.objects[next(iter(b.objects))]=b"corrupt fixture replica"
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=self.authorization())
        self.assert_raw_intact()
    def test_live_writer_lease_refuses(self):
        p=self.authorization()
        with self.lc.writer_lease("a"), self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_hot_reader_lease_refuses(self):
        p=self.authorization()
        with self.lc.reader_lease("a"), self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_changed_source_gate_refuses(self):
        (self.proofs/"source.py").write_bytes(b"different source fixture")
        with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=self.authorization())
        self.assert_raw_intact()

    def replace_same_bytes(self, p):
        other=p.with_name(p.name+".replacement"); other.write_bytes(p.read_bytes()); os.replace(other,p)

    def intercept_first_plan(self, effect):
        original=self.lc._write; done=False
        def write(path,value,**kw):
            nonlocal done
            result=original(path,value,**kw)
            if not done and path.name=="state.json" and value.get("cleanup_transactions"):
                done=True; effect()
            return result
        return mock.patch.object(self.lc,"_write",side_effect=write)

    def test_authorization_identity_swap_after_plan_refuses(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:self.replace_same_bytes(Path(p["path"]))):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_definition_identity_swap_after_plan_refuses(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:self.replace_same_bytes(self.dest/"definition.json")):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_archive_identity_swap_after_plan_refuses(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:self.replace_same_bytes(self.dest/"archive.json")):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_raw_identity_swap_after_plan_refuses(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:self.replace_same_bytes(self.folder/"raw.bin")):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_authorization_content_mutation_after_plan_refuses(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:Path(p["path"]).write_bytes(b"different permission")):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
    def test_real_expiry_rechecked_after_plan_before_any_rename(self):
        p=self.authorization(expires_utc=(datetime.now(timezone.utc)+timedelta(seconds=0.5)).isoformat())
        with self.intercept_first_plan(lambda:time.sleep(0.55)):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        self.assert_raw_intact()
        self.assertFalse(any((self.vault/"quarantine").iterdir()))
    def test_fresh_permission_resumes_existing_planned_transaction(self):
        p=self.authorization()
        with self.intercept_first_plan(lambda:self.replace_same_bytes(Path(p["path"]))):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        fresh=self.authorization(authorization_id="fixture-second-admission")
        result=self.lc.evict_closed_raw("a",retention_authorization_pin=fresh)
        self.assertEqual(result["raw_paths_evicted"],13)
        self.assertEqual(len(json.loads((self.dest/"state.json").read_bytes())["cleanup_transactions"]),13)

    def intercept_phase(self, condition):
        original=self.lc._write; waited=False
        def write(path,value,**kw):
            nonlocal waited
            result=original(path,value,**kw)
            if not waited and path.name=="state.json" and condition(value.get("cleanup_transactions",[])):
                waited=True; time.sleep(0.55)
            return result
        return mock.patch.object(self.lc,"_write",side_effect=write)

    def test_expiry_after_quarantine_keeps_bytes_and_fresh_permission_resumes(self):
        p=self.authorization(expires_utc=(datetime.now(timezone.utc)+timedelta(seconds=0.5)).isoformat())
        with self.intercept_phase(lambda txns: bool(txns) and txns[0]["phase"]=="quarantined"):
            with self.assertRaises(sl.GuardError): self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        state=json.loads((self.dest/"state.json").read_bytes());first=state["cleanup_transactions"][0]
        self.assertEqual(first["phase"],"quarantined")
        self.assertEqual((self.vault/"quarantine"/first["quarantine_name"]).read_bytes(),self.expected[first["relative"]])
        self.assertFalse((self.folder/first["relative"]).exists())
        self.assertEqual(first["retention_authorizations"][0]["authorization_sha256"],p["sha256"])
        fresh=self.authorization(authorization_id="after-quarantine")
        self.assertEqual(self.lc.evict_closed_raw("a",retention_authorization_pin=fresh)["raw_paths_evicted"],13)
        state=json.loads((self.dest/"state.json").read_bytes());history=state["cleanup_transactions"][0]["retention_authorizations"]
        self.assertEqual([a["authorization_sha256"] for a in history],[p["sha256"],fresh["sha256"]])
        output=self.root/"after-quarantine-hydration";self.lc.hydrate_case("a",output)
        for name,raw in self.expected.items():self.assertEqual((output/name).read_bytes(),raw)

    def test_expiry_after_first_unlink_retains_per_transaction_authority(self):
        p=self.authorization(expires_utc=(datetime.now(timezone.utc)+timedelta(seconds=0.5)).isoformat())
        with self.intercept_phase(lambda ts:len(ts)==2 and ts[0]["phase"]=="deleted" and ts[1]["phase"]=="planned"):
            with self.assertRaises(sl.GuardError):self.lc.evict_closed_raw("a",retention_authorization_pin=p)
        state=json.loads((self.dest/"state.json").read_bytes());first=state["cleanup_transactions"][0]
        self.assertEqual(first["phase"],"deleted")
        self.assertEqual(first["retention_authorizations"][0]["authorization_sha256"],p["sha256"])
        self.assertFalse(any(a.get("operation")=="promoted-raw-physical-eviction" for a in state["audit"]))
        fresh=self.authorization(authorization_id="after-first-unlink")
        self.assertEqual(self.lc.evict_closed_raw("a",retention_authorization_pin=fresh)["raw_paths_evicted"],12)
        state=json.loads((self.dest/"state.json").read_bytes())
        self.assertEqual(state["cleanup_transactions"][0]["retention_authorizations"][0]["authorization_sha256"],p["sha256"])
        self.assertEqual(len(state["cleanup_transactions"][0]["retention_authorizations"]),1)
        self.assertEqual([a["authorization_sha256"] for a in state["cleanup_transactions"][1]["retention_authorizations"]],[p["sha256"],fresh["sha256"]])
        output=self.root/"after-unlink-hydration";self.lc.hydrate_case("a",output,backend_id="remote-0")
        for name,raw in self.expected.items():self.assertEqual((output/name).read_bytes(),raw)

    def test_positive_elapsed_ttl_rechecked_on_actual_clock_at_destructive_steps(self):
        epoch=json.loads((self.dest/"state.json").read_bytes())["registered_epoch"]
        p=self.authorization(retention_seconds=time.time()-epoch+1.0)
        self.assertEqual(self.lc.evict_closed_raw("a",retention_authorization_pin=p)["status"],"RETAINED_TTL")
        time.sleep(1.05)
        actual=self.lc._check_retention_authorization;destructive_checks=[]
        def check(**kw):
            if kw.get("require_ttl_elapsed"):destructive_checks.append(time.time())
            return actual(**kw)
        with mock.patch.object(self.lc,"_check_retention_authorization",side_effect=check):
            self.assertEqual(self.lc.evict_closed_raw("a",retention_authorization_pin=p)["raw_paths_evicted"],13)
        self.assertGreaterEqual(len(destructive_checks),39)


if __name__ == "__main__": unittest.main()
