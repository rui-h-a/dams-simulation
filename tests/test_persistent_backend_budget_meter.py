"""Tiny offline durability, immutable-budget, and HTTP fencing counterexamples."""
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from research_tools.persistent_backend_budget_meter import (
    CONFIG_SCHEMA, EVENT_SCHEMA, Meter, Refusal,
)


# Synthetic test allocation; these are not actual research-account details.
TASK_ID = "synthetic-http-probe"
GLOBAL_DEADLINE = "2030-01-06T12:00:00Z"

def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class MeterTests(unittest.TestCase):
    def setUp(self):
        # All owned fixtures are tiny and on the same external source volume.
        self.temp = tempfile.TemporaryDirectory(prefix="meter-fixture-", dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.now = datetime(2030, 1, 1, 12, tzinfo=timezone.utc).timestamp()
        self.clock = lambda: self.now
        self.old = {
            "cumulative_cap_usd": "20", "global_deadline_utc": GLOBAL_DEADLINE,
            "authorization_id": "original-root-test", "no_optimistic_refunds": True,
            "tasks": {"old-hold": {"reserved_cost_usd_upper": "17.00", "state": "not-refunded"}},
            "history": [{"original": True}], "actual_reconciled_spend_usd": 0,
        }
        self.limits = {
            "max_archive_requests": 128, "max_management_requests": 8,
            "authentication_requests_margin": 4, "max_uploaded_body_bytes": 8388608,
            "max_downloaded_body_bytes": 67108864, "max_retained_encoded_bytes": 8388608,
        }
        self.prices = {
            "class_a_usd_per_1000_upper": "0.01", "class_b_usd_per_1000_upper": "0.001",
            "standard_storage_usd_per_gib_hour_upper": "0.00005",
            "outgoing_usd_per_gib_upper": "0.20", "unknown_transport_and_cleanup_margin_usd": "0.04",
        }
        self.task = {
            "reserved_cost_usd_upper": "0.25", "reserved_cost_usd_upper_not_refunded": "0.25",
            "prior_total_envelopes_and_holds_usd": "17.00", "scientific_samples": 0,
            "new_compute": False, "production_preservation_admitted": False,
            "original_holds_retained": True, "no_optimistic_refund": True,
            "global_deadline_utc": GLOBAL_DEADLINE, "reserved_utc": "2030-01-01T11:49:00+00:00",
            **self.limits, **self.prices,
        }
        self.current = copy.deepcopy(self.old)
        self.current["tasks"][TASK_ID] = self.task
        self.current["history"].append({"root_reserved": "0.25"})
        self.old_ref = self.file("original.json", self.old)
        self.current_ref = self.file("current.json", self.current)
        self.source_ref = self.file("module.txt", b"fixed engineering source, no secret\n")
        self.manifest_ref = self.file("source-manifest.json", {"files": [self.source_ref]})
        self.job_ref = self.file("job.json", {"schema": "root-owned-probe-test", "science": False})
        self.state = self.root / "journal"
        self.state.mkdir(mode=0o700)
        anchor = self.state.stat()
        self.object = "/storage/v1/b/fixed-private-bucket/o/fixed%2Fobject"
        self.config = {
            "schema": CONFIG_SCHEMA, "task_id": TASK_ID, "state_dir": str(self.root / "journal"),
            "state_anchor": {"dev": anchor.st_dev, "ino": anchor.st_ino, "initial_ctime_ns": anchor.st_ctime_ns},
            "created_at_utc": "2030-01-01T12:00:00Z", "global_deadline_utc": GLOBAL_DEADLINE,
            "cumulative_cap_usd": "20", "baseline_held_usd": "17.00", "suballocation_usd": "0.25",
            "original_budget": self.old_ref, "current_budget": self.current_ref,
            "source_manifest": self.manifest_ref, "source_job": self.job_ref,
            "source_files": [self.source_ref], "limits": dict(self.limits), "prices": dict(self.prices),
            "per_request_upload_bytes": 8388608, "per_request_download_bytes": 67108864,
            "allowed_paths": {"gcs-private": {
                "GET": ["/storage/v1/b/fixed-private-bucket", self.object],
                "POST": ["/upload/storage/v1/b/fixed-private-bucket/o?uploadType=media&name=fixed%2Fobject&ifGenerationMatch=0"],
                "DELETE": ["/storage/v1/b/fixed-private-bucket"],
            }},
            "generation_media_paths": {"gcs-private": [self.object]},
            "generation_delete_paths": {"gcs-private": [self.object]},
        }
        self.config_path = self.root / "meter.json"

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, value):
        raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
        path = self.root / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return {"path": str(path), "sha256": digest(raw)}

    def meter(self):
        ref = self.file("meter.json", self.config)
        return Meter(ref["path"], config_sha256=ref["sha256"], clock=self.clock)

    def reopen(self):
        return Meter(self.config_path, config_sha256=digest(self.config_path.read_bytes()), clock=self.clock)

    def event(self, method="GET", **changes):
        event = {"schema": EVENT_SCHEMA, "backend_id": "gcs-private", "method": method,
                 "path": self.config["allowed_paths"]["gcs-private"][method if method in ("GET", "POST", "DELETE") else "GET"][0],
                 "request_body_upper_bytes": 0, "response_body_limit_bytes": 10,
                 "request_body_sha256": None}
        event.update(changes)
        return event

    @staticmethod
    def result(**changes):
        result = {"status": 200, "response_body_bytes": 3, "normal_eof": True, "failed": False}
        result.update(changes)
        return result

    def test_ticket_is_fsynced_before_mock_network_and_result_is_paired(self):
        meter = self.meter()
        with mock.patch("research_tools.persistent_backend_budget_meter.os.fsync", wraps=os.fsync) as sync:
            ticket = meter.before(self.event())
            self.assertGreaterEqual(sync.call_count, 2)
        record = json.loads((self.root / "journal/tickets/00000001.json").read_bytes())
        self.assertEqual(ticket["ticket_sha256"], digest((self.root / "journal/tickets/00000001.json").read_bytes()))
        self.assertEqual(record["cumulative"]["downloaded_body_bytes_upper"], 10)
        meter.after(ticket, self.result())
        state = meter.snapshot()
        self.assertEqual((state["requests"], state["completed_attempts"], state["pending_attempts"]), (1, 1, 0))
        self.assertFalse(state["provider_invoice"])

    def test_pending_failed_retry_and_success_all_keep_full_upper_bounds(self):
        meter = self.meter()
        pending = meter.before(self.event())
        fail = meter.before(self.event())
        meter.after(fail, self.result(status=None, response_body_bytes=0, normal_eof=False, failed=True))
        retry = meter.before(self.event())
        meter.after(retry, self.result(response_body_bytes=0))
        state = meter.snapshot()
        self.assertEqual(state["downloaded_body_bytes_upper"], 30)
        self.assertEqual(state["pending_attempts"], 1)
        fresh = self.reopen()
        self.assertEqual(fresh.snapshot(), state)
        fresh.after(pending, self.result(status=503, response_body_bytes=0, failed=True))
        self.assertEqual(fresh.snapshot()["held_cost_usd_upper"], state["held_cost_usd_upper"])

    def test_duplicate_result_and_forged_ticket_refused(self):
        meter = self.meter()
        ticket = meter.before(self.event())
        forged = dict(ticket, ticket_sha256="0" * 64)
        with self.assertRaises(Refusal): meter.after(forged, self.result())
        meter.after(ticket, self.result())
        with self.assertRaises(Refusal): meter.after(ticket, self.result())

    def test_unknown_or_secret_event_fields_are_not_persisted(self):
        meter = self.meter()
        for field in ("Authorization", "headers", "token", "password", "request_body", "extra"):
            with self.subTest(field=field), self.assertRaises(Refusal):
                meter.before(self.event(**{field: "private-do-not-record"}))
        self.assertEqual(list((self.root / "journal/tickets").iterdir()), [])

    def test_required_event_fields_schema_and_typed_byte_bounds(self):
        meter = self.meter()
        for change in ({"schema": "wrong"}, {"request_body_upper_bytes": True},
                       {"response_body_limit_bytes": -1}, {"response_body_limit_bytes": 67108865},
                       {"request_body_upper_bytes": 1}, {"request_body_sha256": "secret"},
                       {"backend_id": "other"}, {"method": "PUT"}):
            with self.subTest(change=change), self.assertRaises(Refusal): meter.before(self.event(**change))
        missing = self.event(); del missing["path"]
        with self.assertRaises(Refusal): meter.before(missing)

    def test_optional_body_sha_none_and_nonempty_digest_rules(self):
        meter = self.meter()
        event = self.event(); del event["request_body_sha256"]
        meter.before(event)
        with self.assertRaises(Refusal):
            meter.before(self.event("POST", request_body_upper_bytes=1))
        meter.before(self.event("POST", request_body_upper_bytes=1, request_body_sha256=digest(b"x")))
        self.assertEqual(meter.snapshot()["uploaded_body_bytes_upper"], 1)

    def test_exact_paths_refuse_prefix_other_bucket_fragment_and_url(self):
        meter = self.meter()
        for path in (self.object + "/extra", "/storage/v1/b/other", "https://example.org/a",
                     "//example.org/a", self.object + "#secret", "/../a", "/a\\b", "/a token"):
            with self.subTest(path=path), self.assertRaises(Refusal): meter.before(self.event(path=path))

    def test_dynamic_media_allows_only_fixed_object_and_equal_generation(self):
        meter = self.meter()
        path = self.object + "?alt=media&generation=123&ifGenerationMatch=123"
        meter.before(self.event(path=path))
        for suffix in ("?alt=media&generation=123&ifGenerationMatch=124",
                       "?alt=media&generation=0&ifGenerationMatch=0", "?alt=media&generation=01&ifGenerationMatch=01",
                       "?generation=123&alt=media&ifGenerationMatch=123",
                       "?alt=media&generation=123&ifGenerationMatch=123&token=secret",
                       "?alt=media&generation=123&generation=123&ifGenerationMatch=123",
                       "?alt=media&generation=123", "?alt=media&generation=x&ifGenerationMatch=x"):
            with self.subTest(suffix=suffix), self.assertRaises(Refusal): meter.before(self.event(path=self.object + suffix))
        with self.assertRaises(Refusal): meter.before(self.event(path=path.replace("object", "other")))

    def test_generation_delete_own_exact_fence_not_media_or_missing_match(self):
        meter = self.meter()
        meter.before(self.event("DELETE", path=self.object + "?generation=123&ifGenerationMatch=123"))
        for suffix in ("?generation=123", "?generation=123&ifGenerationMatch=124",
                       "?alt=media&generation=123&ifGenerationMatch=123", "?generation=123&ifGenerationMatch=123&x=1"):
            with self.subTest(suffix=suffix), self.assertRaises(Refusal):
                meter.before(self.event("DELETE", path=self.object + suffix))

    def test_post_create_and_delete_use_class_a_get_class_b(self):
        meter = self.meter()
        get = meter.before(self.event(response_body_limit_bytes=0))
        post = meter.before(self.event("POST", response_body_limit_bytes=0))
        records = [json.loads((self.root / f"journal/tickets/{ticket['sequence']:08d}.json").read_bytes()) for ticket in (get, post)]
        from decimal import Decimal
        increase = Decimal(records[1]["cumulative"]["held_cost_usd_upper"]) - Decimal(records[0]["cumulative"]["held_cost_usd_upper"])
        self.assertEqual(increase, Decimal("0.00001"))

    def test_request_count_cap_survives_reopen_and_never_refunds_failure(self):
        self.config["limits"]["max_archive_requests"] = 1
        meter = self.meter()
        ticket = meter.before(self.event())
        meter.after(ticket, self.result(status=None, response_body_bytes=0, normal_eof=False, failed=True))
        with self.assertRaises(Refusal): self.reopen().before(self.event())
        self.assertEqual(self.reopen().snapshot()["requests"], 1)

    def test_download_cumulative_and_upload_caps_use_attempt_upper_bounds(self):
        self.config["limits"]["max_downloaded_body_bytes"] = 10
        self.config["per_request_download_bytes"] = 10
        self.config["limits"]["max_uploaded_body_bytes"] = 2
        self.config["per_request_upload_bytes"] = 2
        meter = self.meter()
        meter.before(self.event("POST", request_body_upper_bytes=2, request_body_sha256=digest(b"xx")))
        with self.assertRaises(Refusal): meter.before(self.event(response_body_limit_bytes=1))
        with self.assertRaises(Refusal):
            meter.before(self.event("POST", request_body_upper_bytes=1, response_body_limit_bytes=0, request_body_sha256=digest(b"x")))

    def test_conservative_decimal_cost_cap_no_optimistic_compression_discount(self):
        self.config["prices"]["outgoing_usd_per_gib_upper"] = "4.00"
        meter = self.meter()
        with self.assertRaises(Refusal): meter.before(self.event(response_body_limit_bytes=67108864))
        self.assertEqual(meter.snapshot()["requests"], 0)

    def test_expired_new_requests_refused_existing_result_can_close_without_refund(self):
        meter = self.meter()
        ticket = meter.before(self.event())
        cost = meter.snapshot()["held_cost_usd_upper"]
        self.now = meter.deadline + 1
        with self.assertRaises(Refusal): meter.before(self.event())
        meter.after(ticket, self.result())
        result = json.loads((self.root / "journal/results/00000001.json").read_bytes())
        self.assertTrue(result["closure_after_deadline"])
        self.assertEqual(meter.snapshot()["held_cost_usd_upper"], cost)

    def test_clock_rollback_and_preconfig_clock_refused(self):
        meter = self.meter()
        meter.before(self.event())
        self.now -= 1
        with self.assertRaises(Refusal): meter.before(self.event())
        with self.assertRaises(Refusal): self.reopen().snapshot()

    def test_result_boolean_status_non2xx_incomplete_oversize_and_secret_rejected(self):
        meter = self.meter()
        ticket = meter.before(self.event())
        for change in ({"status": True}, {"status": 503}, {"normal_eof": False},
                       {"response_body_bytes": 11}, {"response_body_bytes": True},
                       {"status": None}, {"token": "secret"}):
            with self.subTest(change=change), self.assertRaises(Refusal): meter.after(ticket, self.result(**change))
        self.assertEqual(meter.snapshot()["pending_attempts"], 1)

    def test_budget_bytes_drift_prevents_before_and_after_without_ledger_mutation(self):
        meter = self.meter()
        ticket = meter.before(self.event())
        Path(self.current_ref["path"]).write_bytes(b"drift\n")
        with self.assertRaises(Refusal): meter.before(self.event())
        with self.assertRaises(Refusal): meter.after(ticket, self.result())
        self.assertFalse((self.root / "journal/results/00000001.json").exists())

    def test_source_job_and_source_bytes_or_identity_drift_refused(self):
        meter = self.meter()
        target = Path(self.source_ref["path"])
        raw = target.read_bytes()
        target.unlink(); target.write_bytes(raw); target.chmod(0o600)
        with self.assertRaises(Refusal): meter.before(self.event())
        # Same-path, same-SHA replacement must not bypass the pinned open identity.

    def test_config_sha_mode_or_native_identity_drift_refused(self):
        meter = self.meter()
        self.config_path.chmod(0o644)
        with self.assertRaises(Refusal): meter.before(self.event())
        with self.assertRaises(Refusal): self.reopen()

    def test_original_hold_reset_history_rewrite_or_authorization_change_refused(self):
        for mutation in ("hold", "history", "authorization", "invoice"):
            with self.subTest(mutation=mutation):
                current = copy.deepcopy(self.current)
                if mutation == "hold": current["tasks"]["old-hold"]["reserved_cost_usd_upper"] = "0"
                elif mutation == "history": current["history"][0] = {"reset": True}
                elif mutation == "authorization": current["authorization_id"] = "other"
                else: current["actual_reconciled_spend_usd"] = 1
                self.config["current_budget"] = self.file("current.json", current)
                with self.assertRaises(Refusal): self.meter()

    def test_no_unilateral_allocation_science_or_compute_flags(self):
        for field, value in (("scientific_samples", 1), ("scientific_samples", False), ("new_compute", True),
                             ("production_preservation_admitted", True), ("reserved_cost_usd_upper_not_refunded", "0")):
            with self.subTest(field=field, value=value):
                current = copy.deepcopy(self.current); current["tasks"][TASK_ID][field] = value
                self.config["current_budget"] = self.file("current.json", current)
                with self.assertRaises(Refusal): self.meter()

    def test_secret_config_unknown_fields_nonfinite_price_and_lower_rates_refused(self):
        for field, value in (("token", "secret"), ("cumulative_cap_usd", "101"),
                             ("global_deadline_utc", "2030-01-07T00:00:00Z")):
            config = copy.deepcopy(self.config); config[field] = value
            old = self.config; self.config = config
            with self.assertRaises(Refusal): self.meter()
            self.config = old
        for value in ("NaN", "Infinity", "0.01", 0.12):
            self.config["prices"]["outgoing_usd_per_gib_upper"] = value
            with self.assertRaises(Refusal): self.meter()

    def test_symlink_input_and_journal_directory_refused_without_outside_write(self):
        target = Path(self.source_ref["path"])
        alias = self.root / "alias-source"
        alias.symlink_to(target)
        self.config["source_files"] = [{"path": str(alias), "sha256": self.source_ref["sha256"]}]
        with self.assertRaises(Refusal): self.meter()
        self.config["source_files"] = [self.source_ref]
        meter = self.meter()
        outside = self.root / "outside"; outside.mkdir()
        (self.root / "journal/tickets").rmdir()
        (self.root / "journal/tickets").symlink_to(outside)
        with self.assertRaises(Refusal): meter.before(self.event())
        self.assertEqual(list(outside.iterdir()), [])

    def test_lock_contention_refuses_second_accounting_actor(self):
        meter = self.meter()
        fd = os.open(self.root / "journal/lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(Refusal): meter.before(self.event())
        finally:
            os.close(fd)

    def test_partial_ticket_sequence_gap_and_counter_tamper_fail_closed(self):
        meter = self.meter()
        meter.before(self.event()); meter.before(self.event())
        path = self.root / "journal/tickets/00000002.json"
        raw = path.read_bytes(); path.write_bytes(raw[:20])
        with self.assertRaises(Refusal): self.reopen()
        path.write_bytes(raw)
        first = self.root / "journal/tickets/00000001.json"
        original = first.read_bytes(); record = json.loads(original)
        record["cumulative"]["held_cost_usd_upper"] = "0"
        first.write_bytes((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
        with self.assertRaises(Refusal): self.reopen()
        first.write_bytes(original); first.unlink()
        with self.assertRaises(Refusal): self.reopen()

    def test_missing_binding_never_resets_pending_tickets(self):
        meter = self.meter(); meter.before(self.event())
        (self.root / "journal/binding.json").unlink()
        with self.assertRaises(Refusal): self.reopen()
        self.assertTrue((self.root / "journal/tickets/00000001.json").exists())

    def test_fsync_failure_never_returns_network_ticket_and_preserves_written_hold(self):
        meter = self.meter()
        with mock.patch("research_tools.persistent_backend_budget_meter.os.fsync", side_effect=OSError("fixture disk")):
            with self.assertRaises(Refusal): meter.before(self.event())
        # A crash/failed fsync between journal and head must refuse, never reset.
        with self.assertRaises(Refusal): self.reopen()
        self.assertTrue((self.root / "journal/tickets/00000001.json").exists())

    def test_duplicate_json_key_config_refused(self):
        raw = json.dumps(self.config).encode()
        raw = raw[:-1] + b', "task_id": "duplicate"}'
        self.config_path.write_bytes(raw); self.config_path.chmod(0o600)
        with self.assertRaises(Refusal): Meter(self.config_path, config_sha256=digest(raw), clock=self.clock)

    def test_secret_query_cannot_be_admitted_even_in_root_path_roster(self):
        self.config["allowed_paths"]["gcs-private"]["GET"].append(self.object + "?access_token=secret")
        with self.assertRaises(Refusal): self.meter()

    def test_malformed_path_config_and_query_are_explicit_refusals(self):
        self.config["allowed_paths"]["gcs-private"]["GET"].append({"path": "wrong-type"})
        with self.assertRaises(Refusal): self.meter()
        self.config["allowed_paths"]["gcs-private"]["GET"].pop()
        meter = self.meter()
        with self.assertRaises(Refusal): meter.before(self.event(path=self.object + "?alt"))

    def test_job_metadata_drift_and_missing_task_refused(self):
        meter = self.meter()
        Path(self.job_ref["path"]).write_bytes(b"revised probe")
        with self.assertRaises(Refusal): meter.before(self.event())
        current = copy.deepcopy(self.current); del current["tasks"][TASK_ID]
        self.config["current_budget"] = self.file("current.json", current)
        with self.assertRaises(Refusal): self.meter()

    def test_last_pending_ticket_truncation_never_regrants_sequence_one(self):
        self.config["limits"]["max_archive_requests"] = 1
        meter = self.meter()
        meter.before(self.event())
        (self.state / "tickets/00000001.json").unlink()
        with self.assertRaises(Refusal): self.reopen()
        self.assertEqual(json.loads((self.state / "head.json").read_bytes())["ticket_count"], 1)

    def test_durable_expiry_refusal_survives_process_reopen_and_clock_rollback(self):
        meter = self.meter()
        meter.before(self.event())
        self.now = meter.deadline + 1
        with self.assertRaises(Refusal): meter.before(self.event())
        self.assertTrue(json.loads((self.state / "head.json").read_bytes())["deadline_observed"])
        self.now = meter.created + 1
        with self.assertRaises(Refusal): self.reopen().before(self.event())

    def test_durable_observed_time_refuses_rollback_without_waiting_for_expiry(self):
        meter = self.meter()
        self.now += 10; meter.snapshot()
        self.now -= 5
        with self.assertRaises(Refusal): self.reopen().before(self.event())

    def test_complete_state_directory_reset_refused_by_root_native_anchor(self):
        meter = self.meter(); meter.before(self.event())
        self.state.rename(self.root / "retained-old-journal")
        self.state.mkdir(mode=0o700)
        with self.assertRaises(Refusal): self.reopen()
        self.assertTrue((self.root / "retained-old-journal/tickets/00000001.json").exists())

    def test_closed_result_tail_deletion_and_head_deletion_are_not_reset(self):
        meter = self.meter(); ticket = meter.before(self.event()); meter.after(ticket, self.result())
        path = self.state / "results/00000001.json"; raw = path.read_bytes(); path.unlink()
        with self.assertRaises(Refusal): self.reopen()
        path.write_bytes(raw); path.chmod(0o600)
        (self.state / "head.json").unlink()
        with self.assertRaises(Refusal): self.reopen()

    def test_same_inode_complete_metadata_empty_reset_cannot_regrant_sequence_one(self):
        self.config["limits"]["max_archive_requests"] = 1
        meter = self.meter(); meter.before(self.event())
        original = self.state.stat()
        for path in self.state.iterdir():
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
        current = self.state.stat()
        self.assertEqual((original.st_dev, original.st_ino), (current.st_dev, current.st_ino))
        self.assertNotEqual(current.st_ctime_ns, self.config["state_anchor"]["initial_ctime_ns"])
        with self.assertRaises(Refusal): self.reopen()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_initial_ctime_anchor_required_and_incomplete_nonempty_state_not_reinitialized(self):
        self.config["state_anchor"]["initial_ctime_ns"] -= 1
        with self.assertRaises(Refusal): self.meter()
        self.config["state_anchor"]["initial_ctime_ns"] += 1
        meter = self.meter()
        (self.state / "binding.json").unlink()
        with self.assertRaises(Refusal): self.reopen()
        self.assertFalse((self.state / "binding.json").exists())


if __name__ == "__main__":
    unittest.main()
