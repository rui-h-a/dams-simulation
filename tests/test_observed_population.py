"""Tiny fictional rows only: no original ACS files, Model or ETL runs."""
import copy
import csv
from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from argparse import Namespace

from dams_sim.observed_population import (
    FLAGS, PERSON_FIELDS, REPLICATES, ObservedPopulationConfig,
    ObservedPopulationPool, build_documents, canonical, sha256,
)
from dams_sim.randomness import WorldRandom
from research_tools.build_observed_population import execute


def fiction():
    a = {name: "" for name in PERSON_FIELDS + FLAGS + REPLICATES}
    a.update(SERIALNO="2023HU0000001", source_state="44", PUMA="00100", SPORDER="1",
             AGEP="30", SEX="1", SCHL="21", ESR="1", COW="1", INDP="7860", OCCP="1010",
             WKHP="40", WKWN="52", WAGP="0", ADJINC="1019518", JWTRNS="1", JWMNP="20",
             RELSHIPP="20", GCL="0", GCR="", GCM="", PWGTP="1")
    a.update({k: "0" for k in FLAGS})
    a.update({k: "1" for k in REPLICATES})
    a["PWGTP1"] = "-2"  # Signed replicate survives; it is not a probability.
    child = dict(a, SPORDER="2", AGEP="10", SCHL="8", ESR="", COW="", WKHP="", WKWN="", WAGP="", JWMNP="", JWTRNS="", RELSHIPP="25")
    b = dict(a, SERIALNO="2023HU0000002", AGEP="60", SEX="2", SCHL="16", WKHP="99", JWTRNS="11", JWMNP="", PWGTP="3", FAGEP="1")
    h = {"SERIALNO": a["SERIALNO"], "STATE": "44", "PUMA": "00100", "TYPEHUGQ": "1", "NP": "2", "R18": "1", "R65": "0", "WGTP": "1"}
    return [a, child, b], [h, dict(h, SERIALNO=b["SERIALNO"], NP="1", R18="0")]


def documents():
    return build_documents(*fiction(), source_manifest={"reference_year": 2023, "origin": "tiny fictional test, not ACS observations"})


def admitted(docs=None):
    docs = docs or documents()
    pins = {"expected_" + k + "_sha256": sha256(canonical(v)) for k, v in docs.items()}
    return ObservedPopulationPool.from_documents(**docs, **pins)


class FixedRandom:
    seed, world, world_context = 19, 4, "firm-world"

    def __init__(self, value):
        self.value, self.calls = value, []

    def uniform(self, *args):
        self.calls.append(args)
        return self.value


