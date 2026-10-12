import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.service import Workspace, compare_packages
from src.serve import make_server


def fixture(fixed=False):
    housing = 'NOMER,OB_PL,J_PL,KOL_K\n'
    roster = 'NOMER,NOMP,KOL_CHL\n'
    for i in range(120):
        area = 60 + i % 31
        living = area * .6 if i or fixed else area + 20
        housing += f'{i:05d},{area},{living},3\n'
        roster += f'{i:05d},01,1\n'
    return dict(housing_csv=housing, roster_csv=roster, year=2024, label='Тестовый пакет')


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, Path(cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def call(self, path, body=None, headers=None):
        headers = headers or {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if 'application/json' in response.headers['Content-Type'] else raw

    def analyze(self, body):
        job = self.call('/api/analyze', body)['job_id']
        limit = time.monotonic() + 30
        while time.monotonic() < limit:
            result = self.call('/api/jobs/' + job)
            if result['status'] == 'done':
                return self.call('/api/state?run_id=' + result['run_id'])
            if result['status'] == 'error':
                self.fail(result['error'])
            time.sleep(.1)
        self.fail('Analysis timed out')

    def test_complete_import_review_assistant_export_revision(self):
        first = self.analyze(fixture())
        self.assertEqual(first['summary']['housing_rows'], 120)
        record = next(r for r in first['records'] if r['id'] == '00000')
        self.assertEqual(record['kind'], 'rule')
        answer = self.call('/api/assistant', dict(run_id=first['run_id'], id='00000', action='explain'))
        self.assertIn('80', answer['answer'])
        self.assertTrue(answer['sources'])
        self.call('/api/review', dict(run_id=first['run_id'], id='00000', status='needs_check', comment='=2+2'))
        self.call('/api/review', dict(run_id=first['run_id'], id='00000', status='confirmed', comment='Сверено с учебным источником'))
        reread = self.call('/api/state?run_id=' + first['run_id'])
        self.assertEqual(len(reread['review_history']), 2)
        self.assertEqual(next(r for r in reread['records'] if r['id'] == '00000')['status'], 'confirmed')
        exported = self.call('/api/export?format=json&run_id=' + first['run_id'])
        self.assertEqual(len(exported['review_history']), 2)
        self.call('/api/review', dict(run_id=first['run_id'], id='00000', status='needs_check', comment='=2+2'))
        csv_data = self.call('/api/export?format=csv&run_id=' + first['run_id']).decode('utf-8-sig')
        row = next(r for r in csv.DictReader(io.StringIO(csv_data), delimiter=';') if r['NOMER'] == '00000')
        self.assertEqual(row['Комментарий'], "'=2+2")
        second_body = fixture(fixed=True); second_body['base_run_id'] = first['run_id']
        second = self.analyze(second_body)
        comparison = second['comparison']
        self.assertEqual(comparison['resolved_count'], 1)
        case = next(c for c in comparison['items'] if c['id'] == '00000')
        self.assertEqual(case['change'], 'resolved')
        self.assertEqual(case['before']['values']['J_PL'], 80)
        self.assertEqual(case['after']['values']['J_PL'], 36)
        self.assertTrue(all(r['status'] == 'pending' for r in second['records']))
        # Original data and original version's review history survive a new upload.
        original = self.call('/api/state?run_id=' + first['run_id'])
        self.assertEqual(next(r for r in original['records'] if r['id'] == '00000')['values']['J_PL'], 80)
        self.assertEqual(len(original['review_history']), 3)
        resumed = Workspace(Path(self.temp.name)).state(second['run_id'])
        self.assertEqual(resumed['comparison']['resolved_count'], 1)

    def test_local_boundary_and_input_rejections(self):
        with self.assertRaises(HTTPError) as caught:
            self.call('/api/review', {}, {'Origin': 'https://untrusted.example'})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.call('/api/state?run_id=../../source')
        self.assertEqual(caught.exception.code, 400)
        body = fixture(); body['year'] = 2023
        with self.assertRaises(HTTPError) as caught:
            self.call('/api/analyze', body)
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.call('/api/review', dict(id='x', status='confirmed', comment=''))
        self.assertEqual(caught.exception.code, 400)

    def test_model_rank_change_is_not_correction(self):
        old = dict(year=2024, run_id='old', records=[{'id': 'a'}], all_records_by_id={
            'a': {'values': {'OB_PL': 70}, 'signal_ids': ['IFOREST_CANDIDATE']}})
        new = dict(year=2024, records=[], all_records_by_id={
            'a': {'values': {'OB_PL': 70}, 'signal_ids': []}})
        result = compare_packages(old, new)
        self.assertEqual(result['resolved_count'], 0)
        self.assertEqual(result['unchanged_count'], 1)
        new['all_records_by_id'] = {}
        result = compare_packages(old, new)
        self.assertEqual(result['resolved_count'], 0)
        self.assertEqual(result['missing_count'], 1)

    def test_comparison_tracks_unflagged_and_declared_size_changes(self):
        old = dict(year=2024, run_id='old', records=[], all_records_by_id={
            'ordinary': {'values': {'OB_PL': 60}, 'signal_ids': []},
            'roster': {'values': {'household_size': 2}, 'signal_ids': ['ROSTER_SIZE'], 'declared_household_sizes': [3]}})
        new = dict(year=2024, records=[], all_records_by_id={
            'ordinary': {'values': {'OB_PL': 80}, 'signal_ids': []},
            'roster': {'values': {'household_size': 2}, 'signal_ids': ['ROSTER_SIZE'], 'declared_household_sizes': [4]}})
        result = compare_packages(old, new)
        self.assertEqual(result['changed_count'], 2)
        self.assertEqual(result['resolved_count'], 0)

    def test_added_records_are_separate_from_new_signals(self):
        old = dict(year=2024, run_id='old', records=[], all_records_by_id={})
        new = dict(year=2024, records=[], all_records_by_id={
            'ordinary': {'values': {'OB_PL': 60}, 'signal_ids': []},
            'statistical': {'values': {'OB_PL': 80}, 'signal_ids': ['IFOREST_CANDIDATE']},
            'contradiction': {'values': {'OB_PL': 60, 'J_PL': 80}, 'signal_ids': ['AREA_ORDER']}})
        result = compare_packages(old, new)
        self.assertEqual(result['added_count'], 3)
        self.assertEqual(result['added_with_signals_count'], 1)
        self.assertEqual(result['new_count'], 0)
        self.assertTrue(all(item['change'] == 'added' for item in result['items']))

    def test_new_violation_in_existing_record_counts_as_new_signal(self):
        old = dict(year=2024, run_id='old', records=[], all_records_by_id={
            'a': {'values': {'OB_PL': 60, 'J_PL': 40}, 'signal_ids': []}})
        new = dict(year=2024, records=[], all_records_by_id={
            'a': {'values': {'OB_PL': 60, 'J_PL': 80}, 'signal_ids': ['AREA_ORDER']}})
        result = compare_packages(old, new)
        self.assertEqual(result['new_count'], 1)
        self.assertEqual(result['added_count'], 0)
        self.assertEqual(result['items'][0]['change'], 'new')

    def test_comparison_export_includes_every_change_and_escapes_formulas(self):
        first_body = fixture(fixed=True)
        for key in ('housing_csv', 'roster_csv'):
            first_body[key] = first_body[key].replace('00119,', '=2+2,')
        first = self.analyze(first_body)
        second_body = dict(first_body, base_run_id=first['run_id'], label='=SUM(1,2)')
        housing = list(csv.DictReader(io.StringIO(first_body['housing_csv'])))
        for row in housing:
            row['OB_PL'] = str(float(row['OB_PL']) + 5)
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=housing[0].keys())
        writer.writeheader(); writer.writerows(housing)
        second_body['housing_csv'] = buffer.getvalue()
        second = self.analyze(second_body)
        self.assertEqual(second['comparison']['changed_count'], 120)
        raw = self.call('/api/export?format=comparison_csv&run_id=' + second['run_id'])
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 120)
        self.assertEqual({r['NOMER'] for r in rows},
                         {f'{i:05d}' for i in range(119)} | {"'=2+2"})
        self.assertTrue(all(row['Пакет'] == "'=SUM(1,2)" for row in rows))
        self.assertTrue(all(row['Изменение'] == 'Изменено' for row in rows))
        first_row = next(row for row in rows if row['NOMER'] == '00000')
        self.assertEqual(float(first_row['Общая площадь до']), 60)
        self.assertEqual(float(first_row['Общая площадь после']), 65)
        self.assertEqual(first_row['Исходная версия'], first['run_id'])
        self.assertEqual(first_row['Новая версия'], second['run_id'])
        self.assertEqual(first_row['KOL_CHL до'], '1.0')
        # The ordinary queue export retains its separate format and scope.
        queue_raw = self.call('/api/export?format=csv&run_id=' + second['run_id'])
        queue_rows = list(csv.DictReader(io.StringIO(queue_raw.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(queue_rows), len(second['records']))
        self.assertIn('Решение', queue_rows[0])

    def test_legacy_comparison_is_normalized_without_rewriting_saved_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = Workspace(root)
            run_id = 'a' * 32
            result_path = workspace.store / 'runs' / run_id / 'result.json'
            result_path.parent.mkdir(parents=True)
            empty = {'values': {'OB_PL': 60}, 'signal_ids': []}
            flagged = {'values': {'OB_PL': 60}, 'signal_ids': ['AREA_ORDER']}
            saved = dict(run_id=run_id, label='Старая версия', summary={}, records=[], comparison={
                'available': True, 'baseline_id': 'b' * 32, 'new_count': 3,
                'changed_count': 0, 'resolved_count': 0, 'unchanged_count': 91, 'missing_count': 0,
                'items': [
                    {'id': 'clean-added', 'change': 'new', 'before': None, 'after': empty},
                    {'id': 'flagged-added', 'change': 'new', 'before': None, 'after': flagged},
                    {'id': 'existing', 'change': 'new', 'before': empty, 'after': flagged},
                ]})
            original_text = json.dumps(saved, ensure_ascii=False)
            result_path.write_text(original_text, encoding='utf-8')
            reports = root / 'reports'
            reports.mkdir()
            benchmark = {'scope': 'current_workspace', 'review_budget': 300}
            (reports / 'workspace_benchmark.json').write_text(json.dumps(benchmark), encoding='utf-8')
            state = workspace.state(run_id)
            comparison = state['comparison']
            self.assertEqual(comparison['added_count'], 2)
            self.assertEqual(comparison['added_with_signals_count'], 1)
            self.assertEqual(comparison['new_count'], 1)
            self.assertEqual(comparison['unchanged_count'], 91)
            self.assertEqual([item['change'] for item in comparison['items']], ['added', 'added', 'new'])
            self.assertEqual(state['workspace_benchmark'], benchmark)
            self.assertEqual(result_path.read_text(encoding='utf-8'), original_text)
