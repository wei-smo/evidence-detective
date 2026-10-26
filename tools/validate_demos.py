"""Validate retrieval provenance against stored raw WSA responses, not model scores."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evidence_detective.search import parse_response, clean_text


def validate(report, audit):
    search_calls = [entry for entry in audit if entry.get('provider') == 'yuanbao']
    reasoning_calls = [entry for entry in audit if entry.get('provider') == 'reasoning']
    returned = {}
    request_ids = set()
    for call in search_calls:
        if call.get('response'):
            for result in parse_response(call['response']):
                returned.setdefault(result.url, []).append(result)
                request_ids.add(result.request_id)
    checks = []
    for source in report['sources']:
        candidates = returned.get(source['url'], [])
        assert candidates, f"Source not returned by WSA: {source['id']}"
        observations = source.get('search_observations', [])
        if observations:
            legacy_text, merged_text = '', ''
            for observation in observations:
                assert observation['query_id'] in source['query_ids'], source['id']
                assert any(clean_text(candidate.content or candidate.passage) == observation['text']
                           and clean_text(candidate.passage) == observation['passage']
                           for candidate in candidates), f"Observation not returned by WSA: {source['id']}"
                part = observation['text']
                if part and part not in legacy_text:
                    legacy_text = (legacy_text + '\n' + part).strip()
                for part in (observation['text'], observation['passage']):
                    if part and part not in merged_text:
                        merged_text = (merged_text + '\n' + part).strip()
            assert source['text'] in (legacy_text, merged_text), source['id']
        else:
            assert any(clean_text(candidate.content or candidate.passage) == source['text'] for candidate in candidates), source['id']
        assert source['request_id'] in request_ids, source['id']
        assert source['digest'] == hashlib.sha256(source['text'].encode()).hexdigest(), source['id']
    checks.append('all source URLs/text/digests/request IDs match stored real WSA responses')
    sources = {s['id']: s for s in report['sources']}
    claims = {c['id']: c for c in report['claims']}
    pages = {entry['source_id']: entry for entry in audit if entry.get('provider') == 'source_reader'}
    for evidence in report['evidence'] + report.get('reviews', []):
        assert evidence['claim_id'] in claims
        assert evidence['source_id'] in sources
        source = sources[evidence['source_id']]
        field = 'page_text' if evidence.get('quote_origin') == 'page_text' else 'text'
        content = source.get(field, '')
        assert evidence['quote'] in content
        if 'quote_start' in evidence:
            assert content[evidence['quote_start']:evidence['quote_end']] == evidence['quote']
            assert evidence['content_digest'] == hashlib.sha256(content.encode()).hexdigest()
        if field == 'page_text' or evidence.get('page_verified'):
            page = pages.get(source['id'])
            # Legacy reports did not save separate reader audits.
            if report.get('schema_version') == '2.0':
                assert page and page['page_text'] == source['page_text']
                assert page['page_digest'] == source['page_digest'] == hashlib.sha256(source['page_text'].encode()).hexdigest()
            assert evidence['quote'] in source['page_text']
    checks.append('no invented source or quotation in evidence/reviews')
    assert len(search_calls) == len(report['queries'])
    assert len(search_calls) <= 6
    assert any(q['purpose'] == 'counter' for q in report['queries'])
    assert [e['sequence'] for e in report['events']] == list(range(1, len(report['events']) + 1))
    assert report['events'][-1]['kind'] == 'stopped'
    checks.append('query count, counter phase, event sequence and stop event verified')
    if not reasoning_calls:
        assert report['verdict'] in ('证据不足', '未知')
        assert report['analysis_mode'].startswith('规则降级')
        checks.append('model-disabled run makes no affirmative automatic factual verdict')
    if report.get('schema_version') == '2.0':
        assert all(e['data']['investigation_id'] == report['id'] for e in report['events'])
        for item in report['evidence']:
            supplied = []
            for call in reasoning_calls:
                if call.get('stage') != 'evidence_review' or not call.get('request'):
                    continue
                data = json.loads(call['request']['messages'][1]['content'])
                supplied += [s for s in data.get('sources', []) if s['id'] == item['source_id']]
            field = 'page_text' if item['quote_origin'] == 'page_text' else 'text'
            assert any(item['quote'] in s.get(field, '') for s in supplied)
        if report['evidence']:
            stages = [call['stage'] for call in reasoning_calls]
            assert 'source_review' in stages and 'evidence_review' in stages
            assert stages.index('source_review') < stages.index('evidence_review')
        checks.append('separate judges, original-page audit, quote origin/offsets and supplied model context verified')
    return {'id': report['id'], 'search_calls': len(search_calls), 'reasoning_calls': len(reasoning_calls),
            'source_count': len(report['sources']), 'elapsed_ms': report['elapsed_ms'],
            'first_result_ms': report['first_result_ms'], 'checks': checks,
            'semantic_accuracy': 'NOT AUTOMATICALLY VERIFIED; requires independent human review'}


def main():
    output = {}
    for name in ('A', 'B', 'C'):
        report = json.loads((ROOT / 'demos' / f'case-{name}.json').read_text(encoding='utf-8'))
        file = ROOT / 'data' / 'investigations' / report['id'] / 'audit.jsonl'
        audit = [json.loads(line) for line in file.read_text(encoding='utf-8').splitlines()]
        output[name] = validate(report, audit)
    folder = ROOT / 'artifacts'
    folder.mkdir(exist_ok=True)
    (folder / 'provenance-checks.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
