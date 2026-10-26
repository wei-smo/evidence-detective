import socket
import unittest
from unittest.mock import patch
from evidence_detective.reader import fetch_public


class ReaderSafetyTests(unittest.TestCase):
    def test_public_hostname_resolving_to_private_ip_is_not_fetched(self):
        with patch('evidence_detective.reader.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        ]), patch('evidence_detective.reader.socket.create_connection') as connect:
            result = fetch_public('https://public.example/doc')
            self.assertEqual(result['url_status'], '安全策略拒绝')
            connect.assert_not_called()

    def test_mixed_public_private_dns_answers_rejected(self):
        with patch('evidence_detective.reader.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))
        ]):
            self.assertEqual(fetch_public('https://public.example/')['url_status'], '安全策略拒绝')


if __name__ == '__main__':
    unittest.main()
