"""Run real Yuanbao searches. Costs search/model quota; never fixture results."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evidence_detective.config import load_env, search_key, model_key, ROOT
from evidence_detective.engine import EvidenceDetective
from evidence_detective.reasoning import Reasoner
from evidence_detective.search import YuanbaoSearch
from evidence_detective.storage import Store, markdown

CASES = {
    "A": "Python 的 venv 模块是否从 Python 3.3 开始加入标准库，并用于创建隔离的虚拟环境？",
    "B": "我看到资料说 SQLite 是公有领域，也有资料说使用 SQLite 要买许可证。这两种说法是否冲突，商业使用是否必须付费？",
    "C": "截至 2026 年 10 月 24 日，蓝鲸月面图书馆「静海一号分馆」是否已正式开放，门票是否为 80 元？请只依据公开资料核实。",
}


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=list(CASES), action="append")
    args = parser.parse_args()
    if not search_key():
        raise SystemExit("Configure Yuanbao SERVICE API key first.")
    folder = ROOT / "demos"
    folder.mkdir(exist_ok=True)
    for name in args.case or CASES:
        import uuid
        store = Store(uuid.uuid4().hex[:16])
        def emit(e):
            print(f"{name} {e['elapsed_ms']/1000:5.1f}s {e['kind']}: {e['message']}", flush=True)
        engine = EvidenceDetective(YuanbaoSearch(search_key(), store.audit),
            Reasoner(model_key(), store.audit) if model_key() else None, emit=emit, verify=True)
        report = engine.investigate(CASES[name], investigation_id=store.path.name).to_dict()
        store.save(report)
        (folder / f"case-{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / f"case-{name}.md").write_text(markdown(report), encoding="utf-8")
        print(json.dumps({"case": name, "id": report["id"], "verdict": report["verdict"],
              "elapsed_ms": report["elapsed_ms"], "first_result_ms": report["first_result_ms"],
              "sources": len(report["sources"]), "evidence": len(report["evidence"]),
              "conflicts": len(report["conflicts"]), "mode": report["analysis_mode"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
