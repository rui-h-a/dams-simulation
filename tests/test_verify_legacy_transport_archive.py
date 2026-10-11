"""Synthetic transport counterexamples only; no Model, network or formal raw."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research_tools"))
import verify_legacy_transport_archive as verifier


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class LegacyTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="legacy-tar-fixture-")
        self.root = Path(self.tmp.name).resolve()
        self.descriptor_root = self.root / "descriptors"
        self.descriptor_root.mkdir()
        self.tar = self.root / "fixture.tar"
        self.index_path = self.root / "archive-index.json"
        self.members = {}
        self.descriptors = {}
        files = []
        raw_pins = []
        self.raw_sum = 0
        for i in range(13):
            name = f"synthetic-{i:02d}.bin"
            if i == 0:
                raw = b"A" * verifier.codec.CHUNK_BYTES + b"different-last-chunk\x00\xff"
            elif i in (1, 2):
                raw = b"shared encoded chunk\x00" * 100
            else:
                raw = (f"synthetic-file-{i}\n".encode() + b"\x00\xff") * (50 + i)
            chunks = []
            for offset in range(0, len(raw), verifier.codec.CHUNK_BYTES):
                part = raw[offset:offset + verifier.codec.CHUNK_BYTES]
                encoded = zlib.compress(part, 1)
                chunk = {"offset": offset, "raw_bytes": len(part), "raw_sha256": verifier.sha(part),
                         "encoded_bytes": len(encoded), "encoded_sha256": verifier.sha(encoded)}
                chunks.append(chunk)
                self.members["encoded/" + chunk["encoded_sha256"]] = encoded
            descriptor = {"schema": 1, "codec": verifier.codec.CODEC,
                          "chunk_bytes": verifier.codec.CHUNK_BYTES, "raw_bytes": len(raw),
                          "raw_sha256": verifier.sha(raw), "encoded_bytes": sum(c["encoded_bytes"] for c in chunks),
                          "chunks": chunks}
            self.descriptors[name] = descriptor
            files.append({"file": name, "bytes": len(raw), "raw_sha256": verifier.sha(raw),
                          "descriptor": "descriptors/" + name + ".json",
                          "descriptor_sha256": verifier.sha(json_bytes(descriptor)),
                          "encoded_bytes": descriptor["encoded_bytes"], "chunk_count": len(chunks)})
            raw_pins.append({"file": name, "bytes": len(raw), "raw_sha256": verifier.sha(raw)})
            self.raw_sum += len(raw)
        source_pins = {}
        for name in ("cloud_archive.py", "cloud_control.py"):
            raw = (Path(verifier.codec.__file__).parent / name).read_bytes()
            self.members["source/" + name] = raw
            source_pins[name] = verifier.sha(raw)
        gate_pins = {}
        for name in verifier.GATE_FILES:
            raw = json_bytes({"fixture": name})
            self.members["evidence/" + name] = raw
            gate_pins[name] = verifier.sha(raw)
        self.case_id = "a" * 64
        self.members["evidence/admission.json"] = json_bytes({"case_id": self.case_id, "synthetic": True})
        self.index = {"schema": verifier.INDEX_SCHEMA, "case_id": self.case_id,
                      "codec": verifier.codec.CODEC, "chunk_fetch_rule": "encoded/<encoded_sha256>",
                      "files": files, "original_file_bindings": raw_pins,
                      "original_all_logical_bytes": self.raw_sum,
                      "codec_source_pins": source_pins, "original_gate_pins": gate_pins}
        self.sync_metadata()
        self.build_tar()

    def tearDown(self):
        self.tmp.cleanup()

    def sync_metadata(self):
        catalog = {}
        for item in self.index["files"]:
            descriptor = self.descriptors[item["file"]]
            raw = json_bytes(descriptor)
            (self.descriptor_root / (item["file"] + ".json")).write_bytes(raw)
            self.members[item["descriptor"]] = raw
            item["descriptor_sha256"] = verifier.sha(raw)
            item["bytes"] = descriptor["raw_bytes"]
            item["raw_sha256"] = descriptor["raw_sha256"]
            item["encoded_bytes"] = descriptor["encoded_bytes"]
            item["chunk_count"] = len(descriptor["chunks"])
            for chunk in descriptor["chunks"]:
                key = chunk["encoded_sha256"]
                if key not in catalog:
                    catalog[key] = {"shape": {field: chunk[field] for field in
                                              ("encoded_bytes", "raw_bytes", "raw_sha256")},
                                    "logical_occurrences": 0}
                catalog[key]["logical_occurrences"] += 1
        self.index["unique_encoded_chunks"] = len(catalog)
        self.index["unique_encoded_bytes"] = sum(item["shape"]["encoded_bytes"] for item in catalog.values())
        self.members["encoded-chunk-catalog.json"] = json_bytes(catalog)
        raw = json_bytes(self.index)
        self.index_path.write_bytes(raw)
        self.members["archive-index.json"] = raw

    def build_tar(self, *, extra=None, nonregular=None):
        with tarfile.open(self.tar, "w", format=tarfile.PAX_FORMAT) as output:
            rows = sorted(self.members.items()) + (extra or [])
            for name, raw in rows:
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                member.mode = 0o600
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = ""
                if nonregular is not None and name == nonregular[0]:
                    member.type = nonregular[1]
                    member.linkname = "encoded/escape" if member.type in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
                    member.size = 0
                    output.addfile(member)
                else:
                    output.addfile(member, io.BytesIO(raw))

    def run_verify(self, **changes):
        params = {"tar_path": self.tar, "index_path": self.index_path,
                  "descriptor_root": self.descriptor_root,
                  "expected_tar_sha256": verifier.sha(self.tar.read_bytes()),
                  "expected_tar_bytes": self.tar.stat().st_size}
        params.update(changes)
        return verifier.verify(**params)

    def reject(self, text=None, **changes):
        with self.assertRaises(verifier.GuardError) as caught:
            self.run_verify(**changes)
        if text:
            self.assertIn(text, str(caught.exception))

    def encoded_name(self):
        return next(name for name in self.members if name.startswith("encoded/"))

    def first_descriptor(self):
        return self.descriptors[self.index["files"][0]["file"]]

    def test_complete13_sink_verifies_every_reference_and_never_extracts(self):
        before = {p.relative_to(self.root): (p.stat().st_size, verifier.sha(p.read_bytes()))
                  for p in self.root.rglob("*") if p.is_file()}
        with mock.patch.object(verifier.codec, "decode_chunk", wraps=verifier.codec.decode_chunk) as decode:
            proof = self.run_verify()
        self.assertEqual(proof["status"], "PASS")
        self.assertEqual(proof["raw_files"], 13)
        self.assertEqual(proof["raw_bytes"], self.raw_sum)
        self.assertEqual(proof["chunk_references"], 14)
        self.assertEqual(proof["unique_encoded_chunks"], 13)
        self.assertEqual(decode.call_count, 14)
        self.assertEqual([f["raw_sha256"] for f in proof["files"]],
                         [row["raw_sha256"] for row in self.index["original_file_bindings"]])
        self.assertEqual(proof["materialized_raw_bytes"], 0)
        self.assertEqual(proof["extracted_encoded_bytes"], 0)
        self.assertEqual(proof["formal_worlds_added"], 0)
        after = {p.relative_to(self.root): (p.stat().st_size, verifier.sha(p.read_bytes()))
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn(str(self.root), json.dumps(proof))

    def test_wrong_outer_sha_is_rejected_before_member_scan(self):
        with mock.patch.object(verifier, "scan_tar") as scan:
            self.reject("complete TAR SHA", expected_tar_sha256="0" * 64)
        scan.assert_not_called()

    def test_wrong_external_length(self):
        self.reject("stat length", expected_tar_bytes=self.tar.stat().st_size + 10240)

    def test_truncated_transport(self):
        self.tar.write_bytes(self.tar.read_bytes()[:-1])
        self.reject("expected TAR length")

    def test_truncated_member_with_re_pinned_outer_transport(self):
        raw = self.tar.read_bytes()
        with tarfile.open(self.tar) as source:
            large = source.getmember("source/cloud_control.py")
        partial = raw[:large.offset_data + 1024]
        partial += bytes((-len(partial)) % 10240)
        self.tar.write_bytes(partial)
        self.reject("offset or length")

    def test_missing_second_normal_eof_block(self):
        raw = bytearray(self.tar.read_bytes())
        with tarfile.open(self.tar) as source:
            last = source.getmembers()[-1]
        eof = last.offset_data + last.size + (-last.size % 512)
        raw[eof + 512] = 1
        self.tar.write_bytes(raw)
        self.reject("two zero EOF")

    def test_nonzero_tail_after_normal_eof(self):
        raw = bytearray(self.tar.read_bytes())
        raw[-1] = 1
        self.tar.write_bytes(raw)
        self.reject("after TAR EOF")

    def test_duplicate_member(self):
        self.build_tar(extra=[("archive-index.json", self.members["archive-index.json"])])
        self.reject("duplicate TAR member")

    def test_missing_member(self):
        del self.members[self.encoded_name()]
        self.build_tar()
        self.reject("incomplete TAR member")

    def test_unexpected_member(self):
        self.build_tar(extra=[("unexpected.bin", b"fixture")])
        self.reject("unexpected or duplicate")

    def test_nonregular_member_types(self):
        for member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE,
                            tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(member_type=member_type):
                self.build_tar(nonregular=(self.encoded_name(), member_type))
                self.reject("regular")

    def test_pax_extension_is_rejected(self):
        self.build_tar(extra=[("x" * 101, b"fixture")])
        self.reject("regular")

    def test_tar_path_escape(self):
        for name in ("../escape", "/escape", "encoded/../escape", "encoded\\escape", "encoded//escape"):
            with self.subTest(name=name):
                self.build_tar(extra=[(name, b"fixture")])
                self.reject("unsafe archive path")

    def test_nonzero_embedded_path_tail(self):
        raw = bytearray(self.tar.read_bytes())
        name_end = raw[:100].index(0)
        raw[name_end + 1] = ord("x")
        # Preserve the normal TAR checksum; the ambiguous lexical tail must fail.
        raw[148:156] = b"        "
        raw[148:156] = f"{sum(raw[:512]):06o}\0 ".encode()
        self.tar.write_bytes(raw)
        self.reject("path tail")

    def test_invalid_tar_header_checksum(self):
        raw = bytearray(self.tar.read_bytes())
        raw[100] ^= 1
        self.tar.write_bytes(raw)
        self.reject("checksum")

    def test_nonzero_member_padding(self):
        raw = bytearray(self.tar.read_bytes())
        with tarfile.open(self.tar) as source:
            member = source.getmember("archive-index.json")
        self.assertNotEqual(member.size % 512, 0)
        raw[member.offset_data + member.size] = 1
        self.tar.write_bytes(raw)
        self.reject("member padding")

    def test_wrong_encoded_member_sha(self):
        name = self.encoded_name()
        raw = bytearray(self.members[name])
        raw[-1] ^= 1
        self.members[name] = bytes(raw)
        self.build_tar()
        self.reject("encoded TAR member SHA")

    def test_wrong_encoded_member_length(self):
        self.members[self.encoded_name()] += b"x"
        self.build_tar()
        self.reject("encoded TAR member length")

    def test_wrong_external_descriptor_sha(self):
        path = self.descriptor_root / (self.index["files"][0]["file"] + ".json")
        path.write_bytes(path.read_bytes() + b"\n")
        self.reject("descriptor exact-byte SHA")

    def test_tar_descriptor_exact_bytes_must_equal_local_metadata(self):
        name = self.index["files"][0]["descriptor"]
        self.members[name] += b"\n"
        self.build_tar()
        self.reject("index/descriptor exact bytes")

    def test_tar_index_exact_bytes_must_equal_local_metadata(self):
        self.members["archive-index.json"] += b"\n"
        self.build_tar()
        self.reject("index/descriptor exact bytes")

    def test_descriptor_raw_offset_order_gap_overlap(self):
        for offset in (0, verifier.codec.CHUNK_BYTES - 1, verifier.codec.CHUNK_BYTES + 1):
            with self.subTest(offset=offset):
                self.first_descriptor()["chunks"][1]["offset"] = offset
                self.sync_metadata()
                self.build_tar()
                self.reject("contiguous and ordered")

    def test_descriptor_chunk_reordering(self):
        self.first_descriptor()["chunks"].reverse()
        self.sync_metadata()
        self.build_tar()
        self.reject("contiguous and ordered")

    def test_full_file_sha_fails_after_all_chunk_shas_pass(self):
        descriptor = self.first_descriptor()
        descriptor["raw_sha256"] = "0" * 64
        self.index["original_file_bindings"][0]["raw_sha256"] = "0" * 64
        self.sync_metadata()
        self.build_tar()
        self.reject("complete raw file SHA")

    def test_descriptor_full_file_pin_disagrees_with_binding(self):
        self.first_descriptor()["raw_sha256"] = "0" * 64
        self.sync_metadata()
        self.build_tar()
        self.reject("original full-file pins")

    def test_encoded_zlib_tail_with_all_outer_pins_updated(self):
        descriptor = self.first_descriptor()
        chunk = descriptor["chunks"][1]
        old_key = chunk["encoded_sha256"]
        raw = self.members.pop("encoded/" + old_key) + b"trailing"
        chunk["encoded_bytes"] = len(raw)
        chunk["encoded_sha256"] = verifier.sha(raw)
        descriptor["encoded_bytes"] += len(b"trailing")
        self.members["encoded/" + chunk["encoded_sha256"]] = raw
        self.sync_metadata()
        self.build_tar()
        self.reject("truncated, trailing")

    def test_catalog_dedup_reference_shape_mismatch(self):
        value = json.loads(self.members["encoded-chunk-catalog.json"])
        value[next(iter(value))]["logical_occurrences"] += 1
        self.members["encoded-chunk-catalog.json"] = json_bytes(value)
        self.build_tar()
        self.reject("catalog differs")

    def test_catalog_numeric_types_cannot_alias_exact_integer_shape(self):
        original = json.loads(self.members["encoded-chunk-catalog.json"])
        key = next(key for key, item in original.items() if item["logical_occurrences"] == 1)
        for field, value in (("logical_occurrences", True), ("logical_occurrences", 1.0),
                             ("encoded_bytes", float(original[key]["shape"]["encoded_bytes"]))):
            with self.subTest(field=field, value=value):
                catalog = json.loads(json.dumps(original))
                if field == "logical_occurrences":
                    catalog[key][field] = value
                else:
                    catalog[key]["shape"][field] = value
                self.members["encoded-chunk-catalog.json"] = json_bytes(catalog)
                self.build_tar()
                self.reject("invalid archive catalog")

    def test_external_descriptor_roster_stops_at_first_unexpected_entry(self):
        consumed_after_unknown = False
        def sequence():
            nonlocal consumed_after_unknown
            yield self.descriptor_root / "unexpected.json"
            consumed_after_unknown = True
            raise AssertionError("unbounded roster enumeration continued")
        @contextmanager
        def entries(_path):
            yield sequence()
        with mock.patch.object(verifier.os, "scandir", entries):
            self.reject("external descriptor roster")
        self.assertFalse(consumed_after_unknown)

    def test_source_drift_with_re_pinned_transport(self):
        self.index["codec_source_pins"]["cloud_archive.py"] = "0" * 64
        self.sync_metadata()
        self.build_tar()
        self.reject("codec source drift")

    def test_archived_control_source_can_differ_from_current_exception_dependency(self):
        raw = b'class GuardError(RuntimeError):\n    pass\n# historical control fixture\n'
        self.members["source/cloud_control.py"] = raw
        self.index["codec_source_pins"]["cloud_control.py"] = verifier.sha(raw)
        self.sync_metadata()
        self.build_tar()
        proof = self.run_verify()
        self.assertEqual(proof["archived_codec_source_sha256"]["cloud_control.py"], verifier.sha(raw))
        self.assertNotEqual(proof["current_codec_source_sha256"]["cloud_control.py"], verifier.sha(raw))
        self.assertTrue(proof["decoder_source_matches_legacy"])

    def test_archived_control_source_full_sha_is_still_required(self):
        self.members["source/cloud_control.py"] += b"# synthetic changed source\n"
        self.build_tar()
        self.reject("source/evidence member SHA")

    def test_archived_control_exception_behavior_is_rejected_even_when_pinned(self):
        raw = b'class GuardError(RuntimeError):\n    def __str__(self):\n        return "changed"\n'
        self.members["source/cloud_control.py"] = raw
        self.index["codec_source_pins"]["cloud_control.py"] = verifier.sha(raw)
        self.sync_metadata()
        self.build_tar()
        self.reject("exception dependency class has behavior")

    def test_runtime_exception_class_added_behavior_is_rejected(self):
        with mock.patch.object(verifier.GuardError, "__repr__", lambda self: "modified fixture", create=True):
            self.reject("unexpected public codec exception dependency")

    def test_wrong_tar_evidence_sha(self):
        name = "evidence/process-exit.json"
        self.members[name] += b"\n"
        self.build_tar()
        self.reject("source/evidence member SHA")

    def test_local_descriptor_path_escape(self):
        self.index["files"][0]["descriptor"] = "descriptors/../escape.json"
        self.index_path.write_bytes(json_bytes(self.index))
        self.reject("unexpected descriptor path")

    def test_symlink_input_and_ancestor_are_refused(self):
        link = self.root / "link.tar"
        link.symlink_to(self.tar)
        self.reject("symlink ancestor", tar_path=link)
        alias = self.root / "alias"
        alias.symlink_to(self.descriptor_root, target_is_directory=True)
        self.reject("symlink ancestor", descriptor_root=alias)

    def test_symlink_descriptor_refused(self):
        path = self.descriptor_root / (self.index["files"][0]["file"] + ".json")
        target = self.root / "descriptor-target.json"
        path.rename(target)
        path.symlink_to(target)
        self.reject("symlink ancestor")

    def test_input_drift_at_end_is_rejected_without_source_mutation(self):
        original = verifier.codec.decode_chunk
        path = self.descriptor_root / (self.index["files"][0]["file"] + ".json")
        changed = False
        def change(encoded, chunk):
            nonlocal changed
            raw = original(encoded, chunk)
            if not changed:
                path.write_bytes(path.read_bytes() + b"\n")
                changed = True
            return raw
        with mock.patch.object(verifier.codec, "decode_chunk", side_effect=change):
            self.reject("metadata or codec source changed")

    def test_tar_drift_after_full_outer_sha_and_scan_is_rejected(self):
        original = verifier.codec.decode_chunk
        changed = False
        def change(encoded, chunk):
            nonlocal changed
            raw = original(encoded, chunk)
            if not changed:
                with self.tar.open("r+b") as output:
                    output.seek(-1, 2)
                    output.write(b"x")
                changed = True
            return raw
        with mock.patch.object(verifier.codec, "decode_chunk", side_effect=change):
            self.reject("input changed")

    def test_nonfinite_and_duplicate_json_are_refused(self):
        for raw in (b'{"a":NaN}', b'{"a":1,"a":2}'):
            with self.subTest(raw=raw), self.assertRaises(verifier.Rejected):
                verifier.parse_json(raw)

    def test_metadata_and_resource_envelopes(self):
        with mock.patch.object(verifier, "MAX_METADATA_BYTES", 1):
            self.reject("metadata exceeds")
        with mock.patch.object(verifier, "MAX_REFERENCES", 13):
            self.reject("reference count")
        with mock.patch.object(verifier, "MAX_UNIQUE_CHUNKS", 12):
            self.reject("unique chunk count")
        with mock.patch.object(verifier, "MAX_SECONDS", 0):
            with self.assertRaises(verifier.Censored):
                self.run_verify()

    def test_small_cli_proof_and_required_explicit_pins(self):
        command = [sys.executable, str(Path(verifier.__file__)), "--tar", str(self.tar),
                   "--index", str(self.index_path), "--descriptor-root", str(self.descriptor_root),
                   "--expected-tar-sha256", verifier.sha(self.tar.read_bytes()),
                   "--expected-tar-bytes", str(self.tar.stat().st_size)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(proof["raw_files"], 13)
        self.assertLess(len(result.stdout.encode()), verifier.MAX_PROOF_BYTES)
        self.assertNotIn(str(self.root), result.stdout)
        failed = subprocess.run(command[:-4], capture_output=True, text=True, check=False)
        self.assertEqual(failed.returncode, 2)

    def test_cli_failed_proof_contains_no_private_path_or_success_claim(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = verifier.main(["--tar", str(self.root / "absent-private-input.tar"),
                                  "--index", str(self.index_path), "--descriptor-root", str(self.descriptor_root),
                                  "--expected-tar-sha256", "0" * 64,
                                  "--expected-tar-bytes", str(self.tar.stat().st_size)])
        self.assertEqual(code, 2)
        proof = json.loads(output.getvalue())
        self.assertEqual(proof["status"], "FAILED")
        self.assertFalse(proof["complete_raw_verification"])
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()
