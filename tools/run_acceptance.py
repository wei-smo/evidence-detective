"""One real case, saved separately from historical demos. Uses configured services only."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evidence_detective.config import ROOT, load_env, model_key, search_key
from evidence_detective.engine import EvidenceDetective
from evidence_detective.reasoning import Reasoner
from evidence_detective.search import YuanbaoSearch
from evidence_detective.storage import Store, markdown
from tools.validate_demos import validate

QUESTION = 'Python 的 venv 模块是否从 Python 3.3 开始加入标准库，并用于创建隔离的虚拟环境？'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--question', default=QUESTION)
    args = parser.parse_args()
    load_env()
    if not search_key() or not model_key():
        raise SystemExit('元宝或已认可的推理模型配置不完整，请在本地配置密钥。')
    store = Store(uuid.uuid4().hex[:16])
    folder = ROOT / 'artifacts' / 'acceptance' / store.path.name
    folder.mkdir(parents=True)

    def emit(event):
        print(f"{event['elapsed_ms']/1000:6.1f}s {event['kind']}: {event['message']}", flush=True)

    detective = EvidenceDetective(YuanbaoSearch(search_key(), store.audit),
        Reasoner(model_key(), store.audit), verify=True, emit=emit)
    report = detective.investigate(args.question, investigation_id=store.path.name).to_dict()
    store.save(report)
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'report.md').write_text(markdown(report), encoding='utf-8')
    audit = [json.loads(line) for line in (store.path / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = validate(report, audit)
    checks.update({'verdict': report['verdict'], 'stop_code': report['stop_code'],
        'source_review_count': len(report['source_reviews']), 'evidence_count': len(report['evidence']),
        'followup_search_count': sum(q['purpose'] == 'followup' for q in report['queries'])})
    (folder / 'validation.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2), flush=True)
    print(f'Artifacts: {folder}', flush=True)


if __name__ == '__main__':
    main()
