import concurrent.futures
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime

from .models import Claim, Report, Event
from .provenance import normalize, bind_evidence, bind_source_reviews, regroup_sources
from .search import SearchResult, clean_text, now
from .transport import ServiceError

VERDICTS = {"支持", "反驳", "部分成立", "证据不足", "未知"}

OPEN_QUESTION = re.compile(
    r"是什么|什么是|是谁|的用途|的用法|的作用|的区别|的差异|做什么的|干什么的|是做什么|"
    r"怎么|怎样|如何|为什么|为何|哪些|介绍|简介|生平|来历|含义|意思|原理|流程|步骤|有哪些")
CLOSED_QUESTION = re.compile(r"是否|是不是|对吗|真的吗|有没有|能不能|能否|吗[？?]?$|呢[？?]?$")


def is_open_question(question):
    """Open questions ask for facts to discover; closed ones check a given claim."""
    text = (question or "").strip()
    return bool(OPEN_QUESTION.search(text)) and not bool(CLOSED_QUESTION.search(text))


def scope_drift(question, candidate):
    """Reject known accidental expansions; semantic fidelity still needs review."""
    text = " ".join(clean_text(candidate.get(k)) for k in ("text", "conditions", "time_scope"))
    terms = ("截至今天", "截至目前", "持续至今", "始终", "每个版本", "所有版本", "各个版本",
             "默认", "离线", "Windows", "Linux", "macOS", "不需要联网", "无需联网")
    return [term for term in terms if term.lower() in text.lower() and term.lower() not in question.lower()]