class ObservedPopulationTests(unittest.TestCase):
    def test_integer_cdf_boundary_and_joint_row(self):
        pool = admitted()
        rng = FixedRandom(0.25)  # W=4: ticket1, so second donor, not independent marginals.
        profile = pool.draw(rng, firm_key="buyer", person_token="entry:3")
        self.assertEqual((profile.age_completed_years, profile.education_code, profile.sex_code), (60, "16", "2"))
        self.assertIsNone(profile.usual_weekly_hours)
        self.assertEqual(profile.usual_weekly_hours_lower_bound, 99)
        self.assertTrue(profile.worked_from_home)
        self.assertIsNone(profile.commute_oneway_minutes)
        self.assertEqual(profile.missing["WKHP"]["status"], "interval_99_plus")
        self.assertEqual(rng.calls, [("observed_population_donor_v1", "buyer", "entry:3", pool.manifest_sha256)])
        self.assertEqual(profile.provenance["ticket"], 1)

    def test_household_wage_zero_missing_allocation_and_no_latent_bridge(self):
        p = admitted().draw(FixedRandom(0.0), firm_key="buyer", person_token="entry:0")
        self.assertEqual(p.wage_income_past12, 0)
        self.assertEqual(p.raw_person["PWGTP1"], "-2")
        self.assertEqual(len(p.household_context["members"]), 2)
        self.assertEqual(p.household_context["members"][1]["raw_person"]["AGEP"], "10")
        self.assertNotIn("care_hours", p.to_dict()["observed"])
        self.assertNotIn("education_years", p.to_dict()["observed"])
        self.assertIn("education_to_learning_effect", p.to_dict()["unmeasured"])
        with self.assertRaises(FrozenInstanceError):
            p.age_completed_years = 99
        raw = p.raw_person
        raw["AGEP"] = "99"
        self.assertEqual(p.raw_person["AGEP"], "30")

    def test_canonical_order_iteration_and_restore_pins(self):
        people, homes = fiction()
        docs = build_documents(people[::-1], homes[::-1], source_manifest=documents()["pool"]["source_manifest"])
        self.assertEqual(docs, documents())
        pool = admitted(docs)
        rng = WorldRandom(2, 5, world_context="common")
        original = pool.draw(rng, firm_key="buyer", person_token="entry:0")
        pool.draw(rng, firm_key="buyer", person_token="entry:8")
        self.assertEqual(pool.restore_profile(original.to_witness(), rng=rng, firm_key="buyer", person_token="entry:0"), original)
        altered = original.to_witness()
        altered["raw_person"]["SCHL"] = "24"
        with self.assertRaisesRegex(ValueError, "restored"):
            pool.restore_profile(altered, rng=rng, firm_key="buyer", person_token="entry:0")
        with self.assertRaisesRegex(ValueError, "restored"):
            pool.restore_profile(original.to_witness(), rng=rng, firm_key="target", person_token="entry:0")
        bad = copy.deepcopy(docs)
        bad["mapping"]["no_conversion"] = []
        with self.assertRaises(ValueError):
            admitted(bad)

    def test_invalid_unsampled_support_join_and_weights_fail_at_admission(self):
        for key, value in (("SCHL", "99"), ("WKHP", "100"), ("SEX", "3"), ("PWGTP", "-1"), ("JWMNP", "20")):
            people, homes = fiction()
            people[-1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_documents(people, homes, source_manifest={"reference_year": 2023})
        people, homes = fiction()
        homes[0]["NP"] = "1"
        with self.assertRaisesRegex(ValueError, "roster"):
            build_documents(people, homes, source_manifest={"reference_year": 2023})
        people, homes = fiction()
        people[-1]["PUMA"] = "00999"
        with self.assertRaisesRegex(ValueError, "join"):
            build_documents(people, homes, source_manifest={"reference_year": 2023})

    def test_gq_and_nonprivate_workers_preserved_but_not_sampled(self):
        people, homes = fiction()
        gq = dict(people[0], SERIALNO="2023GQ0000003", AGEP="40", PWGTP="999")
        other = dict(people[0], SERIALNO="2023HU0000004", COW="2", PWGTP="999")
        people.extend((gq, other))
        homes.extend((dict(homes[1], SERIALNO=gq["SERIALNO"], TYPEHUGQ="2"),
                      dict(homes[1], SERIALNO=other["SERIALNO"])))
        docs = build_documents(people, homes, source_manifest={"reference_year": 2023})
        self.assertEqual(docs["manifest"]["all_person_rows"], 5)
        self.assertEqual(docs["manifest"]["eligible_person_rows"], 2)
        self.assertEqual(docs["manifest"]["total_sampling_weight"], 4)
        self.assertEqual(admitted(docs).draw(FixedRandom(0.999), firm_key="buyer", person_token="x").raw_person["SERIALNO"], "2023HU0000002")

    def test_config_strict_and_files_roundtrip(self):
        docs = documents()
        pool = admitted(docs)
        config = ObservedPopulationConfig(pool.manifest_sha256, pool.pool_sha256, pool.mapping_sha256, "buyer").validate()
        pool.verify_config(ObservedPopulationConfig.from_dict(config.to_dict()))
        with self.assertRaises(ValueError):
            ObservedPopulationConfig.from_dict({**config.to_dict(), "care_coefficient": 0.5})
        with tempfile.TemporaryDirectory(prefix="observed-tiny-") as tmp:
            root = Path(tmp)
            for name, value in docs.items():
                (root / (name + ".json")).write_bytes(canonical(value))
            loaded = ObservedPopulationPool.from_files(root / "manifest.json", expected_manifest_sha256=pool.manifest_sha256, expected_pool_sha256=pool.pool_sha256, expected_mapping_sha256=pool.mapping_sha256)
            self.assertEqual(loaded.draw(FixedRandom(0.5), firm_key="buyer", person_token="x"), pool.draw(FixedRandom(0.5), firm_key="buyer", person_token="x"))

    def test_default_builder_and_dotdot_fail_before_input_reads(self):
        args = Namespace(execute_build=False)
        with patch("research_tools.build_observed_population._read_pinned", side_effect=AssertionError("input read")):
            with self.assertRaisesRegex(ValueError, "admission"):
                execute(args)
            args.execute_build = True
            args.output = "/fiction/other/../source/new"
            with self.assertRaisesRegex(ValueError, "unsafe"):
                execute(args)

    def test_tiny_offline_builder_eof_binding_and_fresh_output(self):
        people, homes = fiction()
        def csv_bytes(rows):
            text = io.StringIO(newline="")
            writer = csv.DictWriter(text, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            return text.getvalue().encode()
        with tempfile.TemporaryDirectory(prefix="observed-builder-tiny-") as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            p = source / "person.csv"
            p.write_bytes(csv_bytes(people))
            z = source / "housing.zip"
            with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("psam_h44.csv", csv_bytes(homes))
                archive.writestr("README.pdf", b"fiction not official PDF")
            psha, zsha = sha256(p.read_bytes()), sha256(z.read_bytes())
            upstream = {"schema": "acs-ri-measurement-diagnostic-1", "status": "DATA_MEASUREMENT_DIAGNOSTIC_ONLY", "files": [{"name": "2023-h-ri.zip", "year": 2023, "kind": "h", "bytes": z.stat().st_size, "sha256": zsha, "ZIP_CRC_accepted": True}], "output_files": [{"name": "2023-person-context.csv", "bytes": p.stat().st_size, "sha256": psha}]}
            m = source / "upstream.json"
            m.write_bytes(canonical(upstream))
            args = Namespace(execute_build=True, output=str(root / "output"), person_csv=str(p), housing_zip=str(z), source_manifest=str(m), safety_root=str(root), year=2023, person_sha256=psha, housing_sha256=zsha, source_sha256=sha256(m.read_bytes()))
            result = execute(args)
            self.assertEqual(result["formal_worlds_added"], 0)
            self.assertLess(sum(f.stat().st_size for f in root.rglob("*") if f.is_file()), 100000)
            manifest = result["files"]
            pool = ObservedPopulationPool.from_files(root / "output" / "manifest.json", expected_manifest_sha256=manifest["manifest"]["sha256"], expected_pool_sha256=manifest["pool"]["sha256"], expected_mapping_sha256=manifest["mapping"]["sha256"])
            self.assertEqual(pool.draw(FixedRandom(0.0), firm_key="buyer", person_token="p").commute_oneway_minutes, 20)
            with self.assertRaisesRegex(ValueError, "fresh"):
                execute(args)


if __name__ == "__main__":
    unittest.main()
