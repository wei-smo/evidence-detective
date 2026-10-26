"""Regression scenarios through the engine; only external model/page boundaries are faked."""
import json
import os
import unittest
from unittest.mock import patch

from evidence_detective.engine import EvidenceDetective
from evidence_detective.reasoning import Reasoner
from evidence_detective.transport import ServiceError
from test_engine import FakeSearch, result
from test_investigation_loop import ENV, FULL


PURPOSE = 'The venv module creates isolated virtual environments.'


class AnswerWorkflowTests(unittest.TestCase):
    def run_case(self, question, *, open_question=False, timeout_later=False,
                 expanded=False, invalid_candidate=False, malformed_followup=False, empty_later=False, invalid_review=None):
        search = FakeSearch({
            '直接': [result('venv docs', 'https://docs.python.org/3/library/venv.html', PURPOSE)],
            '例外': [result('Release notes', 'https://docs.python.org/3.3/whatsnew/3.3.html', FULL)],
        })
        reviews = 0
        candidate_calls = 0

        def model(url, body, key, timeout):
            nonlocal reviews, candidate_calls
            data = json.loads(body['messages'][1]['content'])
            if data.get('task') == 'candidate_facts':
                candidate_calls += 1
                output = {'claims': [{'text': 'venv 用于创建隔离的虚拟环境', 'entity': 'Python venv',
                    'source_id': 'S1', 'quote': 'invented candidate with no source' if invalid_candidate else PURPOSE}]}
            elif 'question' in data:
                text = 'venv 截至今天每个版本始终默认隔离' if expanded else 'venv 用于创建隔离的虚拟环境'
                output = {'question_type': 'open_question' if open_question else 'verification',
                    'claims': [{'text': text, 'entity': 'Python venv',
                                'original_span': '用于创建隔离的虚拟环境'}],
                    'queries': [{'query': '直接', 'purpose': 'direct'}, {'query': '例外', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                output = {'source_reviews': [dict(source_id=s['id'], claim_id=c['id'],
                    applicability='applicable', entity_match='match', time_match='match',
                    conditions_match='match', is_primary=True, reason='对象、条件和身份匹配')
                    for s in data['sources'] for c in data['claims']]}
            else:
                reviews += 1
                if timeout_later and reviews > 1:
                    raise ServiceError('curl 28 timeout')
                if empty_later and reviews > 1:
                    return {'choices': [{'message': {'content': '{}'}}]}
                if invalid_review is not None and reviews > 1:
                    return {'choices': [{'message': {'content': json.dumps(invalid_review)}}]}
                output = {'evidence': [{'claim_id': 'C1', 'source_id': 'S1', 'quote': PURPOSE,
                    'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'support'}],
                    'claims': [{'id': 'C1', 'verdict': '支持', 'conclusion': 'venv 用于创建隔离的虚拟环境', 'missing': []}]}
                if malformed_followup:
                    output['evidence'][0]['support_degree'] = 'PARTIAL'
                    output['claims'][0]['missing'] = ['缺少适用条件']
                    output['followup_queries'] = [{'query': '不能执行的无绑定补查', 'claim_ids': None},
                                                 {'query': '不能执行的字符串绑定', 'claim_ids': 'C1'}]
                    output['conflicts'] = [{'claim_id': 'C1', 'source_ids': None}]
            return {'choices': [{'message': {'content': json.dumps(output)}}]}

        with patch.dict(os.environ, ENV, clear=True), patch('evidence_detective.reasoning.post_json', side_effect=model), patch(
                'evidence_detective.reader.fetch_public', return_value={
                    'http_status': 200, 'url_status': '可访问', 'page_text': PURPOSE + '\n' + FULL}):
            report = EvidenceDetective(search, Reasoner('offline-test-key'), verify=True).investigate(question)
        return report, candidate_calls

    def test_open_question_produces_verified_answer_with_bound_evidence(self):
        report, count = self.run_case('Python 的 venv 是做什么的？', open_question=True)
        self.assertEqual(report.question_type, 'open_question')
        self.assertEqual(count, 1)
        self.assertTrue(report.answer_items)
        answer = report.answer_items[0]
        self.assertEqual(answer['claim_id'], 'C1')
        self.assertTrue(answer['evidence_ids'])
        self.assertEqual(answer['source_ids'], ['S1'])
        self.assertTrue(any(e.kind == 'candidate_claims' for e in report.events))
        self.assertEqual(report.claims[0].original_question, report.question)

    def test_fabricated_candidate_is_not_presented_as_verified_answer(self):
        report, count = self.run_case('Python 的 venv 是做什么的？', open_question=True, invalid_candidate=True)
        self.assertEqual(count, 1)
        self.assertEqual(report.answer_items, [])
        self.assertNotEqual(report.stop_code, 'sufficient')

    def test_latest_review_timeout_preserves_earlier_verified_answer(self):
        report, _ = self.run_case('Python venv 是否用于创建隔离的虚拟环境？', timeout_later=True)
        self.assertTrue(report.answer_items, 'A later service failure must not erase the earlier evidence-backed fact')
        self.assertTrue(any(e.kind == 'error' and e.data.get('stage') == 'analyze' for e in report.events))
        self.assertNotEqual(report.stop_code, 'sufficient')
        self.assertTrue(report.service_failures)
        self.assertTrue(report.answer_items[0]['evidence_ids'])

    def test_empty_later_review_preserves_fact_but_cannot_close_investigation(self):
        report, _ = self.run_case('Python venv 是否用于创建隔离的虚拟环境？', empty_later=True)
        self.assertTrue(report.answer_items)
        self.assertNotEqual(report.stop_code, 'sufficient')
        self.assertTrue(any(f['stage'] == 'analyze' and not f['resolved'] for f in report.service_failures))

    def test_later_review_with_no_valid_claim_result_is_incomplete(self):
        for output in ({'claims': [], 'evidence': []}, {'claims': [{'id': 'invented', 'verdict': '支持'}], 'evidence': []}, {'claims': [{'id': 'C1'}], 'evidence': []}):
            with self.subTest(output=output):
                report, _ = self.run_case('Python venv 是否用于创建隔离的虚拟环境？', invalid_review=output)
                self.assertTrue(report.answer_items)
                self.assertNotEqual(report.stop_code, 'sufficient')
                self.assertTrue(report.service_failures)

    def test_planner_scope_drift_is_rejected_without_an_extra_model_call(self):
        report, count = self.run_case('Python venv 是否用于创建隔离的虚拟环境？', expanded=True)
        self.assertEqual(count, 0)
        self.assertTrue(all('截至今天' not in c.text and '每个版本' not in c.text for c in report.claims))
        self.assertTrue(any(e.kind == 'validation' and e.data.get('stage') == 'plan' for e in report.events))

    def test_malformed_optional_followup_cannot_crash_or_bypass_gap_binding(self):
        report, _ = self.run_case('Python venv 是否用于创建隔离的虚拟环境？', malformed_followup=True)
        self.assertEqual(report.answer_items, [])
        self.assertTrue(all('不能执行' not in q['query'] for q in report.queries))
        self.assertTrue(any(q['purpose'] == 'followup' and q['claim_ids'] == ['C1'] for q in report.queries))

    def test_later_round_for_another_claim_keeps_previous_verified_fact(self):
        search = FakeSearch({'直接': [result('Docs', 'https://docs.python.org/venv', PURPOSE)],
                             '例外': [result('Notes', 'https://docs.python.org/release', FULL)]})
        analyzed = 0
        def model(url, body, key, timeout):
            nonlocal analyzed
            data = json.loads(body['messages'][1]['content'])
            if 'question' in data:
                output = {'claims': [{'text': 'venv 创建隔离环境'}, {'text': 'venv 在3.3引入'}],
                          'queries': [{'query': '直接', 'purpose': 'direct'}, {'query': '例外', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                output = {'source_reviews': [dict(claim_id=c['id'], source_id=s['id'], applicability='applicable',
                    entity_match='match', time_match='match', conditions_match='match', is_primary=True)
                    for c in data['claims'] for s in data['sources']]}
            else:
                analyzed += 1
                cid, sid, quote = ('C1', 'S1', PURPOSE) if analyzed == 1 else ('C2', 'S2', FULL)
                output = {'evidence': [{'claim_id': cid, 'source_id': sid, 'quote': quote,
                          'quote_origin': 'page_text', 'support_degree': 'FULL', 'relation': 'support'}],
                          'claims': [{'id': cid, 'verdict': '支持', 'missing': []}]}
            return {'choices': [{'message': {'content': json.dumps(output)}}]}
        with patch.dict(os.environ, ENV, clear=True), patch('evidence_detective.reasoning.post_json', side_effect=model), patch(
                'evidence_detective.reader.fetch_public', return_value={
                    'http_status': 200, 'page_text': PURPOSE + FULL, 'url_status': '可访问'}):
            report = EvidenceDetective(search, Reasoner('offline-test-key'), verify=True).investigate('Python venv 引入版本和用途')
        self.assertEqual({a['claim_id'] for a in report.answer_items}, {'C1', 'C2'})
        self.assertEqual(report.stop_code, 'sufficient')

    def test_successful_reassessment_can_withdraw_a_previously_supported_fact(self):
        search = FakeSearch({'直接': [result('Docs', 'https://docs.python.org/venv', PURPOSE)],
                             '例外': [result('Notes', 'https://docs.python.org/release', FULL)]})
        analyzed = 0
        def model(url, body, key, timeout):
            nonlocal analyzed
            data = json.loads(body['messages'][1]['content'])
            if 'question' in data:
                output = {'claims': [{'text': 'venv 创建隔离环境'}],
                          'queries': [{'query': '直接', 'purpose': 'direct'}, {'query': '例外', 'purpose': 'counter'}]}
            elif 'source_reviews' not in data:
                output = {'source_reviews': [dict(claim_id='C1', source_id=s['id'], applicability='applicable',
                    entity_match='match', time_match='match', conditions_match='match', is_primary=True) for s in data['sources']]}
            else:
                analyzed += 1
                output = {'evidence': [{'claim_id': 'C1', 'source_id': 'S1', 'quote': PURPOSE,
                    'quote_origin': 'page_text', 'support_degree': 'FULL' if analyzed == 1 else 'PARTIAL', 'relation': 'support'}],
                    'claims': [{'id': 'C1', 'verdict': '支持' if analyzed == 1 else '证据不足', 'missing': [] if analyzed == 1 else ['条件未覆盖']}]}
            return {'choices': [{'message': {'content': json.dumps(output)}}]}
        with patch.dict(os.environ, ENV, clear=True), patch('evidence_detective.reasoning.post_json', side_effect=model), patch(
                'evidence_detective.reader.fetch_public', return_value={
                    'http_status': 200, 'page_text': PURPOSE + FULL, 'url_status': '可访问'}):
            report = EvidenceDetective(search, Reasoner('offline-test-key'), verify=True).investigate('Python venv 用途')
        self.assertEqual(report.answer_items, [])
        self.assertNotEqual(report.stop_code, 'sufficient')


if __name__ == '__main__':
    unittest.main()
