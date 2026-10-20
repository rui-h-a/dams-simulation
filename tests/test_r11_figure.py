import json
import tempfile
import unittest
from pathlib import Path

from research_tools.r11_figure import draw, rows
from research_tools.r11_world_count import PRIMARY


def inference():
    comp = {k: {'unit': 'u', 'worlds': 5, 'mean_difference': -1.0, 'ci95': [-2.0, 0.5]} for k in PRIMARY}
    comp['closed'] = {'worlds': 1, 'status': 'fewer than two complete pairs'}
    return {'reference': 'control-hierarchy', 'comparisons': {'dams-witness': comp}}


class FigureTest(unittest.TestCase):
    def test_rows_keep_primary_outcomes_with_intervals_only(self):
        r = rows(inference())
        self.assertEqual([x[1] for x in r], list(PRIMARY))

    def test_draw_writes_identical_bytes_twice(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'inference.json').write_text(json.dumps(inference()))
            first = draw(d).read_bytes()
            self.assertEqual(draw(d).read_bytes(), first)
            self.assertTrue(first.startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
