import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from evidence_detective import server
from evidence_detective import storage
from test_engine import FakeSearch, result


class HTTPTests(unittest.TestCase):
    def test_history_distinguishes_unfinished_service_from_completed_unknown(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'DATA', Path(directory)):
            for identifier, stop_code in [('1111111111111111', 'service_incomplete'), ('2222222222222222', 'no_new_evidence')]:
                folder = Path(directory) / identifier
                folder.mkdir()
                report = {'id':identifier, 'question':'张飞是谁？', 'verdict':'证据不足',
                          'started_at':'now', 'elapsed_ms':100, 'stop_code':stop_code,
                          'question_type':'open_question', 'answer_items':[]}
                (folder/'report.json').write_text(json.dumps(report), encoding='utf-8')
            status, raw, _ = self.request('GET', '/api/history')
        self.assertEqual(status, 200)
        rows = {r['id']:r for r in json.loads(raw)}
        self.assertTrue(rows['1111111111111111'].get('service_incomplete'))
        self.assertFalse(rows['2222222222222222'].get('service_incomplete'))

    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.worker.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.http.server_port, timeout=4)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_local_configuration_files_are_never_served(self):
        status, _, _ = self.request('GET', '/.env.local')
        self.assertEqual(status, 404)
        status, _, _ = self.request('GET', '/data/investigations')
        self.assertEqual(status, 404)

    def test_health_reports_disabled_reasoning_without_leaking_secrets(self):
        with patch.dict('os.environ', {}, clear=True):
            status, raw, _ = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)['reasoning_configured'])
        self.assertNotIn('api_key', json.loads(raw))

    def test_api_requires_access_token_when_configured(self):
        with patch.object(server, 'ACCESS_TOKEN', 'test-access-token-12345'):
            status, _, _ = self.request('GET', '/api/history')
            self.assertEqual(status, 401)
            with patch.object(server, 'DATA', Path('does-not-exist-for-http-test')):
                status, _, _ = self.request('GET', '/api/history', headers={'Authorization': 'Bearer test-access-token-12345'})
                self.assertEqual(status, 200)

    def test_cross_origin_search_rejected_before_issuing_queries(self):
        status, _, _ = self.request('POST', '/api/investigations', '{}', {'Origin': 'https://attacker.example'})
        self.assertEqual(status, 403)

    def test_invalid_body_rejected_without_external_requests(self):
        for body in ('null', '[]', '{}', '{broken', '{"question": 42}'):
            status, _, _ = self.request('POST', '/api/investigations', body)
            self.assertEqual(status, 400)

    def test_html_has_content_security_policy(self):
        status, raw, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'/app.js', raw)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])

    def test_game_font_is_served_as_font_but_other_files_stay_private(self):
        status, raw, headers = self.request('GET', '/assets/fonts/fusion-pixel-12px-proportional-zh_hans.otf.woff2')
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b'wOF2'))
        self.assertIn('font/woff2', headers['Content-Type'])
        self.assertEqual(self.request('GET', '/assets/fonts/../../.env.local')[0], 404)

    def test_local_api_rejects_untrusted_host_header(self):
        status, _, _ = self.request('GET', '/api/history', headers={'Host': 'attacker.example'})
        self.assertEqual(status, 403)

    def test_v2_stream_report_and_export_share_id_and_independent_judgments(self):
        quote = 'New in version 3.3: venv creates isolated virtual environments.'
        search = FakeSearch({'direct': [result('Python release', 'https://docs.python.org/3.3/venv.html', quote)], 'counter': []})
        env = {'YUANBAO_SEARCH_API_KEY': 'offline-search-key', 'REASONING_ENABLED': '1',
               'REASONING_API_KEY': 'offline-model-key', 'REASONING_BASE_URL': 'https://model.example/v1',
               'REASONING_MODEL': 'approved-test-model'}

        def model(url, body, key, timeout):
            data = json.loads(body['messages'][1]['content'])
            if 'question' in data:
                output = {'claims': [{'text': 'venv first appeared in Python 3.3'}], 'queries': [
                    {'query': 'direct', 'purpose': 'direct'}, {'query': 'counter', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                output = {'source_reviews': [{'source_id': 'S1', 'claim_id': 'C1',
                    'applicability': 'applicable', 'entity_match': 'match', 'time_match': 'match',
                    'conditions_match': 'match', 'is_primary': True}]}
            else:
                output = {'evidence': [{'source_id': 'S1', 'claim_id': 'C1', 'quote': quote,
                    'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'support'}],
                    'claims': [{'id': 'C1', 'verdict': '支持', 'missing': []}]}
            return {'choices': [{'message': {'content': json.dumps(output)}}]}

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', env, clear=True), \
             patch.object(server, 'JOBS', {}), patch.object(server, 'DATA', Path(directory)), \
             patch.object(storage, 'DATA', Path(directory)), \
             patch.object(server, 'YuanbaoSearch', return_value=search), \
             patch('evidence_detective.reasoning.post_json', side_effect=model), \
             patch('evidence_detective.reader.fetch_public', return_value={'http_status': 200, 'url_status': '可访问', 'page_text': quote}):
            status, raw, _ = self.request('POST', '/api/investigations', json.dumps({'question': 'Python venv 3.3?'}))
            self.assertEqual(status, 202)
            identifier = json.loads(raw)['id']
            status, raw, headers = self.request('GET', f'/api/investigations/{identifier}/events')
            self.assertEqual(status, 200)
            self.assertIn('text/event-stream', headers['Content-Type'])
            frames = [block.splitlines() for block in raw.decode().split('\n\n') if block.startswith('event:')]
            events = [json.loads(frame[1][6:]) for frame in frames if frame[0] == 'event: progress']
            report = json.loads(frames[-1][1][6:])['report']
            self.assertEqual(report['id'], identifier)
            self.assertTrue(all(e['data']['investigation_id'] == identifier for e in events))
            self.assertEqual([e['sequence'] for e in events], list(range(1, len(events) + 1)))
            self.assertEqual(events[-1]['kind'], 'stopped')
            self.assertEqual(report['stop_code'], 'sufficient')
            kinds = [e['kind'] for e in events]
            self.assertLess(kinds.index('source_review'), kinds.index('evidence_review'))
            self.assertEqual(report['evidence'][0]['quote'], quote)
            self.assertTrue(report['evidence'][0]['page_verified'])
            status, saved, _ = self.request('GET', f'/api/investigations/{identifier}')
            self.assertEqual(json.loads(saved), report)
            status, exported, _ = self.request('GET', f'/api/investigations/{identifier}/export')
            self.assertEqual(status, 200)
            self.assertIn('来源适用性（独立判断）', exported.decode())
            self.assertIn('原文支持度与逐字引用', exported.decode())


if __name__ == '__main__':
    unittest.main()
