"""Regressions for the handoff gaps; external calls use offline boundary fixtures."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evidence_detective.engine import EvidenceDetective, original_excerpt
from evidence_detective.reasoning import Reasoner
from evidence_detective import transport
from evidence_detective.models import Source, Report, Event, Claim
from evidence_detective.provenance import regroup_sources
from evidence_detective.provenance import normalize
from evidence_detective.search import SearchResult
from evidence_detective.search import parse_response
from tools.validate_demos import validate
from test_backend_repair import Search, Reasoner as FixtureReasoner, FACT, hit
from test_investigation_loop import ENV


class TakeoverRegressions(unittest.TestCase):
    def test_short_full_citation_preserves_checked_adjacent_title_context(self):
        short = 'venv --- Creating virtual environments. New in version 3.3.'
        original = 'The Python Standard Library > Software Packaging > '+short
        class VersionReview(FixtureReasoner):
            def plan(self,question):
                output=super().plan(question)
                output['claims']=[{'text':'venv was added to the Python standard library in version 3.3.'}]
                return output
            def analyze(self, claims, sources, reviews):
                self.contexts.append(sources)
                return {'claims':[{'id':'C1','verdict':'支持','missing':[]}],
                        'evidence':[{'claim_id':'C1','source_id':'S1','quote':short,
                          'quote_origin':'page_text','support_degree':'FULL','relation':'support'}]}
        url='https://docs.python.org/3/library/venv.html'
        with patch('evidence_detective.reader.fetch_public',return_value=dict(
                http_status=200,url_status='可访问',page_text=original,final_url=url)):
            report=EvidenceDetective(Search({'direct':[hit(url,short)]}),VersionReview(),verify=True).investigate('Python venv?')
        self.assertIn('The Python Standard Library',report.evidence[0].quote)
        self.assertIn(short,report.evidence[0].quote)
        self.assertEqual(original[report.evidence[0].quote_start:report.evidence[0].quote_end],report.evidence[0].quote)

    def test_version_question_excerpt_keeps_the_actual_version_context(self):
        purpose = 'The venv module creates virtual environments.'
        original = 'The Python Standard Library. venv. New in version 3.3. '+purpose
        original += ' Unrelated navigation.' * 600
        hint = 'Create an isolated environment with this command: python -m venv.'
        original += hint
        source = Source('S1','venv','https://docs.python.org/doc','docs.python.org',hint,hint,'未知','now',page_text=original)
        excerpt = original_excerpt(source, [Claim('C1','venv was added to the standard library in Python 3.3',time_scope='Python 3.3')])
        self.assertIn('New in version 3.3.', excerpt)
        self.assertIn('The Python Standard Library.', excerpt)
        self.assertIn(excerpt, original)

    def test_counter_or_followup_originals_keep_a_share_of_the_read_budget(self):
        counter = [hit('https://archive.example/doc', FACT)]
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if 'archive.' in url else '')
        for prefix, question in [('https://blocked.example/', 'Does Delta support PDF export?'),
                                 ('https://docs.python.org/blocked/', 'Does Python Delta support PDF export?')]:
            with self.subTest(prefix=prefix), patch('evidence_detective.reader.fetch_public', side_effect=page):
                direct = [hit(prefix+str(i), 'Search lead '+str(i)) for i in range(8)]
                report = EvidenceDetective(Search({'direct': direct, 'counter': counter}), FixtureReasoner(),
                                           verify=True, max_searches=2).investigate(question)
            self.assertTrue(report.answer_items, 'the first eight broken pages must not starve new original sources')
            self.assertLessEqual(sum(bool(s.fetched_at) for s in report.sources), 8)

    def test_single_open_candidate_extraction_can_find_the_seventh_read_page(self):
        class OpenQuestion(FixtureReasoner):
            def __init__(self):
                super().__init__()
                self.candidate_calls = 0
            def plan(self, question):
                output = super().plan(question)
                output['question_type'] = 'open_question'
                return output
            def candidate_facts(self, question, sources):
                self.candidate_calls += 1
                source = next((s for s in sources if FACT in s['page_text'] or FACT in s['text']), None)
                return {'claims': [{'text': FACT, 'source_id': source['id'], 'quote': FACT}] if source else []}
        hits = [hit('https://catalog.example/'+str(i), 'Unrelated catalogue '+str(i)) for i in range(6)]
        hits.append(hit('https://vendor.example/manual', FACT))
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if '/manual' in url else 'Unrelated catalogue page '+url)
        with patch('evidence_detective.reader.fetch_public', side_effect=page):
            reasoner = OpenQuestion()
            report = EvidenceDetective(Search({'direct': hits}), reasoner,
                verify=True).investigate('Delta的用途是什么？')
        self.assertEqual(reasoner.candidate_calls, 1)
        self.assertTrue(report.answer_items)
        self.assertTrue(any(e.source_id == 'S7' and e.page_verified for e in report.evidence))

    def test_only_applicable_new_counterevidence_can_challenge_an_earlier_fact(self):
        opposite = 'Project Delta does not support exporting reports as PDF files.'
        class CounterReview(FixtureReasoner):
            def __init__(self, applicable):
                super().__init__()
                self.applicable = applicable
            def review_sources(self, claims, sources):
                output = super().review_sources(claims, sources)
                for row in output['source_reviews']:
                    if row['source_id'] == 'S3' and not self.applicable:
                        row.update(applicability='inapplicable', time_match='mismatch')
                return output
            def analyze(self, claims, sources, reviews):
                if any(opposite in s['page_text'] for s in sources):
                    self.contexts.append(sources)
                    return {'claims': [{'id': 'C1', 'verdict': '反驳', 'missing': []}],
                        'evidence': [{'claim_id': 'C1', 'source_id': 'S3', 'quote': opposite,
                            'quote_origin': 'page_text', 'support_degree': 'CONTRADICT', 'relation': 'counter'}]}
                return super().analyze(claims, sources, reviews)
        hits = [hit('https://vendor.example/manual', FACT), hit('https://other.example/a', 'Unrelated A'),
                hit('https://counter.example/b', opposite), hit('https://other.example/c', 'Unrelated C')]
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if '/manual' in url else opposite if 'counter.' in url else 'Unrelated '+url)
        for applicable in (False, True):
            with self.subTest(applicable=applicable), patch('evidence_detective.reader.fetch_public', side_effect=page):
                report = EvidenceDetective(Search({'direct': hits}), CounterReview(applicable),
                    verify=True).investigate('Does Delta support PDF export?')
            if applicable:
                self.assertEqual(report.answer_items, [], 'a real applicable contradiction must retain the conflict')
                self.assertNotEqual(report.stop_code, 'sufficient')
            else:
                self.assertTrue(report.answer_items, 'a different-version source cannot retract the checked fact')
                self.assertEqual(report.claims[0].verdict, '支持')

    def test_merged_observations_are_validated_against_each_raw_search_response(self):
        sources, audit = [], []
        for i, passage in enumerate(['First original passage.', 'A later original exception.'], 1):
            raw = {'Response': {'RequestId': 'raw-'+str(i), 'Pages': [{
                'title': 'Manual', 'url': 'https://vendor.example/doc',
                'content': 'Same generic content.', 'passage': passage}]}}
            audit.append({'provider': 'yuanbao', 'response': raw})
            normalize(parse_response(raw), sources, 'Q'+str(i), 'Delta')
        report = Report('fixture', 'Delta', 'now', verdict='证据不足', sources=sources,
            queries=[{'id': 'Q1', 'purpose': 'direct'}, {'id': 'Q2', 'purpose': 'counter'}],
            events=[Event(1, 'stopped', 'done', 'now', 0, {'investigation_id': 'fixture'})]).to_dict()
        self.assertEqual(validate(report, audit)['source_count'], 1)
        report['sources'][0]['search_observations'][1]['passage'] = 'A forged passage never returned.'
        with self.assertRaises(AssertionError):
            validate(report, audit)

    def test_official_deepseek_keeps_budget_and_returns_json_without_hidden_thinking_unless_explicit(self):
        requests = []
        def service(url, body, key, timeout):
            requests.append(body)
            return {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}
        for extra, expected in [({}, 'none'), ({'REASONING_EFFORT': 'none'}, 'none'),
                                ({'REASONING_EFFORT': 'low'}, 'low'), ({'REASONING_EFFORT': 'high'}, 'high')]:
            with patch.dict(os.environ, {**ENV, 'REASONING_BASE_URL': 'https://api.deepseek.com',
                    'REASONING_MODEL': 'deepseek-flash', **extra}, clear=True), \
                    patch('evidence_detective.reasoning.post_json', side_effect=service):
                Reasoner('offline-key').plan('公开问题')
            self.assertEqual(requests[-1].get('reasoning_effort'), expected)
            self.assertLessEqual(requests[-1]['max_tokens'], 3200)
        with patch.dict(os.environ, ENV, clear=True), \
                patch('evidence_detective.reasoning.post_json', side_effect=service):
            Reasoner('offline-key').plan('公开问题')
        self.assertNotIn('reasoning_effort', requests[-1])

    def test_empty_source_review_is_a_service_failure(self):
        class EmptyReview(FixtureReasoner):
            def review_sources(self, claims, sources):
                return {'source_reviews': []}
        url = 'https://vendor.example/doc'
        with patch('evidence_detective.reader.fetch_public', return_value=dict(
                http_status=200, url_status='可访问', page_text=FACT, final_url=url)):
            report = EvidenceDetective(Search({'direct': [hit(url, FACT)]}),
                EmptyReview(), verify=True).investigate('Does Delta support PDF export?')
        self.assertTrue(any(f['stage'] == 'review_sources' for f in report.service_failures))
        self.assertEqual(report.answer_items, [])
        self.assertEqual(report.stop_code, 'service_incomplete')

    def test_relevant_original_after_long_navigation_is_supplied_to_review(self):
        url = 'https://vendor.example/doc'
        original = ('Navigation and unrelated site footer. ' * 400) + FACT + (' More navigation.' * 200)
        with patch('evidence_detective.reader.fetch_public', return_value=dict(
                http_status=200, url_status='可访问', page_text=original, final_url=url)):
            reasoner = FixtureReasoner()
            report = EvidenceDetective(Search({'direct': [hit(url, FACT)]}), reasoner,
                verify=True).investigate('Does Delta support PDF export?')
        self.assertTrue(any(FACT in s['page_text'] for batch in reasoner.contexts for s in batch))
        self.assertTrue(report.answer_items)
        for batch in reasoner.contexts:
            for source in batch:
                self.assertIn(source['page_text'], original, 'each excerpt must be a contiguous original slice')

    def test_failed_later_source_review_preserves_checked_answer(self):
        class LaterFailure(FixtureReasoner):
            def review_sources(self, claims, sources):
                if hasattr(self, 'reviewed_once'):
                    raise transport.ServiceError('simulated source review timeout')
                self.reviewed_once = True
                return super().review_sources(claims, sources)
            def analyze(self, claims, sources, reviews):
                if self.contexts:
                    raise transport.ServiceError('simulated evidence review timeout')
                return super().analyze(claims, sources, reviews)
        url = 'https://vendor.example/doc'
        with patch('evidence_detective.reader.fetch_public', return_value=dict(
                http_status=200, url_status='可访问', page_text=FACT, final_url=url)):
            report = EvidenceDetective(Search({'direct': [hit(url, FACT)],
                'counter': [hit(url, 'New information that requires renewed source review.')]}),
                LaterFailure(), verify=True).investigate('Does Delta support PDF export?')
        self.assertTrue(report.answer_items)
        self.assertEqual(report.source_reviews[0]['applicability'], 'applicable')
        self.assertEqual(report.stop_code, 'service_incomplete')

    def test_unrelated_later_batch_cannot_erase_earlier_checked_fact(self):
        hits = [hit('https://vendor.example/manual', FACT)] + [
            hit('https://catalog.example/'+str(i), 'Unrelated catalogue '+str(i)) for i in range(7)]
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if '/manual' in url else 'Unrelated catalogue page '+url)
        with patch('evidence_detective.reader.fetch_public', side_effect=page):
            report = EvidenceDetective(Search({'direct': hits}), FixtureReasoner(),
                                       verify=True).investigate('Does Delta support PDF export?')
        self.assertTrue(report.answer_items)
        self.assertEqual(report.claims[0].verdict, '支持')

    def test_failed_direct_search_is_service_incomplete_even_with_counter_fact(self):
        class DirectFailure(Search):
            def search(self, query, **kwargs):
                if query == 'direct':
                    raise transport.ServiceError('simulated direct search outage')
                return super().search(query, **kwargs)
        url = 'https://vendor.example/doc'
        with patch('evidence_detective.reader.fetch_public', return_value=dict(
                http_status=200, url_status='可访问', page_text=FACT, final_url=url)):
            report = EvidenceDetective(DirectFailure({'counter': [hit(url, FACT)]}),
                FixtureReasoner(), verify=True).investigate('Does Delta support PDF export?')
        self.assertTrue(report.answer_items)
        self.assertEqual(report.stop_code, 'service_incomplete')
        self.assertTrue(any(f['stage'] == 'direct_search' for f in report.service_failures))

    def test_new_passage_with_unchanged_content_remains_traceable(self):
        sources = []
        for query_id, passage in [('Q1', 'First passage about a public feature.'),
                                  ('Q2', 'New exception requiring an active connection.')]:
            normalize([SearchResult('Manual', 'https://vendor.example/doc',
                content='Unchanged generic overview content.', passage=passage)], sources, query_id, 'Delta')
        self.assertIn('New exception requiring an active connection.', sources[0].text)
        self.assertTrue(any(o['query_id'] == 'Q2' and o['passage'].startswith('New exception')
                            for o in sources[0].search_observations))

    def test_tls_channel_switch_uses_remaining_single_call_deadline(self):
        with patch('shutil.which', return_value='curl.exe'), \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=35, stdout=b'')), \
                patch('time.monotonic', side_effect=[10, 12]), \
                patch.object(transport, '_python_post', return_value={}) as fallback:
            transport.post_json('https://model.example/v1/chat/completions', {}, 'offline-key', timeout=20)
        self.assertEqual(fallback.call_args.args[3], 18)

    def test_duplicate_source_links_merge_transitively(self):
        sources = [Source('S1', 'a', 'https://a.example/a', 'a.example', '', '', '未知', 'now',
                          independence_group='a.example', final_url='https://origin.example/A', page_text='body X', fetched_at='now'),
                   Source('S2', 'b', 'https://b.example/b', 'b.example', '', '', '未知', 'now',
                          independence_group='b.example', final_url='https://origin.example/B', page_text='body Y', fetched_at='now'),
                   Source('S3', 'c', 'https://c.example/c', 'c.example', '', '', '未知', 'now',
                          independence_group='c.example', final_url='https://origin.example/A', page_text='body Y', fetched_at='now')]
        regroup_sources(sources)
        self.assertEqual(len({s.independence_group for s in sources}), 1)

    def test_truncated_model_response_fails_once_without_raising_token_limit(self):
        requests = []
        def service(url, body, key, timeout):
            requests.append(body)
            return {'choices': [{'finish_reason': 'length', 'message': {'content': ''}}]}
        with patch.dict(os.environ, ENV, clear=True), \
                patch('evidence_detective.reasoning.post_json', side_effect=service):
            with self.assertRaises(transport.ServiceError):
                Reasoner('offline-test-key').plan('张飞是谁')
        self.assertEqual(len(requests), 1, 'truncation is a failed call, never a hidden retry')
        self.assertLessEqual(requests[0]['max_tokens'], 3200)

    def test_curl_failure_after_send_cannot_resend_a_paid_request(self):
        for code, raw in [(28, b'\n__HTTP_STATUS__000'),
                          (18, b'partial\n__HTTP_STATUS__200'),
                          (35, b'partial\n__HTTP_STATUS__200')]:
            with self.subTest(code=code), patch('shutil.which', return_value='curl.exe'), \
                    patch('subprocess.run', return_value=SimpleNamespace(returncode=code, stdout=raw)), \
                    patch.object(transport, '_python_post', return_value={'unexpected': 'resend'}) as fallback:
                with self.assertRaises(transport.ServiceError):
                    transport.post_json('https://model.example/v1/chat/completions', {}, 'offline-key')
                self.assertEqual(fallback.call_count, 0)

    def test_seventh_page_reaches_review_when_search_has_no_new_urls(self):
        hits = [hit('https://catalog.example/' + str(i), 'Unrelated catalogue '+str(i)) for i in range(6)]
        hits.append(hit('https://vendor.example/manual', FACT))
        def page(url, *a, **k):
            return dict(http_status=200, url_status='可访问', final_url=url,
                        page_text=FACT if '/manual' in url else 'Unrelated catalogue page '+url)
        for budget in (2, 6):
            with self.subTest(budget=budget), patch('evidence_detective.reader.fetch_public', side_effect=page):
                reasoner = FixtureReasoner()
                report = EvidenceDetective(Search({'direct': hits}), reasoner,
                    verify=True, max_searches=budget).investigate('Does Delta support PDF export?')
                self.assertTrue(any(s['id'] == 'S7' for batch in reasoner.contexts for s in batch))
                self.assertTrue(report.answer_items)
                self.assertEqual(len(report.queries), 2, 'read the existing backlog before spending another search')
                self.assertLessEqual(sum(bool(s.fetched_at) for s in report.sources), 8)

    def test_followup_does_not_search_for_internal_diagnostics(self):
        class Incomplete(FixtureReasoner):
            def analyze(self, claims, sources, reviews):
                return {'claims': [{'id': 'C1', 'verdict': '证据不足',
                    'missing': ['缺少对象、时间与适用条件均匹配的来源判断。']}], 'evidence': []}
        report = EvidenceDetective(Search({'direct': [hit('https://vendor.example/doc', FACT)]}),
                                   Incomplete()).investigate('Does Delta support PDF export?')
        followups = [q['query'] for q in report.queries if q['purpose'] == 'followup']
        self.assertTrue(followups)
        self.assertTrue(all('未知' not in q and '来源判断' not in q and '缺少' not in q for q in followups))


if __name__ == '__main__':
    unittest.main()
