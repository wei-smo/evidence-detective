import unittest

from evidence_detective.engine import EvidenceDetective, SearchResult


class FakeSearch:
    def __init__(self, results_by_query=None, error=None):
        self.results_by_query = results_by_query or {}
        self.error = error
        self.queries = []

    def search(self, query, *, limit=10, site=None):
        self.queries.append(query)
        if self.error:
            raise RuntimeError(self.error)
        return list(self.results_by_query.get(query, []))[:limit]


def result(title, url, passage, *, date="2026-10-20", site="example.com"):
    return SearchResult(
        title=title,
        url=url,
        passage=passage,
        content=passage,
        date=date,
        site=site,
        score=0.9,
    )


class EngineBehaviorTests(unittest.TestCase):
    def test_investigation_runs_direct_counter_and_conflict_queries(self):
        search = FakeSearch(
            {
                "星河软件 支持离线导出 官方": [
                    result("官方功能说明", "https://vendor.example/features", "支持离线导出 PDF。")
                ],
                "星河软件 不支持离线导出 限制 问题": [
                    result("发行说明", "https://vendor.example/release", "旧版本不支持离线导出。", date="2025-01-01")
                ],
                "星河软件 离线导出 版本 2026": [
                    result("版本说明", "https://vendor.example/version", "2026 版本支持离线导出。")
                ],
            }
        )

        report = EvidenceDetective(search).investigate("星河软件支持离线导出吗？")

        self.assertGreaterEqual(len(search.queries), 3)
        self.assertIn(report.verdict, {"部分成立", "支持", "证据不足"})
        self.assertTrue(report.claims)
        self.assertTrue(report.sources)
        self.assertTrue(report.events)
        self.assertTrue(any("反证" in query or "限制" in query for query in search.queries))

    def test_reposts_are_not_counted_as_independent_sources(self):
        search = FakeSearch(
            {
                "某活动 仍可购票 官方": [
                    result("报道 A", "https://news-a.example/a", "官方页面显示仍可购票。", site="news-a.example"),
                    result("报道 B", "https://news-b.example/b", "官方页面显示仍可购票。来源：news-a.example", site="news-b.example"),
                ],
                "某活动 售罄 取消 延期 限制": [],
            }
        )

        report = EvidenceDetective(search).investigate("某活动现在还可以购票吗？")

        self.assertEqual(report.independent_source_count, 1)
        self.assertTrue(any(source.is_repost for source in report.sources))

    def test_no_evidence_is_unknown_not_contradicted(self):
        search = FakeSearch({})

        report = EvidenceDetective(search).investigate("蓝鲸展览在月球上是否已经开放？")

        self.assertIn(report.verdict, {"未知", "证据不足"})
        self.assertFalse(report.claims[0].evidence_ids)
        self.assertTrue(report.next_action)

    def test_search_error_returns_graceful_report_without_fake_sources(self):
        report = EvidenceDetective(FakeSearch(error="network down")).investigate("某个公开事实")

        self.assertEqual(report.sources, [])
        self.assertIn(report.verdict, {"未知", "证据不足"})
        self.assertTrue(any(event.kind == "error" for event in report.events))
        self.assertNotIn("伪造", report.summary)


if __name__ == "__main__":
    unittest.main()
