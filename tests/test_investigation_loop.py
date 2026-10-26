import json
import os
import unittest
from unittest.mock import patch

from evidence_detective.engine import EvidenceDetective, compose_verdict
from evidence_detective.models import Claim, Source
from evidence_detective.provenance import bind_evidence
from evidence_detective.reasoning import Reasoner
from test_engine import FakeSearch, result


ENV = {
    'REASONING_ENABLED': '1', 'REASONING_API_KEY': 'offline-test-key',
    'REASONING_BASE_URL': 'https://model.example/v1', 'REASONING_MODEL': 'approved-test-model',
}
PARTIAL = 'The venv module creates virtual environments.'
FULL = 'New in version 3.3: venv creates isolated virtual environments.'


class InvestigationLoopTests(unittest.TestCase):
    def test_failed_counter_search_cannot_be_reported_as_sufficient(self):
        class CounterFailure(FakeSearch):
            def search(self, query, **kwargs):
                if query == '例外':
                    self.queries.append(query)
                    raise RuntimeError('offline failure')
                return super().search(query, **kwargs)
        search = CounterFailure({'直接': [result('Release', 'https://docs.python.org/3.3/doc', FULL)]})
        def model(url, body, key, timeout):
            data = json.loads(body['messages'][1]['content'])
            if 'question' in data:
                output = {'claims': [{'text': 'venv 3.3'}], 'queries': [
                    {'query': '直接', 'purpose': 'direct'}, {'query': '例外', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                output = {'source_reviews': [{'source_id': 'S1', 'claim_id': 'C1', 'applicability': 'applicable',
                    'entity_match': 'match', 'time_match': 'match', 'conditions_match': 'match', 'is_primary': True}]}
            else:
                output = {'evidence': [{'source_id': 'S1', 'claim_id': 'C1', 'quote': FULL, 'quote_origin': 'page_text',
                    'support_degree': 'FULL', 'relation': 'support'}], 'claims': [{'id': 'C1', 'verdict': '支持'}]}
            return {'choices': [{'message': {'content': json.dumps(output)}}]}
        with patch.dict(os.environ, ENV, clear=True), patch('evidence_detective.reasoning.post_json', side_effect=model), patch('evidence_detective.reader.fetch_public', return_value={'http_status': 200, 'url_status': '可访问', 'page_text': FULL}):
            report = EvidenceDetective(search, Reasoner('offline-test-key'), verify=True).investigate('Python venv?')
        self.assertNotEqual(report.stop_code, 'sufficient')
        self.assertTrue(any('反证' in m for c in report.claims for m in c.missing))

    def test_partial_evidence_triggers_targeted_search_and_separate_judges(self):
        search = FakeSearch({
            '直接': [result('Wrong version', 'https://docs.python.org/2/venv.html', FULL),
                     result('Overview', 'https://docs.python.org/3/venv.html', PARTIAL)],
            '例外': [],
            '核查 3.3 引入时间': [result('3.3 release', 'https://docs.python.org/3.3/library/venv.html', 'Release page.')],
        })
        stages = []

        def model(url, body, key, timeout):
            data = json.loads(body['messages'][1]['content'])
            if 'question' in data:
                stages.append('plan')
                output = {'claims': [{'text': 'venv 从 3.3 开始提供隔离环境', 'entity': 'Python venv'}],
                          'queries': [{'query': '直接', 'purpose': 'direct'}, {'query': '例外', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                stages.append('source_review')
                self.assertTrue(all(s.get('page_text') for s in data['sources']))
                output = {'source_reviews': [dict(source_id=s['id'], claim_id='C1',
                    applicability='inapplicable' if '/2/' in s['url'] else 'applicable',
                    entity_match='match', time_match='mismatch' if '/2/' in s['url'] else 'match',
                    conditions_match='match', is_primary=True, reason='核对对象与版本', limitations=[])
                    for s in data['sources']]}
            else:
                stages.append('evidence_review')
                source = next((s for s in data['sources'] if '/3.3/' in s['url']), None)
                quote, degree, sid = (FULL, 'FULL', source['id']) if source else (PARTIAL, 'PARTIAL', 'S2')
                output = {'evidence': [{'source_id': sid, 'claim_id': 'C1', 'quote': quote,
                    'quote_origin': 'page_text', 'support_degree': degree, 'relation': 'support', 'reason': '逐项核对'}],
                    'claims': [{'id': 'C1', 'verdict': '支持', 'conclusion': '材料支持该主张', 'missing': []}],
                    'followup_queries': [] if source else [{'query': '核查 3.3 引入时间', 'claim_ids': ['C1'], 'reason': '缺少引入时间'}]}
            return {'choices': [{'message': {'content': json.dumps(output)}}]}

        def page(url, *args, **kwargs):
            return {'http_status': 200, 'url_status': '可访问', 'page_text': FULL if '/3.3/' in url else PARTIAL}

        with patch.dict(os.environ, ENV, clear=True), patch('evidence_detective.reasoning.post_json', side_effect=model), patch('evidence_detective.reader.fetch_public', side_effect=page):
            report = EvidenceDetective(search, Reasoner('offline-test-key'), verify=True).investigate('Python venv 3.3?')
        self.assertIn('核查 3.3 引入时间', search.queries)
        self.assertEqual(report.verdict, '支持')
        self.assertEqual(report.stop_code, 'sufficient')
        self.assertTrue(report.source_reviews)
        followup = next(e for e in report.events if e.kind == 'followup')
        self.assertTrue(followup.data['gaps'])
        self.assertEqual(stages[0], 'plan')
        self.assertEqual(stages[1:3], ['source_review', 'evidence_review'])
        self.assertTrue(any(e.kind == 'source_review' for e in report.events))
        self.assertTrue(any(e.kind == 'evidence_review' for e in report.events))
        self.assertTrue(all(e.page_verified for e in report.evidence))

    def test_primary_domain_cannot_override_explicit_scope_mismatch(self):
        claim = Claim('C1', 'Python 3.3 的行为')
        source = Source('S1', '旧版', 'https://docs.python.org/2/doc', 'docs.python.org', FULL, '', '未知', 'now', primary=True, page_text=FULL)
        items, _ = bind_evidence([{'source_id': 'S1', 'claim_id': 'C1', 'quote': FULL,
            'relation': 'support', 'support_degree': 'FULL'}], [claim], [source])
        compose_verdict([claim], [source], items, {'claims': [{'id': 'C1', 'verdict': '支持'}]},
                        [{'source_id': 'S1', 'claim_id': 'C1', 'applicability': 'inapplicable', 'is_primary': True}])
        self.assertEqual(claim.verdict, '证据不足')

    def test_page_only_quote_has_exact_origin_and_offsets(self):
        source = Source('S1', '原文', 'https://docs.python.org/doc', 'docs.python.org', 'Summary only.', '', '未知', 'now', page_text=FULL)
        items, rejected = bind_evidence([{'source_id': 'S1', 'claim_id': 'C1', 'quote': FULL,
            'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'support'}], [Claim('C1', '待核查')], [source])
        self.assertEqual(rejected, 0)
        self.assertEqual(items[0].quote_origin, 'page_text')
        self.assertEqual(source.page_text[items[0].quote_start:items[0].quote_end], FULL)
        self.assertTrue(items[0].page_verified)

    def test_conflicting_support_labels_and_fabricated_page_quotes_are_rejected(self):
        source = Source('S1', '原文', 'https://docs.python.org/doc', 'docs.python.org', PARTIAL, '', '未知', 'now', page_text=FULL)
        proposals = [
            {'source_id': 'S1', 'claim_id': 'C1', 'quote': FULL, 'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'counter'},
            {'source_id': 'S1', 'claim_id': 'C1', 'quote': PARTIAL, 'quote_origin': 'page_text', 'support_degree': 'PARTIAL', 'relation': 'support'},
            {'source_id': 'S1', 'claim_id': 'C1', 'quote': 'Fabricated passage with no original.', 'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'support'},
        ]
        items, rejected = bind_evidence(proposals, [Claim('C1', '待核查')], [source])
        self.assertEqual(items, [])
        self.assertEqual(rejected, 3)


if __name__ == '__main__':
    unittest.main()
