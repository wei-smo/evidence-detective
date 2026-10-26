import json
import os
import threading
from pathlib import Path

from .config import ROOT

DATA = ROOT / "data" / "investigations"


class Store:
    def __init__(self, identifier, root=None):
        self.path = Path(root or DATA) / identifier
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def audit(self, entry):
        # No Authorization headers are ever supplied. Logs remain server-local.
        with self.lock:
            with (self.path / "audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save(self, report):
        path = self.path / "report.json"
        temporary = path.with_suffix(".tmp")
        with self.lock:
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)


def markdown(report):
    failures = [f for f in report.get("service_failures", []) if not f.get("resolved")]
    if report.get("stop_code") == "service_incomplete" or failures:
        status = "调查状态：服务未完成 · " + (report.get("stop_reason") or "部分审查未完成；已核实事实照常保留。")
    else:
        status = "结论：" + report["verdict"]
    lines = ["# 证据侦探 · 调查报告", "", "## " + report["question"], "",
             f"调查编号：{report['id']} · 开始于 {report['started_at']}", "",
             status, "", report["summary"], ""]
    sources = {s["id"]: s for s in report["sources"]}
    evidence = {e["id"]: e for e in report["evidence"]}
    if report.get('answer_items'):
        lines += ['## 已查明', '']
        for item in report['answer_items']:
            citations = ' '.join(f"[{sid}]({sources[sid]['url']})" for sid in item.get('source_ids', []) if sid in sources)
            lines += [f"- {item['text']} {citations} · 证据 {', '.join(item.get('evidence_ids', []))}"]
        lines += ['']
    if report.get('service_failures'):
        lines += ['## 服务执行记录（与材料不足分开）', '']
        for failure in report['service_failures']:
            lines += [f"- {failure['stage']} · {'后续已恢复' if failure.get('resolved') else '未完成'}"]
        lines += ['']
    for claim in report["claims"]:
        lines += [f"## {claim['id']} · {claim['text']}", "", f"**{claim['verdict']}** · {claim['conclusion']}", ""]
        reviews = [r for r in report.get('source_reviews', []) if r['claim_id'] == claim['id']]
        if reviews:
            lines += ['### 来源适用性（独立判断）', '']
            for review in reviews:
                source = sources[review['source_id']]
                lines += [f"- [{source['id']}]({source['url']}) · {review['applicability']} · 对象 {review['entity_match']} / 时间 {review['time_match']} / 条件 {review['conditions_match']} · 一手判断 {review['is_primary']}",
                          f"  {review['reason']}"]
            lines += ['', '### 原文支持度与逐字引用', '']
        for eid in claim["evidence_ids"]:
            item = evidence[eid]
            source = sources[item["source_id"]]
            lines += [f"- {eid} / {item['relation']} / {item.get('support_degree', '未分级')} / [{source['id']} · {source['title']}]({source['url']})",
                      f"  > {item['quote']}", f"  {item['reason']}",
                      f"  来源日期：{source['date']}；检索时间：{source['retrieved_at']}；层级：{source['level']}", ""]
            if 'quote_origin' in item:
                lines += [f"  引用位置：{item['quote_origin']}[{item['quote_start']}:{item['quote_end']}]；原文匹配：{item['page_verified']}；文本 SHA-256：{item['content_digest']}", '']
        lines += ["缺口：" + "；".join(claim["missing"]), "", "下一步：" + claim["next_action"], ""]
    lines += ["## 来源冲突", ""]
    for conflict in report["conflicts"]:
        lines += [f"- {conflict['kind']} · {', '.join(conflict['source_ids'])} · {conflict['explanation']}"]
    if not report["conflicts"]:
        lines += ["未取得经有效引用绑定的冲突；不代表不存在矛盾。"]
    lines += ["", "## 调查边界", "", report["scope"], "", "停止原因：" + report["stop_reason"], "",
              f"停止码：{report.get('stop_code', 'legacy')}；搜索 {len(report['queries'])} 次；总耗时 {report['elapsed_ms']/1000:.2f} 秒；分析模式：{report['analysis_mode']}", ""]
    lines += ["- " + x for x in report["limitations"]]
    if report.get('reviews'):
        lines += ['', '## 人工复核记录（非自动分析）', '']
        for review in report['reviews']:
            source = sources[review['source_id']]
            lines += [f"- {review['id']} · {review['claim_id']} · {review['relation']} · {review['reviewer']} · {review['at']}",
                      f"  [{source['id']} · {source['title']}]({source['url']})", f"  > {review['quote']}",
                      f"  {review['reason']} · {review['label']}", '']
    if report.get('manual_conclusions'):
        lines += ['', '## 操作者结论（与自动结论分开）', '']
        for item in report['manual_conclusions']:
            lines += [f"- {item['claim_id']} · {item['verdict']} · {item['conclusion']}",
                      f"  复核记录：{', '.join(item['review_ids'])}；操作者：{item['reviewer']}；{item['label']}", '']
    lines += ["", "## 真实查询", ""]
    lines += [f"- {q['id']} · {q['purpose']} · {q['query']} · {q['status']}" for q in report["queries"]]
    return "\n".join(lines)
