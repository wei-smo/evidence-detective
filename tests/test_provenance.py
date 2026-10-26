import json
import unittest
from evidence_detective.models import Claim, Source
from evidence_detective.provenance import bind_evidence
from evidence_detective.search import parse_response, public_url
from evidence_detective.engine import compose_verdict


class ProvenanceTests(unittest.TestCase):
    def test_actual_api_string_pages_are_parsed_and_bad_urls_rejected(self):
        payload = {"Response": {"RequestId": "real-request", "Pages": [
            json.dumps({"title": "<b>文档</b>", "url": "https://docs.python.org/3/", "passage": "正文", "score": None}),
            {"url": "javascript:alert(1)"}, "invalid json", None]}}
        results = parse_response(payload)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].request_id, "real-request")
        self.assertEqual(results[0].title, "文档")

    def test_quote_and_source_hallucinations_are_rejected(self):
        sources = [Source("S1", "文档", "https://example.com/", "example.com",
                          "Python venv 模块用于创建虚拟环境。", "", "未知", "now")]
        proposals = [
            {"source_id": "S999", "claim_id": "C1", "quote": "伪造的证据不存在", "relation": "support"},
            {"source_id": "S1", "claim_id": "C1", "quote": "Python 支持神奇的未知功能。", "relation": "support"},
            {"source_id": "S1", "claim_id": "C1", "quote": "Python venv 模块用于创建虚拟环境。", "relation": "support"}]
        accepted, rejected = bind_evidence(proposals, [Claim("C1", "venv 创建虚拟环境")], sources)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, 2)

    def test_no_key_or_no_model_cannot_create_semantic_verdict(self):
        claim = Claim("C1", "目标支持功能")
        compose_verdict([claim], [], [], {"claims": [{"id": "C1", "verdict": "支持", "conclusion": "无证据也支持"}]})
        self.assertEqual(claim.verdict, "未知")
        self.assertNotIn("无证据也支持", claim.conclusion)

    def test_private_network_urls_blocked(self):
        for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/", "file:///etc/passwd", "https://user:pass@example.com/"):
            self.assertEqual(public_url(url), "")


if __name__ == "__main__":
    unittest.main()
