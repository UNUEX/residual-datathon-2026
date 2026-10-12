import csv
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from src.workspace import DatasetError, analyze_package


def package(count=150):
    rng = np.random.default_rng(97)
    housing = io.StringIO()
    roster = io.StringIO()
    h_writer, r_writer = csv.writer(housing), csv.writer(roster)
    h_writer.writerow(['NOMER', 'OB_PL', 'J_PL', 'KOL_K'])
    r_writer.writerow(['NOMER', 'NOMP', 'KOL_CHL'])
    for index in range(count):
        key = f'{index:06d}'
        area = round(float(rng.uniform(40, 160)), 2)
        members = 1 + index % 6
        h_writer.writerow([key, area, round(area * .6, 2), 1 + index % 4])
        for person in range(1, members + 1):
            r_writer.writerow([key, person, members])
    return housing.getvalue(), roster.getvalue()


class WorkspaceTests(unittest.TestCase):
    def test_semicolon_decimal_comma_leading_zero_and_source_rows(self):
        result = analyze_package('NOMER;OB_PL;J_PL;KOL_K\n001;60,5;80,25;2\n',
                                 'NOMER\tNOMP\n001\t01\n')
        record = result['records'][0]
        self.assertEqual(record['id'], '001')
        self.assertEqual(record['values']['OB_PL'], 60.5)
        self.assertEqual(record['kind'], 'rule')
        self.assertEqual(record['signal_ids'], ['AREA_ORDER'])
        self.assertEqual(record['source_rows']['housing']['row'], 2)
        self.assertEqual(record['source_rows']['housing']['file'], 'D006 — загруженный CSV')
        self.assertEqual(record['source_rows']['roster']['file'], 'D008 — загруженный CSV')
        self.assertEqual(result['summary']['input_files']['housing']['filename'], 'D006 — загруженный CSV')
        self.assertEqual(record['source_rows']['roster']['rows'], [2])
        self.assertFalse(result['summary']['model_metadata']['enabled'])
        json.dumps(result, allow_nan=False)

    def test_missing_values_and_zero_are_clarifications(self):
        result = analyze_package('NOMER,OB_PL,J_PL,KOL_K\n01,,0,0\n', 'NOMER,NOMP\n01,1\n')
        record = result['records'][0]
        self.assertEqual(record['kind'], 'validation')
        self.assertIsNone(record['values']['OB_PL'])
        self.assertEqual(set(record['signal_ids']), {'MISSING_VALUE', 'NONPOSITIVE_VALUE'})
        self.assertEqual(result['summary']['training_rows'], 0)
        self.assertTrue(all(item['median'] is None for item in record['evidence']))
        json.dumps(result, allow_nan=False)

    def test_invalid_numeric_and_nonfinite_values_are_rejected(self):
        for value in ['abc', 'NaN', 'inf', '-Infinity', '1e999', '1,2.3']:
            with self.subTest(value=value), self.assertRaisesRegex(DatasetError, 'конечным числом'):
                analyze_package(f'NOMER;OB_PL;J_PL;KOL_K\n01;{value};20;2\n',
                                'NOMER,NOMP\n01,1\n')

    def test_missing_keys_duplicate_keys_and_missing_links(self):
        cases = [
            ('NOMER,OB_PL,J_PL,KOL_K\n,40,20,2\n', 'NOMER,NOMP\n01,1\n', 'пустой ключ NOMER'),
            ('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n', 'NOMER,NOMP\n01,\n', 'пустой ключ NOMP'),
            ('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n01,50,30,3\n', 'NOMER,NOMP\n01,1\n', 'повтор ключа'),
            ('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n', 'NOMER,NOMP\n01,1\n01,1\n', 'повтор ключа'),
            ('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n', 'NOMER,NOMP\n1,1\n', 'нет карточки'),
        ]
        for housing, roster, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(DatasetError, message):
                analyze_package(housing, roster)

    def test_schema_row_width_and_limit_errors(self):
        for housing, message in [
            ('NOMER,OB_PL,J_PL\n01,40,20\n', 'KOL_K'),
            ('NOMER,OB_PL,J_PL,KOL_K,KOL_K\n01,40,20,2,2\n', 'повторяются'),
            ('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2,5\n', 'значений вместо'),
            ('NOMER,OB_PL,J_PL,KOL_K\n', 'нет строк данных'),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(DatasetError, message):
                analyze_package(housing, 'NOMER,NOMP\n01,1\n')
        with patch('src.workspace.MAX_ROWS', 1), self.assertRaisesRegex(DatasetError, 'больше'):
            analyze_package('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n02,40,20,2\n',
                            'NOMER,NOMP\n01,1\n02,1\n')

    def test_declared_size_disagreement_excludes_training(self):
        result = analyze_package('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n',
                                 'NOMER,NOMP,KOL_CHL\n01,1,3\n01,2,3\n')
        record = result['records'][0]
        self.assertEqual(record['signal_ids'], ['ROSTER_SIZE'])
        self.assertEqual(record['values']['household_size'], 2)
        self.assertEqual(record['declared_household_sizes'], [3])
        self.assertEqual(result['all_records_by_id']['01']['declared_household_sizes'], [3])
        self.assertEqual(result['summary']['training_rows'], 0)
        revised = analyze_package('NOMER,OB_PL,J_PL,KOL_K\n01,40,20,2\n',
                                  'NOMER,NOMP,KOL_CHL\n01,1,4\n01,2,4\n')
        before = result['all_records_by_id']['01']
        after = revised['all_records_by_id']['01']
        self.assertEqual(before['values'], after['values'])
        self.assertEqual(before['signal_ids'], after['signal_ids'])
        self.assertNotEqual(before['declared_household_sizes'], after['declared_household_sizes'])

    def test_iforest_reproducible_and_does_not_displace_baseline(self):
        housing, roster = package()
        first, second = analyze_package(housing, roster), analyze_package(housing, roster)
        self.assertTrue(first['summary']['model_metadata']['enabled'])
        self.assertEqual(first['summary']['training_rows'], 150)
        self.assertEqual(first['summary']['base_queue_size'], 15)
        self.assertEqual(first['summary']['ml_queue'], 3)
        self.assertEqual(first['records'], second['records'])
        self.assertEqual(len(first['all_records_by_id']), 150)
        self.assertEqual(len({r['id'] for r in first['records']}), 18)
        self.assertTrue(all(r['supplemental'] for r in first['records'][15:]))
        self.assertNotIn('NOMER', first['summary']['features'])
        json.dumps(first, allow_nan=False)

    def test_all_explicit_signals_retained_above_candidate_budget(self):
        housing = 'NOMER,OB_PL,J_PL,KOL_K\n' + ''.join(
            f'{i:04d},40,80,2\n' for i in range(300))
        roster = 'NOMER,NOMP\n' + ''.join(f'{i:04d},1\n' for i in range(300))
        result = analyze_package(housing, roster)
        self.assertEqual(len(result['records']), 300)
        self.assertEqual(result['summary']['rule_flags'], 300)
        self.assertTrue(result['summary']['queue_policy']['expanded_for_signals'])
        self.assertEqual(len(result['all_records_by_id']), 300)

    def test_input_fingerprint_and_unsupported_period(self):
        housing, roster = package(3)
        original = analyze_package(housing, roster)
        changed = analyze_package(housing.replace('0.6', '0.7') + '\n', roster)
        self.assertNotEqual(original['summary']['input_sha256'], changed['summary']['input_sha256'])
        with self.assertRaisesRegex(DatasetError, '2024'):
            analyze_package(housing, roster, year=2023)

    def test_optional_known_dataset(self):
        data = Path(__file__).resolve().parents[2] / 'synthetic_microdata_2021-2024_20260921'
        housing, roster = data / 'd006/2024/rio_2024.csv', data / 'd008/2024/kontr_k.csv'
        if not housing.exists() or not roster.exists():
            self.skipTest('Конкурсный архив не установлен локально')
        result = analyze_package(housing, roster)
        self.assertEqual(result['summary']['housing_rows'], 11704)
        self.assertEqual(result['summary']['living_exceeds_total'], 46)
        self.assertEqual(result['summary']['base_queue_size'], 250)
        self.assertEqual(result['summary']['ml_queue'], 50)
        self.assertEqual(result['summary']['queue_size'], 300)
        self.assertEqual(len(result['all_records_by_id']), 11704)
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
