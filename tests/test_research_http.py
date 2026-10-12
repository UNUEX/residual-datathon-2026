"""HTTP integration tests; model transport is deliberately mocked here."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.model_provider import ProviderError
from src.serve import make_server


class ResearchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.server = make_server(0, Path(self.folder.name))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.folder.cleanup()

    def call(self, path, payload=None, headers=None):
        headers = dict(headers or {})
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(self.base + path, body, headers), timeout=10) as response:
            raw = response.read()
            return (json.loads(raw) if response.headers['Content-Type'].startswith('application/json') else raw,
                    dict(response.headers))

    def test_new_workspace_demo_plan_report_and_download(self):
        state, _ = self.call('/api/research/state')
        self.assertEqual(state['datasets'], [])
        self.assertEqual(state['reports'], [])
        demo, _ = self.call('/api/research/demo', {})
        self.assertTrue(demo['is_demo'])
        candidate = dict(status='ready', reason='Медиана для всех домохозяйств.', year=2024,
                         metrics=['total_area'], aggregation='median', grouping='all')
        with patch.object(self.server.research.provider, 'propose', return_value=candidate):
            plan, _ = self.call('/api/research/plan', {'dataset_id': demo['id'],
                'question': 'Медиана общей площади жилья для всех домохозяйств.'})
        self.assertEqual(self.call('/api/research/state')[0]['reports'], [])
        report, _ = self.call('/api/research/execute', {'plan_id': plan['id']})
        self.assertGreater(report['table'][0]['values']['total_area'], 0)
        self.assertEqual(self.call('/api/research/report?id=' + report['id'])[0], report)
        for format, mime in [('csv', 'text/csv'), ('html', 'text/html'), ('python', 'text/x-python')]:
            raw, headers = self.call('/api/research/export?id=' + report['id'] + '&format=' + format)
            self.assertGreater(len(raw), 50)
            self.assertIn(mime, headers['Content-Type'])
            self.assertIn('attachment;', headers['Content-Disposition'])
        self.assertEqual(len(self.call('/api/research/state')[0]['reports']), 1)

    def test_provider_error_is_explicit_and_does_not_create_fake_report(self):
        with patch.object(self.server.research.provider, 'probe', side_effect=ProviderError('Модель недоступна.')):
            with self.assertRaises(HTTPError) as caught:
                self.call('/api/research/probe', {})
            self.assertEqual(caught.exception.code, 502)
            self.assertEqual(json.loads(caught.exception.read())['error'], 'Модель недоступна.')
        self.assertEqual(self.call('/api/research/state')[0]['reports'], [])

    def test_cross_origin_and_arbitrary_source_paths_rejected(self):
        for endpoint, body, headers, expected in [
            ('/api/research/demo', {}, {'Origin': 'https://elsewhere.example'}, 403),
            ('/api/research/report?id=../../secrets', None, {}, 400),
            ('/api/research/execute', {'plan_id': 'a' * 32, 'code': 'anything'}, {}, 400),
        ]:
            with self.assertRaises(HTTPError) as caught:
                self.call(endpoint, body, headers)
            self.assertEqual(caught.exception.code, expected)


if __name__ == '__main__':
    unittest.main()