def original_excerpt(source, claims, cap=2400):
    """Supply one continuous original window; do not splice synthetic quotations."""
    page = source.page_text
    if len(page) <= cap:
        return page
    versions = list(dict.fromkeys(re.findall(r'\b\d+(?:\.\d+)+\b',
        ' '.join(c.time_scope + ' ' + c.text for c in claims))))
    for version in versions:
        match = re.search(r'(?<![\d.])' + re.escape(version) + r'(?!\d|\.\d)', page)
        if match:
            start = max(0, match.start() - cap // 4)
            return page[start:start + cap]
    # A search passage is only a locator here. The model still receives the
    # actual fetched page slice and all evidence is checked against that page.
    for hint in (source.passage, source.text):
        if 15 <= len(hint) <= 600 and hint in page:
            position = page.index(hint)
            start = max(0, position - cap // 4)
            return page[start:start + cap]
    terms = set(re.findall(r'[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}',
                           ' '.join(c.text + ' ' + c.entity for c in claims)))
    positions = [m.start() for term in terms for m in list(re.finditer(re.escape(term), page, re.I))[:8]]
    starts = {max(0, p - cap // 4) for p in positions} or {0}
    start = max(sorted(starts), key=lambda p: sum(term.casefold() in page[p:p + cap].casefold() for term in terms))
    return page[start:start + cap]


def answer_items(report):
    """Build answers from checked claims, never from unconstrained model prose."""
    reviews = {(r['claim_id'], r['source_id']): r for r in report.source_reviews}
    answers = []
    for claim in report.claims:
        if claim.verdict not in {'支持', '反驳'}:
            continue
        degree = 'FULL' if claim.verdict == '支持' else 'CONTRADICT'
        items = [e for e in report.evidence if e.claim_id == claim.id and e.support_degree == degree
                 and e.quote_verified and e.page_verified
                 and reviews.get((claim.id, e.source_id), {}).get('applicability') == 'applicable']
        if items:
            answers.append({'claim_id': claim.id,
                            'text': claim.text if claim.verdict == '支持' else f'现有原文反驳了“{claim.text}”',
                            'evidence_ids': [e.id for e in items],
                            'source_ids': list(dict.fromkeys(e.source_id for e in items))})
    return answers


def fallback_plan(question):
    text = question.strip().rstrip("？?。")
    claim_text = re.sub(r"是否|现在|还|吗$", "", text)
    if is_open_question(question):
        # Open questions need discovery queries, never "counter a claim" wording.
        entity = re.split(r"是谁|什么人|是什么|什么是|的用途|的作用|的区别|的差异|怎么|怎样|如何|"
                          r"为什么|为何|有哪些|介绍|简介|生平|来历|含义|意思|原理|流程|步骤|"
                          r"干什么|做什么|什么", text)[0].strip()[:60] or text[:60]
        if re.search(r"是谁|什么人|生平|简介|来历", text):
            direct, counter, follow = (f"{entity} 生平 事迹", f"{entity} 生平 争议 不同说法",
                                       f"{entity} 史料 原始记载 出处")
        elif re.search(r"用途|作用|干什么|做什么", text):
            direct, counter, follow = (f"{entity} 用途 作用", f"{entity} 实际使用 场景 限制",
                                       f"{entity} 官方 说明 文档")
        elif re.search(r"区别|差异|不同", text):
            direct, counter, follow = (f"{entity} 区别 对比", f"{entity} 适用 场景 差异",
                                       f"{entity} 官方 定义 说明")
        else:
            direct, counter, follow = (f"{text} 介绍 资料", f"{text} 出处 记载 疑问",
                                       f"{text} 常见问题 说明")
        return {"claims": [{"text": text, "entity": entity}], "queries": [
            {"query": direct, "purpose": "direct", "claim_ids": ["C1"], "reason": "查找能直接回答问题的材料"},
            {"query": counter, "purpose": "counter", "claim_ids": ["C1"], "reason": "查找不同说法、争议或反例材料"},
            {"query": follow, "purpose": "followup", "claim_ids": ["C1"], "reason": "核对原始出处与缺失条件"}]}
    entity = re.split(r"支持|是否|现在|在|已经", text)[0][:60] or text[:60]
    if "离线导出" in text:
        direct = f"{entity} 支持离线导出 官方"
        counter = f"{entity} 不支持离线导出 限制 问题"
        follow = f"{entity} 离线导出 版本 {datetime.now().year}"
    elif "购票" in text:
        direct = f"{entity} 仍可购票 官方"
        counter = f"{entity} 售罄 取消 延期 限制"
        follow = f"{entity} 最新公告 票务状态"
    else:
        direct = f"{text} 官方 文档"
        counter = f"{entity} 限制 例外 更正 不支持"
        follow = f"{text} 发布时间 版本 原始出处"
    return {"claims": [{"text": claim_text, "entity": entity}], "queries": [
        {"query": direct, "purpose": "direct", "claim_ids": ["C1"], "reason": "查找直接材料与原始出处"},
        {"query": counter, "purpose": "counter", "claim_ids": ["C1"], "reason": "查找限制、例外或前提不成立的材料"},
        {"query": follow, "purpose": "followup", "claim_ids": ["C1"], "reason": "核对时间、版本和缺失条件"}]}


def compose_verdict(claims, sources, evidence, analysis, source_reviews=None):
    by_source = {s.id: s for s in sources}
    reviews = {(r["claim_id"], r["source_id"]): r for r in (source_reviews or [])}
    proposed_claims = analysis.get("claims", [])
    proposals = {c.get("id"): c for c in proposed_claims if isinstance(c, dict)} if isinstance(proposed_claims, list) else {}
    for claim in claims:
        items = [e for e in evidence if e.claim_id == claim.id]
        claim.evidence_ids = [e.id for e in items]
        applicable = [e for e in items if reviews.get((claim.id, e.source_id), {}).get("applicability") == "applicable"]
        supporting = [e for e in applicable if e.support_degree == "FULL" and e.relation == "support"]
        opposing = [e for e in applicable if e.support_degree == "CONTRADICT" and e.relation == "counter"]
        proposal = proposals.get(claim.id, {})
        proposed = proposal.get("verdict", "证据不足")
        missing = proposal.get("missing", [])
        claim.missing = [clean_text(x)[:300] for x in missing if isinstance(x, str)][:5] if isinstance(missing, list) else []
        claim.next_action = clean_text(proposal.get("next_action"))[:500] or "打开相关项目或主办方的一手页面核对具体对象、时间和版本。"
        accepted = (proposed == "支持" and supporting) or (proposed == "反驳" and opposing)
        decisive = supporting if proposed == "支持" else opposing if proposed == "反驳" else supporting + opposing
        original = [e for e in decisive if e.page_verified]
        groups = {by_source[e.source_id].independence_group for e in original if by_source[e.source_id].independence_group}
        primary = any(reviews[(claim.id, e.source_id)].get("is_primary") for e in original)
        sufficient = bool(original) and (primary or len(groups) >= 2) and not claim.missing
        unexplained_opposition = bool(supporting and opposing and proposed in {"支持", "反驳"})
        if unexplained_opposition:
            sufficient = False
            claim.missing.append("存在支持与反对片段，尚不能仅凭单侧材料消除另一侧证据；请解释时间、版本和适用条件。")
        if not items:
            claim.verdict = "未知" if not sources else "证据不足"
            claim.conclusion = "本轮未取得与该主张直接绑定的有效证据；不能由搜索无结果推断主张错误。"
        elif accepted and sufficient:
            claim.verdict = proposed
            # The conclusion must be made of verbatim checked quotes, never model prose.
            quotes = list(dict.fromkeys(clean_text(e.quote)[:600] for e in original if clean_text(e.quote)))
            if proposed == "反驳":
                claim.conclusion = ("现有原文反驳了“" + claim.text + "”：" + "；".join(quotes or [claim.text]))[:1000]
            else:
                claim.conclusion = ("；".join(quotes) or claim.text)[:1000]
        else:
            claim.verdict = "证据不足"
            claim.conclusion = "已找到相关材料，但直接性、独立来源数量或适用条件尚不足以作出确定判断。"
        if not applicable:
            claim.missing.append("缺少对象、时间与适用条件均匹配的来源判断。")
        if not supporting and not opposing:
            claim.missing.append("缺少完整支持该原子主张的片段或直接反例；部分支持不能代替缺失条件。")
        if not primary and len(groups) < 2:
            claim.missing.append("缺少已读原文中的一手直接证据或至少两个保守来源组。")
        if not original:
            claim.missing.append("关键片段尚未在实时网页中复现；搜索内容可能滞后或截断。")
        claim.missing = list(dict.fromkeys(claim.missing))
        claim.strength = "中 · 适用性与支持度分别审查，原文匹配" if sufficient else "低 · 支持度、来源或原文仍有缺口"


class EvidenceDetective:
    def __init__(self, search, reasoner=None, *, emit=None, verify=False,
                 max_searches=6, time_budget=300, cancelled=None):
        self.search, self.reasoner, self.callback = search, reasoner, emit
        self.verify, self.max_searches, self.time_budget = verify, max_searches, time_budget
        self.cancelled = cancelled or threading.Event()

    def investigate(self, question, *, investigation_id=None):
        started = time.monotonic()
        report = Report(id=investigation_id or uuid.uuid4().hex[:16], question=question, started_at=now())
        def emit(kind, message, data=None):
            event = Event(len(report.events) + 1, kind, message, now(),
                          round((time.monotonic() - started) * 1000),
                          {"schema_version": report.schema_version, "investigation_id": report.id, **(data or {})})
            report.events.append(event)
            if self.callback:
                self.callback(asdict(event))
        def remaining():
            return self.time_budget - (time.monotonic() - started)
        def model_call(method, *args):
            if not self.reasoner or remaining() < 5 or self.cancelled.is_set():
                return {}
            self.reasoner.timeout = max(3, min(75, int(remaining() - 1)))
            try:
                output = getattr(self.reasoner, method)(*args)
                required = {'plan': ('claims',), 'candidate_facts': ('claims',),
                            'review_sources': ('source_reviews',), 'analyze': ('claims', 'evidence')}
                if not isinstance(output, dict) or not output or any(not isinstance(output.get(k), list) for k in required.get(method, ())):
                    raise ServiceError('推理返回缺少必要结构，当前审查未完成。')
                if method == 'review_sources':
                    expected_pairs = {(c['id'], s['id']) for c in args[0] for s in args[1]}
                    received_pairs = {(r.get('claim_id'), r.get('source_id')) for r in output['source_reviews']
                        if isinstance(r, dict) and isinstance(r.get('claim_id'), str) and isinstance(r.get('source_id'), str)
                        and r.get('applicability') in {'applicable', 'inapplicable', 'unknown'}
                        and all(r.get(f) in {'match', 'mismatch', 'unknown'}
                                for f in ('entity_match', 'time_match', 'conditions_match'))}
                    if not expected_pairs <= received_pairs:
                        raise ServiceError('来源审查未返回全部待核对对象的有效判断，当前审查未完成。')
                if method == 'analyze':
                    expected_ids = {c['id'] for c in args[0]}
                    valid_claims = [c for c in output['claims'] if isinstance(c, dict)
                                    and isinstance(c.get('id'), str) and c['id'] in expected_ids
                                    and c.get('verdict') in VERDICTS]
                    if not valid_claims:
                        raise ServiceError('支持度审查没有返回任何有效主张判断，当前审查未完成。')
                    output['claims'] = valid_claims
                if isinstance(output, dict) and output:
                    for failure in report.service_failures:
                        if failure['stage'] == method:
                            failure['resolved'] = True
                return output if isinstance(output, dict) else {}
            except Exception as exc:
                report.limitations.append("推理服务未完成，受影响部分采用保守输出。")
                diagnostic = str(exc) if isinstance(exc, ServiceError) else type(exc).__name__
                report.service_failures.append({'stage': method, 'round_elapsed_ms': round((time.monotonic() - started) * 1000),
                                                'diagnostic': diagnostic, 'resolved': False})
                emit("error", "推理步骤未完成；不会用规则猜测语义结论。", {"stage": method, "diagnostic": diagnostic})
                if any(code in diagnostic for code in ("HTTP 402", "HTTP 401", "HTTP 403")):
                    report.limitations.append("推理服务鉴权/余额不可用：" + diagnostic)
                    self.reasoner = None
                return {}
        emit("started", "建立调查档案；所有核心检索仅使用元宝搜索。")
        plan = model_call("plan", question)
        inferred_open = is_open_question(question)
        report.question_type = plan.get('question_type') if plan.get('question_type') in {'verification', 'open_question'} else ('open_question' if inferred_open else 'verification')
        if isinstance(plan.get("claims"), list) and not plan["claims"]:
            report.scope = clean_text(plan.get("scope")) or "该问题不在公开、非敏感信息调查范围内。"
            report.stop_reason = "范围限制：不进行敏感或私人信息调查。"
            report.stop_code = "out_of_scope"
            report.verdict, report.summary, report.next_action = "未知", report.scope, "请换一个公开、非敏感、可复核的问题。"
            report.elapsed_ms = round((time.monotonic() - started) * 1000)
            emit("stopped", report.stop_reason, {"stop_code": report.stop_code, "verdict": report.verdict, "gaps": []})
            return report
        if not isinstance(plan.get("claims"), list) or not plan["claims"]:
            plan = fallback_plan(question)
            report.limitations.append("主张拆解使用规则降级；未启用完整模型语义分析时不作确定事实判断。")
            report.limitations.append("本轮为模板查询，不是基于证据的智能反证改写；语义冲突分析与自动主张判断未启用。")
        if report.question_type == 'verification':
            drift = [c for c in plan['claims'] if isinstance(c, dict) and scope_drift(question, c)]
            if drift:
                # Keep the user's whole assertion instead of another speculative rewrite.
                plan['claims'] = [{'text': question.strip().rstrip('？?。'), 'original_span': question}]
                emit('validation', '规划增加了原问题未要求的条件，已回到用户原句核查。',
                     {'stage': 'plan', 'rejected': len(drift), 'added_conditions': [scope_drift(question, c) for c in drift]})
        report.claims = [Claim(id=f"C{i+1}", text=clean_text(c.get("text"))[:500],
                               entity=clean_text(c.get("entity"))[:100],
                               time_scope=clean_text(c.get("time_scope")) or "未知",
                               conditions=clean_text(c.get("conditions")) or "未知", original_question=question)
                         for i, c in enumerate(plan["claims"][:3]) if isinstance(c, dict) and c.get("text")]
        report.scope = clean_text(plan.get("scope")) or report.scope
        if not report.claims:
            report.claims = [Claim("C1", question, original_question=question)]
        emit("plan", f"整理为 {len(report.claims)} 个{'检索目标，待取得材料后提取事实' if report.question_type == 'open_question' else '待核查主张'}。",
             {"claims": [asdict(c) for c in report.claims], "scope": report.scope, 'question_type': report.question_type})
        seen_queries = set()
        def run_queries(queries, round_number):
            work = []
            for q in queries:
                if not isinstance(q, dict) or not isinstance(q.get("query"), str):
                    continue
                query = clean_text(q["query"])[:240]
                key = re.sub(r'\s+', ' ', query).casefold()
                if not query or key in seen_queries or len(report.queries) >= self.max_searches or remaining() < 2 or self.cancelled.is_set():
                    continue
                seen_queries.add(key)
                task = {"id": f"Q{len(report.queries)+1}", "query": query,
                        "purpose": q.get("purpose", "followup"), "reason": clean_text(q.get("reason"))[:400],
                        "claim_ids": [c for c in q.get("claim_ids", []) if isinstance(c, str) and c in {c.id for c in report.claims}]
                                     if isinstance(q.get("claim_ids", []), list) else [],
                        "round": round_number, "status": "running"}
                report.queries.append(task)
                work.append(task)
                emit("search_start", f"{task['id']} · {task['reason'] or '检索公开证据'}", task.copy())
            if not work:
                return 0
            if hasattr(self.search, "timeout"):
                self.search.timeout = max(2, min(12, int(remaining())))
            added_count = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(self.search.search, t["query"], limit=8): t for t in work}
                for future in concurrent.futures.as_completed(futures):
                    task = futures[future]
                    try:
                        results = future.result()
                        observed = {s.id: s.digest for s in report.sources}
                        added = normalize(results, report.sources, task["id"], question)
                        grown = sum(1 for s in report.sources if s.id in observed and s.digest != observed[s.id])
                        # A new passage on a known URL is progress for the next review round.
                        added_count += len(added) + grown
                        task.update({"status": "done", "result_count": len(results), "new_sources": len(added),
                                     "new_passages": grown})
                        if results and not report.first_result_ms:
                            report.first_result_ms = round((time.monotonic() - started) * 1000)
                        emit("search_result", f"{task['id']} · 返回 {len(results)} 条，新增 {len(added)} 个来源"
                             + (f"、{grown} 段新片段。" if grown else "。"),
                             {**task, "sources": [asdict(s) for s in added]})
                    except Exception as exc:
                        task.update({"status": "failed", "error": type(exc).__name__})
                        report.service_failures.append({'stage': task['purpose'] + '_search',
                            'query_id': task['id'], 'diagnostic': type(exc).__name__, 'resolved': False})
                        emit("error", f"{task['id']} · 搜索未完成，保留证据缺口，不补造结果。", task.copy())
            return added_count
        reviewed = {}
        examined = {}
        latest = {}
        evidence_ids = {}
        last_proposals = {}
        candidates_attempted = False
        candidates_ready = report.question_type != 'open_question'
        def reviewable_sources():
            # The same eight-page budget bounds the backlog, including pages
            # whose read failed. Unread search snippets remain leads only.
            return [s for s in report.sources if s.fetched_at] if self.verify else report.sources[:8]
        def pending_material():
            return bool(self.reasoner and candidates_ready and any(
                examined.get(s.id) != (s.digest, s.page_digest) for s in reviewable_sources()))
        def analyze(round_number):
            nonlocal candidates_attempted, candidates_ready
            if not report.sources or self.cancelled.is_set():
                return {}
            linked = {e.source_id for e in report.evidence if e.support_degree in ("FULL", "PARTIAL", "CONTRADICT")}
            query_order = {q['id']: i for i, q in enumerate(report.queries)}
            ranked = sorted(report.sources, key=lambda s: (
                -max((query_order.get(qid, -1) for qid in s.query_ids), default=-1), not s.primary, s.id not in linked))
            # Read in batches so new counter/follow-up originals retain a share
            # of the total eight attempts, even when old domains look primary.
            if self.verify and remaining() > 4:
                from .reader import verify_sources
                budget = max(0, 8 - sum(bool(s.fetched_at) for s in report.sources))
                batch = [s for s in ranked if not s.fetched_at][:min(4, budget)]
                if batch:
                    emit("verification", "分批读取候选原文，为反证和补查保留阅读额度。",
                         {"round": round_number, "source_ids": [s.id for s in batch]})
                    verify_sources(batch, emit, limit=len(batch), timeout=min(3, max(1, remaining() / 2)),
                                   audit=getattr(self.search, "audit", None))
                    regroup_sources(report.sources)
            candidates = []
            preferred = sorted(reviewable_sources(), key=lambda s: (
                examined.get(s.id) == (s.digest, s.page_digest), not s.page_text, not s.primary, s.id not in linked))
            # Give each actual query a representative before filling the small model context.
            for task in reversed(report.queries):
                candidate = next((s for s in preferred if task["id"] in s.query_ids and s not in candidates
                                  and examined.get(s.id) != (s.digest, s.page_digest)), None)
                if candidate and len(candidates) < 2:
                    candidates.append(candidate)
            for source in preferred:
                if source not in candidates and len(candidates) < 2:
                    candidates.append(source)
            if not self.reasoner or remaining() < 5:
                return {}
            if not candidates:
                return {}
            def source_data(s, cap=2400, text_cap=2200):
                return {"id": s.id, "title": s.title, "url": s.url, "final_url": s.final_url,
                     "date": s.date, "text": s.text[:text_cap], "page_text": original_excerpt(s, report.claims, cap=cap),
                     "primary_candidate": s.primary, "url_status": s.url_status,
                     "independence_group": s.independence_group}
            data = [source_data(s) for s in candidates]
            if not candidates_ready:
                if candidates_attempted:
                    return {}
                candidates_attempted = True
                emit('candidate_claims_start', '从首批真实材料中整理候选事实；尚未核实。', {'round': round_number})
                # Discovery happens once. It must see the entire read budget,
                # rather than irrevocably missing an answer on page seven just
                # because later semantic review uses small two-source batches.
                discovery_pool = sorted(report.sources, key=lambda s: (not bool(s.page_text), not s.primary))[:16]
                discovery_data = [source_data(s, cap=1600, text_cap=600) for s in discovery_pool]
                raw_candidates = model_call('candidate_facts', question, discovery_data)
                by_id = {s['id']: s for s in discovery_data}
                accepted = []
                candidate_source_ids = []
                proposed = raw_candidates.get('claims', [])
                for item in proposed if isinstance(proposed, list) else []:
                    if not isinstance(item, dict):
                        continue
                    s = by_id.get(item.get('source_id'), {})
                    quote = item.get('quote')
                    if not (isinstance(quote, str) and 10 <= len(quote) <= 600
                            and (quote in s.get('page_text', '') or quote in s.get('text', '')) and clean_text(item.get('text'))):
                        continue
                    limit = 1 if re.search(r'是谁[？?]?$', question.strip()) else 3
                    if len(accepted) >= limit:
                        break
                    accepted.append(Claim(f'C{len(accepted)+1}', clean_text(item['text'])[:500],
                                          entity=clean_text(item.get('entity'))[:100],
                                          time_scope=clean_text(item.get('time_scope')) or '未知',
                                          conditions=clean_text(item.get('conditions')) or '未知', original_question=question))
                    candidate_source_ids.append(item['source_id'])
                if accepted:
                    report.claims = accepted
                    candidates_ready = True
                    priority = [s for sid in dict.fromkeys(candidate_source_ids)
                                for s in report.sources if s.id == sid]
                    budget = max(0, 8 - sum(bool(s.fetched_at) for s in report.sources))
                    targeted = [s for s in priority if not s.fetched_at][:budget]
                    if self.verify and targeted and remaining() > 4:
                        emit('verification', '候选事实指向尚未读取的来源，先读取对应原文再核验。',
                             {'round': round_number, 'source_ids': [s.id for s in targeted]})
                        verify_sources(targeted, emit, limit=len(targeted), timeout=min(3, max(1, remaining() / 2)),
                                       audit=getattr(self.search, 'audit', None))
                        regroup_sources(report.sources)
                    candidates = (priority + [s for s in candidates if s not in priority])[:2]
                    data = [source_data(s) for s in candidates]
                else:
                    report.limitations.append('开放问答未取得带真实片段的候选事实，不将检索目标当作已核实断言。')
                emit('candidate_claims', '候选事实已绑定材料，接下来分别核验来源和原文。' if accepted else '未取得可核验候选事实，不猜答案。',
                     {'round': round_number, 'status': 'completed' if accepted else 'degraded',
                      'claims': [asdict(c) for c in report.claims], 'accepted_count': len(accepted), 'question_type': report.question_type})
                if not accepted:
                    return {}
            examined.update({s.id: (s.digest, s.page_digest) for s in candidates})
            claim_data = [{field: getattr(c, field) for field in ("id", "text", "entity", "time_scope", "conditions", "missing")}
                          for c in report.claims]
            pending = [s for s in candidates if reviewed.get(s.id) != (s.digest, s.page_digest)]
            if pending:
                pending_ids = {s.id for s in pending}
                emit("source_review_start", "独立审查来源：对象、时间、版本与一手身份。", {"round": round_number, "source_ids": sorted(pending_ids)})
                raw = model_call("review_sources", claim_data, [d for d in data if d["id"] in pending_ids])
                # A failed new review cannot overwrite a completed review with
                # synthetic unknown rows. Its previous digest remains recorded.
                rows = bind_source_reviews(raw.get("source_reviews"), report.claims, pending) if raw else []
                latest.update({(r["claim_id"], r["source_id"]): r for r in rows})
                if raw:
                    reviewed.update({s.id: (s.digest, s.page_digest) for s in pending})
                report.source_reviews = list(latest.values())
                emit("source_review", "来源适用性审查已返回；未知项保留缺口。" if raw else "来源审查未完成，相关适用性保留未知。",
                     {"round": round_number, "status": "completed" if raw else "degraded", "reviews": rows})
            source_ids = {s.id for s in candidates}
            rows = [r for r in report.source_reviews if r["source_id"] in source_ids]
            emit("evidence_review_start", "独立审查原文支持度：完整、部分、矛盾、无关或未知。", {"round": round_number, "source_ids": sorted(source_ids)})
            output = model_call("analyze", claim_data, data, rows)
            if not output:
                # Retain checked facts; service failure prevents a closed investigation separately.
                emit("evidence_review", "本轮支持度审查未完成，保留已有引用与缺口。", {"round": round_number,
                    "status": "degraded", "evidence": [asdict(e) for e in report.evidence], "claims": [asdict(c) for c in report.claims], "rejected": 0})
                return {}
            report.analysis_mode = "模型独立来源审查 + 原文支持度审查 + 程序引用/充分性校验"
            supplied = {d["id"]: d for d in data}
            proposals = output.get("evidence", [])
            valid = []
            expanded_count = 0
            for item in proposals if isinstance(proposals, list) else []:
                if not isinstance(item, dict):
                    continue
                source = supplied.get(item.get("source_id"), {})
                field = "page_text" if item.get("quote_origin") == "page_text" else "text"
                if isinstance(item.get("quote"), str) and item["quote"] in source.get(field, ""):
                    # The model reviewed this whole continuous window. Retain
                    # adjacent title/section context with short decisive quotes,
                    # rather than showing a version label detached from its object.
                    if field == 'page_text' and item.get('support_degree') == 'FULL' and 10 <= len(item['quote']) <= 240:
                        context = source[field]
                        start = context.index(item['quote'])
                        expanded = context[max(0, start - 160):start + len(item['quote']) + 80]
                        if expanded != item['quote']:
                            item = {**item, 'quote': expanded,
                                    'reason': clean_text(item.get('reason')) + '（程序保留同一已审原文窗口的连续前后文。）'}
                            expanded_count += 1
                    valid.append(item)
            if expanded_count:
                emit('validation', f'为 {expanded_count} 条短引文保留所核对的相邻原文，标题与条件不脱离上下文。',
                     {'round': round_number, 'context_expanded': expanded_count})
            fresh, rejected = bind_evidence(valid, report.claims, report.sources)
            previous_decisive = {c.id: {e.source_id for e in report.evidence
                if e.claim_id == c.id and e.support_degree in {'FULL', 'CONTRADICT'} and e.page_verified}
                for c in report.claims}
            # Replace reviewed claim/source pairs, retain facts outside this round's small context.
            proposed_ids = {c.get('id') for c in output.get('claims', []) if isinstance(c, dict)} if isinstance(output.get('claims'), list) else set()
            refreshed = {(cid, sid) for cid in proposed_ids for sid in source_ids} | {(e.claim_id, e.source_id) for e in fresh}
            report.evidence = [e for e in report.evidence if (e.claim_id, e.source_id) not in refreshed] + fresh
            for item in report.evidence:
                key = (item.claim_id, item.source_id, item.quote_origin, item.quote)
                if key not in evidence_ids:
                    evidence_ids[key] = f"E{len(evidence_ids) + 1}"
                item.id = evidence_ids[key]
            rejected += max(0, len(proposals) - len(valid)) if isinstance(proposals, list) else 1
            if rejected:
                emit("validation", f"引用校验拦截 {rejected} 条无效/重复/非逐字引用。", {"round": round_number, "rejected": rejected})
            for proposal in output.get('claims', []) if isinstance(output.get('claims'), list) else []:
                if isinstance(proposal, dict) and proposal.get('id') in {c.id for c in report.claims}:
                    prior = previous_decisive.get(proposal['id'], set())
                    if prior and prior.isdisjoint(source_ids) and not any(
                            e.claim_id == proposal['id'] and e.support_degree in {'FULL', 'CONTRADICT'}
                            and any(r['claim_id'] == e.claim_id and r['source_id'] == e.source_id
                                    and r['applicability'] == 'applicable' for r in report.source_reviews)
                            for e in fresh):
                        # "Not found in this batch" cannot retract facts checked
                        # in an earlier batch absent from this model context.
                        continue
                    last_proposals[proposal['id']] = proposal
            compose_verdict(report.claims, report.sources, report.evidence,
                            {**output, 'claims': list(last_proposals.values())}, report.source_reviews)
            emit("evidence_review", "原文支持度判断完成，程序已校验引用出处。", {"round": round_number,
                 "status": "completed", "evidence": [asdict(e) for e in report.evidence], "claims": [asdict(c) for c in report.claims], "rejected": rejected})
            return output
        queries = plan.get("queries", [])
        if not isinstance(queries, list):
            queries = []
        direct = [q for q in queries if isinstance(q, dict) and q.get("purpose") == "direct"][:2]
        counter = [q for q in queries if isinstance(q, dict) and q.get("purpose") == "counter"][:2]
        fallback = fallback_plan(question)["queries"]
        run_queries(direct or fallback[:1], 1)
        preliminary = analyze(1)
        emit("counter", "挑战初步结论：查找例外、版本限制或相反事实。" if self.reasoner else "模板补查：查找限制与例外（非智能语义反证）。", {"adaptive": bool(self.reasoner)})
        new_counter = run_queries(counter or fallback[1:2], 2)
        output = (analyze(2) if new_counter or pending_material() else {}) or preliminary
        if not output:
            compose_verdict(report.claims, report.sources, report.evidence, {}, report.source_reviews)
        def enforce_counter():
            if not any(q["purpose"] == "counter" and q["status"] == "done" for q in report.queries):
                for claim in report.claims:
                    if "反证检索未完成，不能声称已排查相反事实。" not in claim.missing:
                        claim.missing.append("反证检索未完成，不能声称已排查相反事实。")
                # A failed counter round constrains completion, not the checked fact itself.
                if not any(f["stage"] == "counter_search" for f in report.service_failures):
                    report.service_failures.append({"stage": "counter_search",
                                                    "diagnostic": "反证检索未完成（没有成功的 counter 查询）",
                                                    "resolved": False})
        enforce_counter()
        def gaps():
            return [{"claim_id": c.id, "missing": c.missing, "verdict": c.verdict}
                    for c in report.claims if c.missing or c.verdict not in {"支持", "反驳"}]
        def unresolved():
            return [c for c in output.get("conflicts", []) if isinstance(c, dict) and c.get("resolved") is not True] if isinstance(output.get("conflicts"), list) else []
        exhausted = False
        round_number = 3
        while (gaps() or unresolved()) and remaining() > 8 and not self.cancelled.is_set():
            if pending_material():
                emit('analysis', '先审查阅读预算内尚未核对的已有材料，再决定是否补查。', {'round': round_number})
                output = analyze(round_number) or output
                enforce_counter()
                round_number += 1
                continue
            if len(report.queries) >= self.max_searches:
                break
            suggested = output.get("followup_queries", [])
            open_ids = {c.id for c in report.claims if c.missing or c.verdict not in {'支持', '反驳'}}
            extra = [dict(q, purpose="followup") for q in suggested if isinstance(q, dict) and isinstance(q.get("query"), str)
                     and re.sub(r'\s+', ' ', clean_text(q["query"])[:240]).casefold() not in seen_queries
                     and isinstance(q.get('claim_ids'), list)
                     and any(cid in open_ids for cid in q['claim_ids'] if isinstance(cid, str))][:2] if isinstance(suggested, list) else []
            if not extra:
                open_claim = next((c for c in report.claims if c.missing), report.claims[0])
                time_scope = open_claim.time_scope if open_claim.time_scope != '未知' else ''
                extra = [{"query": f"{open_claim.entity or open_claim.text} {time_scope} 原始出处 说明".strip(),
                          "purpose": "followup", "claim_ids": [open_claim.id], "reason": "补齐尚未核实的具体条件"}]
                if not self.reasoner:
                    extra = [fallback[2]]
            emit("followup", "证据缺口尚未关闭，按缺失条件自动补查。", {"round": round_number, "gaps": gaps(), "conflicts": unresolved(), "queries": extra})
            count_before = len(report.queries)
            additions = run_queries(extra, round_number)
            if additions:
                output = analyze(round_number) or output
                enforce_counter()
            if len(report.queries) == count_before or not additions:
                exhausted = True
                break
            if not self.reasoner:
                break
            round_number += 1
        source_ids = {s.id for s in report.sources}
        claim_ids = {c.id for c in report.claims}
        for conflict in output.get("conflicts", []) if isinstance(output.get("conflicts"), list) else []:
            if not isinstance(conflict, dict):
                continue
            raw_ids = conflict.get("source_ids", [])
            ids = [sid for sid in raw_ids if isinstance(sid, str) and sid in source_ids] if isinstance(raw_ids, list) else []
            linked_ids = {e.source_id for e in report.evidence if e.claim_id == conflict.get("claim_id")}
            if len(set(ids)) < 2 or conflict.get("claim_id") not in claim_ids or not set(ids) <= linked_ids:
                continue
            report.conflicts.append({"claim_id": conflict["claim_id"], "source_ids": ids,
                "kind": clean_text(conflict.get("kind"))[:50],
                "explanation": clean_text(conflict.get("explanation"))[:1000],
                "resolved": bool(conflict.get("resolved")), "review": "模型解释 · 请复核对应片段"})
        report.independent_source_count = len({s.independence_group for s in report.sources})
        if self.cancelled.is_set():
            report.stop_code = "cancelled"
            report.stop_reason = "用户取消；仅保留已经取得的材料，不继续发起请求。"
        elif not gaps() and not unresolved() and report.evidence and not any(not f['resolved'] for f in report.service_failures):
            report.stop_code = "sufficient"
            report.stop_reason = "已完成直接检索与反证检索；关键主张的适用性、完整支持度及原文引用均达门槛，且没有未关闭的缺口。"
        elif remaining() <= 8:
            report.stop_code = "time_budget"
            report.stop_reason = "达到时间预算，未关闭的证据缺口如实保留。"
        elif len(report.queries) >= self.max_searches:
            report.stop_code = "search_budget"
            report.stop_reason = f"达到 {self.max_searches} 次搜索上限，未解决的冲突不强行消除。"
        elif not any(q["status"] == "done" for q in report.queries):
            report.stop_code = "service_unavailable"
            report.stop_reason = "搜索服务未完成，没有足够材料继续审查；保留证据缺口。"
        elif exhausted:
            report.stop_code = "no_new_evidence"
            report.stop_reason = "针对缺口的补查未带来新增来源，或查询已重复；保留未知或证据不足。"
        else:
            report.stop_code = "incomplete"
            report.stop_reason = "已完成直接检索、反证和必要补查；当前证据不足以消除所有不确定性。"
        if any(not f['resolved'] for f in report.service_failures) and report.stop_code not in {'cancelled', 'time_budget', 'search_budget'}:
            report.stop_code = 'service_incomplete'
            report.stop_reason = ('审查服务未完成（' + '、'.join(dict.fromkeys(
                f['stage'] for f in report.service_failures if not f['resolved']))
                + '）；已核实事实照常保留，但本轮不能据此判定材料不足。')
        verdicts = {c.verdict for c in report.claims}
        report.verdict = next(iter(verdicts)) if len(verdicts) == 1 else ("证据不足" if verdicts & {"未知", "证据不足"} else "部分成立")
        report.summary = report.claims[0].conclusion if len(report.claims) == 1 else f"核查 {len(report.claims)} 项主张：" + "；".join(f"{c.id} {c.verdict}" for c in report.claims)
        report.answer_items = answer_items(report) if candidates_ready else []
        if report.answer_items:
            report.summary = '；'.join(item['text'] + ' ' + ' '.join(f'[{sid}]' for sid in item['source_ids']) for item in report.answer_items)
            if gaps():
                report.summary += '。其余部分尚未查明，详见证据缺口。'
        report.next_action = "；".join(dict.fromkeys(c.next_action for c in report.claims))
        if report.stop_code == 'service_incomplete':
            stages = '、'.join(dict.fromkeys(f['stage'] for f in report.service_failures if not f['resolved']))
            report.limitations.append(f"本轮审查服务未完成（{stages}）；报告中的「证据不足」是本轮未审完的占位，不代表材料确实不存在。")
            if not report.answer_items:
                report.summary = (f"审查服务未完成（{stages}）：推理模型本轮未能完成来源审查与原文支持度审阅，"
                                  "因此不把「检索到但未审」当成材料不足来下结论；请重新发起调查。")
        if not gaps() and any(not f['resolved'] for f in report.service_failures):
            report.next_action = '已有事实可复核；请稍后重新调查以完成未结束的服务审查。'
        report.limitations.extend(["来源可访问不代表内容真实；逐字匹配也不能自动证明语义蕴含，最终需要人工复核。",
                                  "独立来源数是域名/内容保守分组，不是已验证的独立采访或原创材料数量。"])
        report.limitations = list(dict.fromkeys(report.limitations))
        report.elapsed_ms = round((time.monotonic() - started) * 1000)
        emit("stopped", report.stop_reason, {"stop_code": report.stop_code, "verdict": report.verdict,
             "gaps": gaps(), "search_count": len(report.queries), "elapsed_ms": report.elapsed_ms})
        return report
