"""Human adjudication is explicit, append-only, and never impersonates a model."""
from copy import deepcopy

from .search import clean_text, now

RELATIONS = {'support', 'counter', 'context'}
VERDICTS = {'支持', '反驳', '部分成立', '证据不足', '未知'}


def text_field(data, name, minimum=1, maximum=1500):
    value = data.get(name)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{name} 字段无效或长度不符合要求。')
    return clean_text(value)


def add_review(report, data):
    claim_id, source_id = data.get('claim_id'), data.get('source_id')
    source = next((s for s in report['sources'] if s['id'] == source_id), None)
    if not source or claim_id not in {c['id'] for c in report['claims']}:
        raise ValueError('只能复核本次调查中已有的主张和真实检索来源。')
    quote = data.get('quote')
    if not isinstance(quote, str) or not 10 <= len(quote) <= 2000 or quote not in source['text']:
        raise ValueError('引用必须是该来源返回内容中 10～2000 字符的连续逐字片段，不可改写。')
    if data.get('relation') not in RELATIONS:
        raise ValueError('请选择支持、反对或背景关系。')
    result = deepcopy(report)
    reviews = result.setdefault('reviews', [])
    if len(reviews) >= 100:
        raise ValueError('本报告最多保留 100 条复核记录。')
    reviews.append({'id': f'R{len(reviews)+1}', 'claim_id': claim_id, 'source_id': source_id,
                    'quote': quote, 'relation': data['relation'],
                    'reason': text_field(data, 'reason', 4, 1500),
                    'reviewer': text_field(data, 'reviewer', 1, 60),
                    'at': now(), 'quote_verified': True,
                    'page_verified': bool(source.get('page_text') and quote in source['page_text']),
                    'label': '操作者标注 · 语义未经独立审核'})
    return result


def review_conclusion(report, data):
    claim_id = data.get('claim_id')
    if claim_id not in {c['id'] for c in report['claims']} or data.get('verdict') not in VERDICTS:
        raise ValueError('主张或结论状态无效。')
    ids = data.get('review_ids')
    reviews = {r['id']: r for r in report.get('reviews', []) if r['claim_id'] == claim_id}
    if not isinstance(ids, list) or not ids or any(not isinstance(r, str) or r not in reviews for r in ids):
        raise ValueError('结论必须绑定本主张下已保存的复核记录。')
    relations = {reviews[r]['relation'] for r in ids}
    if data['verdict'] == '支持' and 'support' not in relations or data['verdict'] == '反驳' and 'counter' not in relations:
        raise ValueError('确定性结论需要同方向的证据标注。')
    result = deepcopy(report)
    conclusions = result.setdefault('manual_conclusions', [])
    if len(conclusions) >= 100:
        raise ValueError('本报告人工结论记录已达到上限。')
    conclusions.append({'id': f'M{len(conclusions)+1}', 'claim_id': claim_id,
        'verdict': data['verdict'], 'conclusion': text_field(data, 'conclusion', 4, 2000),
        'review_ids': list(dict.fromkeys(ids)), 'reviewer': text_field(data, 'reviewer', 1, 60),
        'at': now(), 'label': '操作者判断 · 不改变自动结论 · 待独立复核'})
    return result
