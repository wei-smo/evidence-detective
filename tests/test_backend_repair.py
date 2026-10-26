"""Offline behavioral regressions for the 2026-10-25 backend repair."""
import unittest
from unittest.mock import patch

from evidence_detective.engine import EvidenceDetective, fallback_plan
from evidence_detective.search import SearchResult


FACT = 'Project Delta supports exporting reports as PDF files.'
OLD = 'General release index with no feature information.'
NEW = 'Correction: PDF export requires an active network connection.'


def hit(url, text):
    return SearchResult('Fixture', url, passage=text, content=text)


class Search:
    def __init__(self, mapping, fail_counter=False):
        self.mapping, self.fail_counter = mapping, fail_counter

    def search(self, query, **kwargs):
        if query == 'counter' and self.fail_counter:
            raise RuntimeError('offline simulated search outage')
        return self.mapping.get(query, [])


class Reasoner:
    def __init__(self, *, primary=True, need_new=False):
        self.primary, self.need_new = primary, need_new
        self.contexts = []

    def plan(self, question):
        return {'claims': [{'text': FACT}], 'queries': [
            {'query': 'direct', 'purpose': 'direct'},
            {'query': 'counter', 'purpose': 'counter'}]}

    def review_sources(self, claims, sources):
        return {'source_reviews': [dict(claim_id=c['id'], source_id=s['id'],
            applicability='applicable', entity_match='match', time_match='match',
            conditions_match='match', is_primary=self.primary)
            for s in sources for c in claims]}

    def analyze(self, claims, sources, reviews):
        self.contexts.append(sources)
        usable = [s for s in sources if FACT in s['page_text']
                  and (not self.need_new or NEW in s['text'])]
        return {'claims': [{'id': 'C1', 'verdict': '支持' if usable else '证据不足',
            'conclusion': 'Project Delta also supports offline Linux export.',
            'missing': [] if usable else ['Missing relevant material']}],
            'evidence': [dict(claim_id='C1', source_id=s['id'], quote=FACT,
                quote_origin='page_text', support_degree='FULL', relation='support') for s in usable],
            'followup_queries': [{'query': 'followup', 'claim_ids': ['C1']}]}


class BackendRepairTests(unittest.TestCase):
    def run_engine(self, mapping, reasoner=None, page=None, fail_counter=False, **kwargs):
        reasoner = reasoner or Reasoner()
        page = page or (lambda url, *a, **k: dict(http_status=200, url_status='可访问',
                                                page_text=FACT, final_url=url))
        with patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')), \
                patch('evidence_detective.reader.fetch_public', side_effect=page):
            report = EvidenceDetective(Search(mapping, fail_counter), reasoner,
                                       verify=True, **kwargs).investigate('Does Delta support PDF export?')
        return report, reasoner

    def test_final_url_or_identical_body_cannot_supply_two_independent_sources(self):
        mapping = {'direct': [hit('https://mirror-a.example/a', OLD),
                              hit('https://mirror-b.example/b', 'Technical operation manual and format reference.') ]}
        for same_url in (True, False):
            with self.subTest(same_url=same_url):
                def page(url, *a, **k):
                    body = FACT + (url if same_url else '')
                    return dict(http_status=200, url_status='可访问', page_text=body,
                                final_url='https://origin.example/doc' if same_url else url)
                report, _ = self.run_engine(mapping, Reasoner(primary=False), page)
                self.assertEqual(len({s.independence_group for s in report.sources}), 1)
                self.assertNotEqual(report.stop_code, 'sufficient')
                self.assertEqual(report.answer_items, [])

    def test_same_url_new_passage_keeps_observations_and_reaches_analysis(self):
        url = 'https://vendor.example/doc'
        report, reasoner = self.run_engine({'direct': [hit(url, OLD)],
            'counter': [hit(url, NEW)]}, Reasoner(need_new=True))
        self.assertTrue(any(NEW in s['text'] for batch in reasoner.contexts for s in batch))
        source = report.sources[0]
        self.assertIn(OLD, source.text)
        self.assertIn(NEW, source.text)
        observations = source.search_observations
        self.assertTrue(any(o['query_id'] == 'Q1' and o['text'] == OLD for o in observations))
        self.assertTrue(any(o['query_id'] == 'Q2' and o['text'] == NEW for o in observations))
        self.assertTrue(report.answer_items)

    def test_backlog_is_read_even_without_new_urls_and_after_search_budget(self):
        hits = [hit('https://catalog.example/'+str(i), 'Unrelated catalogue '+str(i)) for i in range(4)]
        hits.append(hit('https://vendor.example/manual', FACT))
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if '/manual' in url else 'Unrelated catalogue page '+url)
        for budget in (2, 6):
            with self.subTest(budget=budget):
                report, reasoner = self.run_engine({'direct': hits, 'followup': hits},
                                                   page=page, max_searches=budget)
                self.assertTrue(report.sources[4].fetched_at)
                self.assertTrue(any(s['id'] == 'S5' for batch in reasoner.contexts for s in batch))
                self.assertTrue(report.answer_items)
                self.assertLessEqual(sum(bool(s.fetched_at) for s in report.sources), 8)

    def test_conclusion_cannot_introduce_unchecked_facts(self):
        report, _ = self.run_engine({'direct': [hit('https://vendor.example/doc', FACT)]})
        self.assertEqual(report.claims[0].conclusion, FACT)
        self.assertNotIn('offline Linux', report.summary)

    def test_counter_service_failure_preserves_fact_but_not_completion(self):
        report, _ = self.run_engine({'direct': [hit('https://vendor.example/doc', FACT)]}, fail_counter=True)
        self.assertTrue(report.answer_items)
        self.assertEqual(report.claims[0].verdict, '支持')
        self.assertNotEqual(report.stop_code, 'sufficient')
        self.assertTrue(report.service_failures)

    def test_open_fallback_for_identity_purpose_and_difference(self):
        for question in ('张飞是谁？', 'venv的用途', 'TCP和UDP的区别是什么？'):
            with self.subTest(question=question):
                report = EvidenceDetective(Search({})).investigate(question)
                self.assertEqual(report.question_type, 'open_question')
                self.assertEqual(report.answer_items, [])
        queries = ' '.join(q['query'] for q in fallback_plan('张飞是谁？')['queries'])
        self.assertNotIn('不支持', queries)
        self.assertNotIn('版本', queries)
        self.assertIn('生平', queries)


if __name__ == '__main__':
    unittest.main()
