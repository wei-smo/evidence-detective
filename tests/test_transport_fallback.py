"""The curl-to-Python fallback must stay byte-identical and never leak the key."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from evidence_detective import transport


class TransportFallbackTests(unittest.TestCase):
    def test_curl_failure_falls_back_to_python_channel(self):
        reply = {"choices": [{"message": {"content": "{}"}}]}
        with mock.patch('shutil.which', return_value='curl.exe'), \
                mock.patch('subprocess.run', return_value=SimpleNamespace(
                    returncode=35, stdout=b'', stderr=b'SEC_E_NO_CREDENTIALS')), \
                mock.patch.object(transport, '_python_post', return_value=reply) as fallback:
            result = transport.post_json('https://api.example/v1/chat/completions',
                                         {'model': 'm'}, 'sk-test')
        self.assertEqual(result, reply)
        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(fallback.call_args.args[:3], ('https://api.example/v1/chat/completions',
                                                     {'model': 'm'}, 'sk-test'))
        self.assertGreater(fallback.call_args.args[3], 0)
        self.assertLessEqual(fallback.call_args.args[3], 20)

    def test_both_channels_failing_keeps_both_diagnostics_and_hides_the_key(self):
        with mock.patch('shutil.which', return_value='curl.exe'), \
                mock.patch('subprocess.run', return_value=SimpleNamespace(
                    returncode=35, stdout=b'', stderr=b'tls')), \
                mock.patch.object(transport, '_python_post', side_effect=transport.ServiceError(
                    '网络请求失败（URLError）。')):
            with self.assertRaises(transport.ServiceError) as caught:
                transport.post_json('https://api.example/v1/chat/completions',
                                    {'model': 'm'}, 'sk-secret-value')
        message = str(caught.exception)
        self.assertIn('curl 35', message)
        self.assertIn('URLError', message)
        self.assertNotIn('sk-secret-value', message)

    def test_python_channel_posts_the_same_json_and_header(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return json.dumps({'ok': True}).encode('utf-8')

        def fake_open(_self, request, timeout=None):
            captured['url'] = request.full_url
            captured['body'] = request.data
            captured['headers'] = {k.lower(): v for k, v in request.header_items()}
            captured['timeout'] = timeout
            return FakeResponse()

        with mock.patch('urllib.request.OpenerDirector.open', fake_open):
            result = transport._python_post('https://api.example/v1/chat/completions',
                                            {'model': 'm', 'messages': []}, 'sk-test', 20)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(json.loads(captured['body'].decode('utf-8')),
                         {'model': 'm', 'messages': []})
        self.assertEqual(captured['headers']['authorization'], 'Bearer sk-test')
        self.assertEqual(captured['headers']['content-type'],
                         'application/json; charset=utf-8')
        self.assertEqual(captured['timeout'], 20)


if __name__ == '__main__':
    unittest.main()
