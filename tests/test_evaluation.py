import csv
import io
import unittest

import numpy as np

from src.evaluate_workspace import METHODS, SCENARIOS, alter_package, measure, select_queues
from src.workspace import analyze_package


def fixture(count=600):
    rng = np.random.default_rng(9)
    housing, roster = io.StringIO(), io.StringIO()
    h, r = csv.writer(housing), csv.writer(roster)
    h.writerow(['NOMER', 'OB_PL', 'J_PL', 'KOL_K'])
    r.writerow(['NOMER', 'NOMP', 'KOL_CHL'])
    for i in range(count):
        key = f'{i:05d}'
        total = round(float(rng.uniform(35, 180)), 2)
        living = total * (1.2 if i == 0 else float(rng.uniform(.3, .85)))
        h.writerow([key, total, living, 1 + i % 5])
        for person in (1, 2):
            r.writerow([key, person, 2])
    return housing.getvalue(), roster.getvalue()


def csv_rows(text):
    return {row['NOMER']: row for row in csv.DictReader(io.StringIO(text))}


class CurrentPipelineEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.housing, cls.roster = fixture()
        cls.original = analyze_package(cls.housing, cls.roster)
        cls.eligible = [key for key, row in cls.original['all_records_by_id'].items()
                        if row['robust_score'] is not None]

    def test_equal_budgets_original_priority_and_production_hybrid(self):
        selection = select_queues(self.original, 17, base_budget=60, total_budget=72)
        self.assertEqual(selection['priority'], ['00000'])
        for queue in selection['queues'].values():
            self.assertEqual(len(queue), 72)
            self.assertEqual(len(set(queue)), 72)
            self.assertIn('00000', queue)
        self.assertEqual(selection['queues'][METHODS[1]], [r['id'] for r in self.original['records']])
        self.assertEqual(selection, select_queues(self.original, 17, 60, 72))
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            select_queues(self.original, 17, 600, 650)

    def test_gross_alters_disjoint_eligible_rows_without_changing_controls(self):
        original_rows = csv_rows(self.housing)
        changed, labels = alter_package(self.housing, self.eligible, 'gross', 17)
        self.assertEqual(len(labels), 3 * int(len(self.eligible) * .01))
        self.assertNotIn('00000', labels)
        changed_rows = csv_rows(changed)
        for key in set(original_rows) - set(labels):
            self.assertEqual(original_rows[key], changed_rows[key])
        self.assertEqual(original_rows, csv_rows(self.housing))
        for key, kind in labels.items():
            old, new = original_rows[key], changed_rows[key]
            if kind == 'living_exceeds_total':
                self.assertGreater(float(new['J_PL']), float(new['OB_PL']))
            elif kind == 'area_unit_x10':
                self.assertAlmostEqual(float(new['OB_PL']), float(old['OB_PL']) * 10)
                self.assertAlmostEqual(float(new['J_PL']), float(old['J_PL']) * 10)
            else:
                self.assertEqual(float(new['KOL_K']), float(old['KOL_K']) * 8)

    def test_moderate_mechanisms_preserve_positive_noncontradictory_values(self):
        changed, labels = alter_package(self.housing, self.eligible, 'moderate', 29)
        before, after = csv_rows(self.housing), csv_rows(changed)
        for key, kind in labels.items():
            old, new = before[key], after[key]
            total, living, rooms = [float(new[f]) for f in ('OB_PL', 'J_PL', 'KOL_K')]
            self.assertTrue(0 < living <= total)
            self.assertTrue(rooms > 0 and rooms.is_integer())
            if kind == 'area_scale_x1_4':
                self.assertAlmostEqual(total, float(old['OB_PL']) * 1.4)
                self.assertAlmostEqual(living / total, float(old['J_PL']) / float(old['OB_PL']))
            elif kind == 'rooms_plus_one':
                self.assertEqual(rooms, float(old['KOL_K']) + 1)
            else:
                self.assertAlmostEqual(abs(living / total - float(old['J_PL']) / float(old['OB_PL'])), .15)

    def test_changes_enter_actual_package_before_fit_and_metrics_keep_unknown_controls(self):
        changed, labels = alter_package(self.housing, self.eligible, 'gross', 43)
        result = analyze_package(changed, self.roster)
        self.assertEqual(result['summary']['rule_flags'], 1 + int(len(self.eligible) * .01))
        self.assertEqual(result['summary']['model_metadata']['training_rows'],
                         600 - result['summary']['rule_flags'])
        self.assertNotEqual(result['summary']['input_sha256'], self.original['summary']['input_sha256'])
        selected = select_queues(result, 43, 60, 72)
        self.assertEqual(selected['queues'][METHODS[1]], [r['id'] for r in result['records']])
        rows, marginal = measure(selected, labels, 'gross', 43)
        for row in rows:
            self.assertEqual(row['found'] + row['unchanged_reviewed'], 72)
            self.assertEqual(sum(x['found'] for x in row['by_scenario'].values()), row['found'])
            self.assertEqual(sum(x['altered'] for x in row['by_scenario'].values()), len(labels))
            self.assertEqual(row['base_found'] + row['supplemental_found'], row['found'])
            self.assertNotIn('false_positives', row)
        self.assertEqual(marginal['ml_minus_next_robust'], rows[1]['found'] - rows[0]['found'])

    def test_explicit_signals_are_never_silently_truncated(self):
        records = {str(i): dict(signal_ids=['AREA_ORDER'], robust_score=None, ml_score=None)
                   for i in range(11)}
        with self.assertRaisesRegex(ValueError, 'Explicit signals exceed'):
            select_queues({'all_records_by_id': records}, 17, 10, 20)


if __name__ == '__main__':
    unittest.main()
