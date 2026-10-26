import unittest

from evidence_detective.engine import compose_verdict
from evidence_detective.models import Claim, Evidence, Source
from evidence_detective.provenance import normalize
from evidence_detective.search import SearchResult


def source(identifier, host, primary=False):
    return Source(identifier, '公开材料', f'https://{host}/doc', host, '逐字材料示例，不保证事实语义。', '',
                  '未知', 'now', primary=primary, independence_group=host)


class VerdictRulesTests(unittest.TestCase):
    def test_opposing_source_does_not_make_single_support_source_sufficient(self):
        claim = Claim('C1', '产品支持该功能')
        sources = [source('S1', 'a.example'), source('S2', 'b.example')]
        evidence = [Evidence('E1', 'C1', 'S1', '逐字材料示例，不保证事实语义。', 'support', ''),
                    Evidence('E2', 'C1', 'S2', '逐字材料示例，不保证事实语义。', 'counter', '')]
        compose_verdict([claim], sources, evidence, {'claims': [{'id': 'C1', 'verdict': '支持', 'conclusion': '直接支持'}]})
        self.assertEqual(claim.verdict, '证据不足')

    def test_unexplained_opposition_prevents_one_sided_verdict(self):
        claim = Claim('C1', '产品支持该功能')
        sources = [source('S1', 'vendor.example', True), source('S2', 'b.example')]
        evidence = [Evidence('E1', 'C1', 'S1', '逐字材料示例，不保证事实语义。', 'support', ''),
                    Evidence('E2', 'C1', 'S2', '逐字材料示例，不保证事实语义。', 'counter', '')]
        compose_verdict([claim], sources, evidence, {'claims': [{'id': 'C1', 'verdict': '支持', 'conclusion': '忽略反证'}]})
        self.assertEqual(claim.verdict, '证据不足')

    def test_malformed_model_fields_degrade_without_crashing(self):
        claim = Claim('C1', '待核查')
        compose_verdict([claim], [], [], {'claims': [{'id': 'C1', 'missing': None}]})
        self.assertEqual(claim.verdict, '未知')

    def test_unknown_content_from_same_publisher_not_counted_as_repost(self):
        sources = []
        normalize([SearchResult('A', 'https://a.example/a'), SearchResult('B', 'https://b.example/b')], sources, 'Q1', '公开问题')
        self.assertFalse(any(s.is_repost for s in sources))

    def test_old_version_is_not_a_counterexample_to_the_new_version(self):
        claim = Claim('C1', '产品3.4版本支持PDF导出')
        sources = [source('S1', 'vendor.example', True), source('S2', 'vendor.example', True)]
        evidence = [Evidence('E1', 'C1', 'S1', '3.4版本新增PDF导出功能。', 'support', '', page_verified=True, support_degree='FULL'),
                    Evidence('E2', 'C1', 'S2', '3.3版本没有PDF导出功能。', 'counter', '', page_verified=True, support_degree='CONTRADICT')]
        reviews = [dict(claim_id='C1', source_id='S1', applicability='applicable', is_primary=True),
                   dict(claim_id='C1', source_id='S2', applicability='inapplicable', is_primary=True)]
        compose_verdict([claim], sources, evidence, {'claims': [{'id': 'C1', 'verdict': '支持', 'missing': []}]}, reviews)
        self.assertEqual(claim.verdict, '支持')

    def test_partial_quote_never_proves_unmentioned_offline_and_platform_conditions(self):
        claim = Claim('C1', '3.4支持Windows和Linux离线PDF导出')
        sources = [source('S1', 'vendor.example', True)]
        evidence = [Evidence('E1', 'C1', 'S1', '3.4版本新增PDF导出功能。', 'support', '', page_verified=True, support_degree='PARTIAL')]
        reviews = [dict(claim_id='C1', source_id='S1', applicability='applicable', is_primary=True)]
        compose_verdict([claim], sources, evidence, {'claims': [{'id': 'C1', 'verdict': '支持', 'missing': ['离线和平台未覆盖']}]}, reviews)
        self.assertEqual(claim.verdict, '证据不足')
        self.assertIn('离线和平台未覆盖', claim.missing)


if __name__ == '__main__':
    unittest.main()
