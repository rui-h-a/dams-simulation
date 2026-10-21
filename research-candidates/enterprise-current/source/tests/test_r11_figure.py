import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_tools import r11_figure
from research_tools.r11_figure import draw, rows
from research_tools.r11_world_count import PRIMARY


def inference():
    comp = {k: {'unit': 'u', 'worlds': 5, 'mean_difference': -1.0, 'ci95': [-2.0, 0.5],
                'status': 'conditional complete-pair Student-t summary'} for k in PRIMARY}
    comp['closed'] = {'worlds': 1, 'status': 'fewer than two complete pairs'}
    return {'schema': 'r11-roster-and-raw-inference-v2', 'admission_errors': [],
            'reference': 'control-hierarchy', 'comparisons': {'dams-witness': comp}}


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

    def test_undefined_ci_and_insufficient_pairs_are_not_plotted_as_zero(self):
        data = inference()
        comparison = data['comparisons']['dams-witness']
        comparison[PRIMARY[0]].update(ci95=None, status='degenerate paired differences; Student-t inference undefined')
        comparison[PRIMARY[1]] = {'unit': 'u', 'worlds': 1, 'status': 'fewer than two complete pairs'}
        self.assertEqual([r[1] for r in rows(data)], [PRIMARY[2]])
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'inference.json').write_text(json.dumps(data))
            with patch.object(r11_figure.plt, 'close'):
                self.assertTrue(draw(d).read_bytes().startswith(b'%PDF'))
                fig = r11_figure.plt.gcf()
            try:
                for ax in fig.axes[:2]:
                    self.assertEqual(list(ax.get_xticks()), [])
                    self.assertEqual(list(ax.lines), [])
                self.assertIn('CI undefined (n=5)', [t.get_text() for t in fig.axes[0].texts])
                self.assertIn('Not estimated (n=1)', [t.get_text() for t in fig.axes[1].texts])
                self.assertTrue(fig.axes[2].lines)
            finally:
                r11_figure.plt.close(fig)

    def test_short_primary_titles_and_wrapped_units_fit_the_canvas(self):
        data = inference()
        units = ('resource units / member-year', 'regret units / member-hour',
                 'mixed member-hours and resource units / member-year')
        for key, unit in zip(PRIMARY, units):
            data['comparisons']['dams-witness'][key]['unit'] = unit
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'inference.json').write_text(json.dumps(data))
            with patch.object(r11_figure.plt, 'close'):
                draw(d)
                fig = r11_figure.plt.gcf()
            try:
                fig.canvas.draw()
                boxes = [ax.title.get_window_extent(fig.canvas.get_renderer()) for ax in fig.axes]
                for box in boxes:
                    self.assertGreaterEqual(box.x0, 0)
                    self.assertLessEqual(box.x1, fig.bbox.x1)
                    self.assertLessEqual(box.y1, fig.bbox.y1)
                self.assertFalse(boxes[0].overlaps(boxes[1]))
                self.assertFalse(boxes[1].overlaps(boxes[2]))
                self.assertIn('Legacy governance index', fig.axes[2].get_title())
                self.assertNotIn('cost', fig.axes[2].get_title())
            finally:
                r11_figure.plt.close(fig)

    def test_nonempty_comparison_with_all_unestimated_rows_is_explicit(self):
        data = inference()
        data['comparisons']['dams-witness'] = {k: {'unit': 'u', 'worlds': 0,
                                                'status': 'fewer than two complete pairs'} for k in PRIMARY}
        self.assertEqual(rows(data), [])
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'inference.json').write_text(json.dumps(data))
            with patch.object(r11_figure.plt, 'close'):
                self.assertTrue(draw(d).read_bytes().startswith(b'%PDF'))
                fig = r11_figure.plt.gcf()
            try:
                for ax in fig.axes:
                    self.assertEqual(list(ax.get_xticks()), [])
                    self.assertEqual(list(ax.lines), [])
                    self.assertIn('Not estimated (n=0)', [t.get_text() for t in ax.texts])
            finally:
                r11_figure.plt.close(fig)

    def test_unadmitted_or_aggregate_reports_are_refused(self):
        for data in ({'comparisons': {}}, {**inference(), 'comparisons': {}},
                     {**inference(), 'admission_errors': ['legacy unbound plan']},
                     {**inference(), 'schema': 'unverified-aggregate'}):
            with self.assertRaises(ValueError):
                rows(data)


if __name__ == '__main__':
    unittest.main()
