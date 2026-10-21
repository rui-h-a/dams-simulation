"""Small arithmetic/schema tests; no download, Model or calibration."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("acs_diag", Path(__file__).parents[1] / "research_tools/acs_population_diagnostic.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class DiagnosticTests(unittest.TestCase):
    def test_signed_replicate_sdr_and_conditional(self):
        table = {}
        a = [10] + [-2] * 40 + [22] * 40
        b = [30] + [42] * 40 + [18] * 40
        m.add(table, ("g", "a"), a); m.add(table, ("g", "b"), b)
        cell = m.summarize(table, 1)["cells"][0]
        self.assertEqual(cell["weighted_total"], 10)
        self.assertEqual(cell["weighted_total_se"], 24)
        self.assertEqual(cell["joint_share"], .25)
        self.assertAlmostEqual(cell["conditional_share_se"], .6)
        self.assertEqual(a[1], -2)

    def test_zero_replicate_denominator_is_not_clipped(self):
        table = {("g", "a"): [1, [1] + [0]*80]}
        cell = m.summarize(table, 1)["cells"][0]
        self.assertIsNone(cell["joint_share_se"])
        self.assertTrue(cell["replicate_ratio_unestimated"])

    def test_frozen_bins_do_not_conflate_na_zero_hours(self):
        self.assertEqual(m.band(16, m.AGE_BINS), "16_24")
        self.assertEqual(m.hours(""), "not_applicable")
        self.assertEqual(m.hours("99"), "99_plus")
        self.assertEqual(m.category(""), "(blank)")

    def test_alias_ambiguity_refused(self):
        self.assertEqual(m.alias(["STATE"], ("STATE", "ST")), "STATE")
        with self.assertRaises(ValueError): m.alias(["STATE", "ST"], ("STATE", "ST"))

    def test_household_not_care_hours_and_gq_separate(self):
        self.assertEqual(m.context({"TYPEHUGQ": "1", "R18": "1", "R65": "2"}, "TYPEHUGQ"), "HU_u18_1_65_1")
        self.assertEqual(m.context({"TYPE": "2"}, "TYPE"), "GQ_institutional")

    def test_no_network_without_explicit_flag(self):
        with patch.object(m.urllib.request, "build_opener", side_effect=AssertionError("network forbidden")):
            with self.assertRaises(ValueError):
                m.main(["run", "--plan", "/fiction", "--plan-sha256", "0"*64,
                        "--output", "/fiction", "--safety-root", "/fiction"])

    def test_tiny_zip_normal_eof_and_original_codes(self):
        root = Path(__file__).parents[1]
        s = os.statvfs(root)
        self.assertGreaterEqual(s.f_bavail*s.f_frsize, 2*1024**3)
        with tempfile.TemporaryDirectory(prefix="acs-tiny-", dir=root) as td:
            path = Path(td)/"tiny.zip"
            raw = b"SERIALNO,STATE,SCHL\ns,44,01\n"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("tiny.csv", raw)
            description = {}
            guard = m.Guard((root,))
            self.assertEqual(list(m.records(path, ("SERIALNO", "SCHL"), guard, description)),
                             [{"SERIALNO":"s", "STATE":"44", "SCHL":"01"}])
            self.assertEqual(guard.decoded, len(raw))
            self.assertTrue(description["ZIP_CRC_accepted"])
            self.assertEqual(description["members"][0]["bytes"], len(raw))


if __name__ == "__main__": unittest.main()
