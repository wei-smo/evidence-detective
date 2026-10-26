import json
import tempfile
import unittest
from pathlib import Path

from evidence_detective.evaluation import evaluate_report, evaluate_suite, load_benchmark


def sample_report():
    return {
        "id": "r1",
        "question": "软件是否支持离线导出？",
        "verdict": "部分成立",
        "claims": [{"id": "C1", "verdict": "部分成立", "evidence_ids": ["E1", "E2"]}],
        "sources": [
            {"id": "S1", "url": "https://vendor.example/features", "domain": "vendor.example",
             "text": "支持离线导出 PDF。", "retrieved_at": "2026-10-24T00:00:00Z"},
            {"id": "S2", "url": "https://vendor.example/release", "domain": "vendor.example",
             "text": "旧版本不支持离线导出。", "retrieved_at": "2026-10-24T00:00:00Z"},
            {"id": "S3", "url": "https://noise.example/post", "domain": "noise.example",
             "text": "一篇无关内容。", "retrieved_at": "2026-10-24T00:00:00Z"},
        ],
        "evidence": [
            {"id": "E1", "claim_id": "C1", "source_id": "S1", "relation": "support",
             "quote": "支持离线导出 PDF。"},
            {"id": "E2", "claim_id": "C1", "source_id": "S2", "relation": "counter",
             "quote": "旧版本不支持离线导出。"},
        ],
        "queries": [
            {"id": "Q1", "query": "软件 离线导出 官方", "purpose": "direct", "status": "ok"},
            {"id": "Q2", "query": "软件 离线导出 旧版本 限制", "purpose": "counter", "status": "ok"},
        ],
        "elapsed_ms": 1400,
    }


class EvaluationTests(unittest.TestCase):
    def test_report_metrics_distinguish_retrieval_from_evidence_use(self):
        item = {
            "task_id": "t1",
            "question": "软件是否支持离线导出？",
            "expected_verdict": "部分成立",
            "gold_sources": [
                {"url": "https://vendor.example/features", "label": "positive", "required": True},
                {"url": "https://vendor.example/release", "label": "negative", "required": True},
            ],
            "gold_claim_ids": ["C1"],
        }
        result = evaluate_report(item, sample_report(), model="test-model")
        self.assertEqual(result["retrieval"]["required_source_recall"], 1.0)
        self.assertEqual(result["retrieval"]["retrieved_source_precision"], 2 / 3)
        self.assertEqual(result["evidence"]["gold_source_use_rate"], 1.0)
        self.assertEqual(result["answer"]["verdict_correct"], 1.0)
        self.assertEqual(result["efficiency"]["search_calls"], 2)

    def test_unretrieved_gold_source_is_not_counted_as_used(self):
        report = sample_report()
        report["sources"] = [report["sources"][0], report["sources"][2]]
        report["evidence"] = [report["evidence"][0]]
        report["claims"][0]["evidence_ids"] = ["E1"]
        item = {
            "task_id": "t2", "question": report["question"], "expected_verdict": "部分成立",
            "gold_sources": [
                {"url": "https://vendor.example/features", "required": True},
                {"url": "https://vendor.example/release", "required": True},
            ], "gold_claim_ids": ["C1"],
        }
        result = evaluate_report(item, report, model="test-model")
        self.assertEqual(result["retrieval"]["required_source_recall"], 0.5)
        self.assertEqual(result["evidence"]["gold_source_use_rate"], 0.5)

    def test_suite_aggregates_quality_and_efficiency_by_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            benchmark_path = Path(tmp) / "benchmark.json"
            benchmark_path.write_text(json.dumps({"version": "1", "items": [{
                "task_id": "t1", "question": "q", "expected_verdict": "部分成立",
                "gold_sources": [{"url": "https://vendor.example/features", "required": True}],
                "gold_claim_ids": ["C1"],
            }]}), encoding="utf-8")
            benchmark = load_benchmark(benchmark_path)
            reports = [{"model": "m1", "report": sample_report()}]
            summary = evaluate_suite(benchmark, reports)
            self.assertEqual(summary["runs"], 1)
            self.assertIn("m1", summary["by_model"])
            self.assertIn("required_source_recall", summary["by_model"]["m1"])


if __name__ == "__main__":
    unittest.main()
