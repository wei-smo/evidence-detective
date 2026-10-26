import unittest
from evidence_detective.reviews import add_review, review_conclusion


def report():
    return {'id': 'f' * 16, 'verdict': '证据不足', 'claims': [{'id': 'C1', 'text': '公开主张'}],
            'sources': [{'id': 'S1', 'text': '这是一段来自真实检索响应的逐字材料，需要由操作者核查语义。',
                         'page_text': '', 'url': 'https://example.org/doc'}]}


class ReviewTests(unittest.TestCase):
    def test_review_keeps_automatic_verdict_and_records_exact_provenance(self):
        original = report()
        updated = add_review(original, {'claim_id': 'C1', 'source_id': 'S1',
            'quote': '这是一段来自真实检索响应的逐字材料', 'relation': 'context',
            'reason': '测试记录，不构成事实背书。', 'reviewer': '测试操作者'})
        self.assertEqual(updated['verdict'], '证据不足')
        self.assertNotIn('reviews', original)
        self.assertEqual(updated['reviews'][0]['source_id'], 'S1')
        self.assertEqual(updated['reviews'][0]['reviewer'], '测试操作者')
        self.assertFalse(updated['reviews'][0]['page_verified'])

    def test_cannot_save_a_rewritten_or_invented_quote(self):
        with self.assertRaises(ValueError):
            add_review(report(), {'claim_id': 'C1', 'source_id': 'S1', 'quote': '修改过的内容不是检索原文。',
                'relation': 'support', 'reason': '理由', 'reviewer': '测试者'})

    def test_manual_conclusion_requires_own_claim_evidence(self):
        with self.assertRaises(ValueError):
            review_conclusion(report(), {'claim_id': 'C1', 'verdict': '支持', 'conclusion': '确实成立',
                'review_ids': ['R999'], 'reviewer': '测试者'})

    def test_manual_conclusion_stays_separate_and_binds_review_ids(self):
        updated = add_review(report(), {'claim_id': 'C1', 'source_id': 'S1',
            'quote': '这是一段来自真实检索响应的逐字材料', 'relation': 'context',
            'reason': '仅作为待核查背景，不证明主张。', 'reviewer': '测试者'})
        updated = review_conclusion(updated, {'claim_id': 'C1', 'verdict': '证据不足',
            'conclusion': '材料不足，不能证明对象相同。', 'review_ids': ['R1'], 'reviewer': '测试者'})
        self.assertEqual(updated['verdict'], '证据不足')
        self.assertEqual(updated['manual_conclusions'][0]['review_ids'], ['R1'])
        self.assertIn('操作者', updated['manual_conclusions'][0]['label'])


if __name__ == '__main__':
    unittest.main()
