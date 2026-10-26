import threading
import unittest

from evidence_detective.engine import EvidenceDetective


class SearchBoundary:
    def __init__(self):
        self.queries = []

    def search(self, query, *, limit=10):
        self.queries.append(query)
        return []


class BudgetTests(unittest.TestCase):
    def test_cancelled_job_does_not_issue_requests(self):
        cancelled = threading.Event()
        cancelled.set()
        search = SearchBoundary()
        report = EvidenceDetective(search, cancelled=cancelled).investigate('Python 官方资料')
        self.assertEqual(search.queries, [])
        self.assertIn('取消', report.stop_reason)

    def test_search_count_never_exceeds_budget(self):
        search = SearchBoundary()
        report = EvidenceDetective(search, max_searches=1).investigate('Python 官方资料')
        self.assertEqual(len(search.queries), 1)
        self.assertIn('上限', report.stop_reason)

    def test_model_disabled_run_is_explicitly_not_adaptive_reasoning(self):
        search = SearchBoundary()
        report = EvidenceDetective(search).investigate('某产品公开功能')
        self.assertEqual(report.analysis_mode, '规则降级 · 不作语义事实判断')
        self.assertTrue(any('模板' in item for item in report.limitations))


if __name__ == '__main__':
    unittest.main()
