"""Statistics used by research_tools/r11_inference.py against published constants."""
import unittest

from research_tools.r11_inference import holm, t_quantile, t_two_sided_p


class InferenceStatisticsTest(unittest.TestCase):
    def test_student_t_quantiles(self):
        for df, q in [(2, 4.302653), (9, 2.262157), (56, 2.003241), (299, 1.967929)]:
            self.assertAlmostEqual(t_quantile(.975, df), q, places=5)

    def test_two_sided_p_and_holm(self):
        self.assertAlmostEqual(t_two_sided_p(2.262157, 9), .05, places=6)
        self.assertAlmostEqual(t_two_sided_p(1.0, 30), .325309, places=5)
        self.assertEqual(holm([.01, .04, .03]), [.03, .06, .06])


if __name__ == '__main__':
    unittest.main()
