const $ = (id) => document.getElementById(id);
const state = { report: null, selected: null, job: null, running: false, sources: new Map(), sourceReviews: new Map(), evidence: [], claims: [], searches: 0, started: 0, timer: null, questionType: 'verification', question: '', history: false };
const esc = (x) => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = (u) => { try { const p = new URL(u); return ['https:', 'http:'].includes(p.protocol) ? p.href : '#'; } catch { return '#'; } };
const date = (x) => { const d = new Date(x); return Number.isNaN(d.valueOf()) ? esc(x || '未知') : d.toLocaleString('zh-CN', {hour12:false}); };
const headers = () => ({'Content-Type': 'application/json', ...(sessionStorage.getItem('ed-token') ? {'Authorization':'Bearer '+sessionStorage.getItem('ed-token')} : {})});
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers: {...headers(), ...options.headers}});
  if (!response.ok) { let error; try { error = await response.json(); } catch {} throw new Error(error?.error || `请求失败（${response.status}）`); }
  return response;
}
function showError(message) { $('error-banner').textContent = message; $('error-banner').hidden = false; }
function clearError() { $('error-banner').hidden = true; }
function status(text, running=false) { $('run-status').textContent = text; $('run-status').classList.toggle('running', running); }
function metrics() {
  $('metric-claims').textContent = state.claims.length || '—';
  $('metric-searches').textContent = state.searches || '—';
  $('metric-sources').textContent = state.sources.size || '—';
  const seconds = state.report ? state.report.elapsed_ms/1000 : state.started ? (Date.now()-state.started)/1000 : 0;
  $('metric-time').innerHTML = `${seconds ? seconds.toFixed(1) : '—'}<em>s</em>`;
}
/* ---------- 工作流卡片：把真实事件流映射为 5 个阶段 ---------- */
const STAGES = ['understand', 'search', 'challenge', 'verify', 'answer'];
const STAGE_INFO = {
  understand: {num: '01', kicker: '问题建模', title: '先把一句模糊的话，拆成可核实的主张。', body: '识别对象、时间范围与适用条件。范围外的敏感或私人问题会在这里被拦下，而不是被硬查。', empty: '调查开始后，这里会列出拆解出的待核查主张。'},
  search: {num: '02', kicker: '寻找证据', title: '先用元宝搜索，找直接材料与原始出处。', body: '只使用元宝搜索召回来源。来源按域名与内容相似度保守分组，转载不重复计数。', empty: '这一阶段还没有真实检索记录。'},
  challenge: {num: '03', kicker: '主动找反证', title: '站在对立面再查一遍：限制、例外、旧版本。', body: '寻找推翻或限定结论的材料。旧版与新版的差异不自动构成矛盾；没有找到反证，也不等于证明不存在反例。', empty: '尚无实际反证检索记录。'},
  verify: {num: '04', kicker: '核验出处', title: '先判断来源是否适用，再判断原文支持多少。', body: '全程最多读取 8 个网页，每批最多 2 个来源进入审查；先核对来源，再核对原文。引用须与已取得的文字逐字匹配，并保留位置。', empty: '尚未核验来源。'},
  answer: {num: '05', kicker: '给出结论', title: '把查明的答案和出处交给你。', body: '确定结论需要适用来源、完整支持或直接反例、原文匹配及来源门槛。部分支持或未完成反证会留下缺口，并触发补查。', empty: '调查结束后，这里显示结论与停止原因。'}
};
const ROLES = {
  understand: [{id:'compass', name:'领案员·罗盘', duty:'保留原问题，拆解对象、时间与条件'}],
  search: [{id:'blue', name:'寻踪员·小蓝', duty:'直接检索、登记来源、按缺口补查'}],
  challenge: [{id:'fox', name:'挑错员·赤狐', duty:'寻找反例、限制与适用边界'}],
  verify: [{id:'gold', name:'验源官·阿金', duty:'独立记录来源适用性审查'}, {id:'violet', name:'核证员·紫灯', duty:'独立记录原文支持度审查'}],
  answer: [{id:'ink', name:'档案官·墨墨', duty:'整理已查明、尚未查明与停止原因'}]
};
// Only add existing, reviewed PNG assets here; an empty mapping deliberately loads no images.
const ROLE_PNG = Object.fromEntries(['compass','blue','fox','gold','violet','ink'].map(id => [id, `/assets/roles/${id}.png`]));
const wf = {events: {}, queries: new Map(), current: null, selected: 'understand', pinned: false, finished: false, interrupted: false};
const STOP_LABEL = {sufficient:'证据门槛已满足', service_incomplete:'服务未完成 · 调查未闭合', service_unavailable:'服务不可用 · 调查未闭合', time_budget:'调度时间预算已用尽 · 保留已核实内容', search_budget:'搜索次数预算已用尽 · 保留缺口', cancelled:'用户已停止 · 保留已有记录', no_new_evidence:'没有新增可用证据', incomplete:'仍有未查明事项'};
const SERVICE_STAGE = {plan:'understand', candidates:'understand', extract_candidates:'understand', candidate_claims:'understand', review_sources:'verify', source_review:'verify', analyze:'verify', evidence_review:'verify'};
function stopLabel(report) { return STOP_LABEL[report?.stop_code] || '本轮已停止 · 见停止原因'; }
function displayVerdict(verdict) { return ['证据不足', '未知'].includes(verdict) ? '尚未查明' : verdict; }
function reportHeadline(report) {
  if (report?.service_incomplete || ['service_incomplete', 'service_unavailable'].includes(report?.stop_code)
      || report?.service_failures?.some(f => !f.resolved)) return '执行未完成';
  if (report?.question_type === 'open_question' && (report.answer_count > 0
      || (Array.isArray(report.claims) && checkedAnswers(report).length))) return '已查明';
  return displayVerdict(report?.verdict);
}
function queryStage(data = {}) {
  const purpose = data.purpose || wf.queries.get(data.id)?.purpose;
  return purpose === 'counter' ? 'challenge' : 'search';
}
function stageOf(event) {
  const k = event.kind;
  if (['started', 'plan', 'candidate_claims_start', 'candidate_claims'].includes(k)) return 'understand';
  if (k === 'search_start' || k === 'search_result' || (k === 'error' && event.data?.query)) return queryStage(event.data);
  if (k === 'counter') return 'challenge';
  if (k === 'followup') return 'search';
  if (k === 'error' && ['review_sources', 'analyze', 'source_review', 'evidence_review'].includes(event.data?.stage)) return 'verify';
  if ((k === 'error' && ['plan', 'candidates', 'candidate_facts', 'extract_candidates', 'candidate_claims'].includes(event.data?.stage)) || (k === 'validation' && event.data?.stage === 'plan')) return 'understand';
  if (['verification', 'source_check', 'validation', 'source_review_start', 'source_review', 'evidence_review_start', 'evidence_review'].includes(k)) return 'verify';
  if (k === 'stopped') return 'answer';
  return wf.current || (k === 'analysis' ? 'search' : 'understand');
}
function workflowReset() {
  wf.events = Object.fromEntries(STAGES.map(s => [s, []])); wf.queries = new Map(); wf.current = null;
  wf.selected = 'understand'; wf.pinned = false; wf.finished = false; wf.interrupted = false;
  renderWorkflow();
}
function workflowFeed(event) {
  const stage = stageOf(event);
  wf.events[stage].push(event);
  if (event.data?.id && event.data?.query && ['search_start', 'search_result', 'error'].includes(event.kind)) wf.queries.set(event.data.id, {...wf.queries.get(event.data.id), ...event.data});
  wf.current = stage;
  if (!wf.pinned) wf.selected = wf.current;
  renderWorkflow();
}
function workflowFinish() {
  wf.finished = true; if (!wf.pinned) wf.selected = 'answer';
  wf.current = 'answer'; renderWorkflow();
}
function reviewEvents(role) {
  const source = role === 'gold';
  return wf.events.verify.filter(e => source ? e.kind.startsWith('source_review') || (e.kind === 'error' && ['review_sources','source_review'].includes(e.data?.stage)) : e.kind.startsWith('evidence_review') || (e.kind === 'error' && ['analyze','evidence_review'].includes(e.data?.stage)));
}
function reviewStatus(role) {
  const events = reviewEvents(role), last = events.at(-1);
  if (!last) return wf.finished ? 'skipped' : 'idle';
  if (last.kind === 'error' || last.data?.status === 'failed') return 'failed';
  if (last.data?.status === 'degraded') return 'degraded';
  if (last.kind.endsWith('_start')) return wf.finished || wf.interrupted ? 'interrupted' : 'active';
  return 'done';
}
function stageStatus(stage) {
  const events = wf.events[stage], last = events.at(-1);
  if (!last) return wf.finished ? 'skipped' : 'idle';
  if (state.report?.service_failures?.some(f => !f.resolved && SERVICE_STAGE[f.stage] === stage)) return 'degraded';
  if (stage === 'verify') {
    const statuses = ['gold','violet'].map(reviewStatus);
    for (const s of ['active','failed','degraded','interrupted']) if (statuses.includes(s)) return s;
    return statuses.every(s => s === 'done') ? 'done' : wf.finished ? 'incomplete' : 'waiting';
  }
  if (last.kind === 'error' || last.data?.status === 'failed') return 'failed';
  if (last.data?.status === 'degraded') return 'degraded';
  if (stage === 'search' || stage === 'challenge') {
    const queries = [...wf.queries.values()].filter(q => queryStage(q) === stage);
    if (queries.some(q => !['done','failed'].includes(q.status))) return wf.finished || wf.interrupted ? 'interrupted' : 'active';
    if (queries.some(q => q.status === 'failed')) return 'failed';
    if (last.kind === 'followup' || last.kind === 'counter') return wf.finished ? 'skipped' : 'waiting';
    return queries.length ? 'done' : wf.finished ? 'skipped' : 'waiting';
  }
  if (stage === 'answer') {
    if (state.report?.stop_code === 'service_incomplete' || state.report?.service_failures?.some(f => !f.resolved)) return 'degraded';
    if (['time_budget','search_budget','cancelled','service_unavailable','incomplete'].includes(state.report?.stop_code)) return 'incomplete';
    return last.kind === 'stopped' ? 'done' : wf.finished ? 'incomplete' : 'waiting';
  }
  if (['plan','candidate_claims'].includes(last.kind)) return 'done';
  return wf.finished || wf.interrupted ? 'interrupted' : 'active';
}
function stageCount(stage) {
  const events = wf.events[stage];
  if (stage === 'search' || stage === 'challenge') { const n = events.filter(e => e.kind === 'search_start').length; return n ? `${n} 次搜索` : ''; }
  if (stage === 'understand') return state.claims.length ? `${state.claims.length} 项主张` : '';
  if (stage === 'verify') { const n = events.filter(e => e.kind === 'source_check').length; return n ? `${n} 个来源` : ''; }
  return state.report ? reportHeadline(state.report) : '';
}
const STATE_LABEL = {idle: '待执行', active: '进行中', done: '记录已返回', skipped: '未执行', failed: '服务失败', degraded: '降级 · 未完成', interrupted: '未完成', waiting: '已计划 · 等待执行', incomplete: '审查不完整'};
function inspectorBody(stage) {
  const events = wf.events[stage];
  const li = (main, sub = '', tone = '') => `<li class="${tone}">${main}${sub ? `<small>${sub}</small>` : ''}</li>`;
  const queryItems = (target) => [...wf.queries.values()].filter(q => queryStage(q) === target).map(q =>
    li(esc(q.query), `${esc(q.id)} · ${esc({direct:'直接检索',counter:'主动反证',followup:'缺口补查'}[q.purpose] || '历史记录未标用途')} · 主张 ${esc((q.claim_ids || []).join('、') || '未标记')} · ${esc(q.reason || '检索公开证据')} · ${q.status === 'done' ? `返回 ${q.result_count ?? 0} 条，新增 ${q.new_sources ?? 0} 个来源` : q.status === 'failed' ? '搜索失败，保留证据缺口' : wf.finished || wf.interrupted ? '未取得返回记录' : '检索中…'}`, q.status === 'failed' ? 'tone-bad' : ''));
  let items = [];
  if (stage === 'understand') {
    items = [li('原问题', esc(state.report?.question || state.question || '尚未提交'))].concat(state.claims.map(c => li(`${esc(c.id)} · ${esc(c.text)}`, `${c.original_question ? `原句：${esc(c.original_question)} · ` : ''}对象：${esc(c.entity || '待澄清')} · 时间：${esc(c.time_scope || '未知')} · 条件：${esc(c.conditions || '未知')}`)));
    if (state.questionType === 'open_question') items.push(li('开放问答：先检索，再提取候选事实', '候选仅是待核查对象；通过两种审查及原文、来源门槛后才进入已查明。'));
    const scope = events.find(e => e.kind === 'plan')?.data?.scope; if (scope) items.push(li('范围', esc(scope)));
  } else if (stage === 'search') {
    items = queryItems('search');
    for (const e of events.filter(e => ['analysis','error','followup'].includes(e.kind))) items.push(li(`${e.kind === 'followup' ? '调整记录 · ' : ''}${esc(e.message)}`, e.kind === 'followup' ? '这是已记录的补查计划；是否执行请核对上方查询返回状态。' : '', e.kind === 'error' ? 'tone-bad' : ''));
  } else if (stage === 'challenge') {
    for (const e of events.filter(e => e.kind === 'counter' || e.kind === 'followup')) items.push(li(esc(e.message), '', 'tone-warn'));
    items = items.concat(queryItems('challenge'));
    for (const e of events.filter(e => e.kind === 'error')) items.push(li(esc(e.message), '', 'tone-bad'));
  } else if (stage === 'verify') {
    const groups = ROLES.verify.map(role => {
      const rows = reviewEvents(role.id).flatMap(e => {
        const heading = li(esc(e.message), `${esc(e.data?.status || e.kind)} · ${((e.elapsed_ms || 0)/1000).toFixed(1)}s`, ['error','degraded','failed'].includes(e.data?.status || e.kind) ? 'tone-bad' : '');
        if (e.kind === 'source_review') return [heading, ...(e.data.reviews || []).map(r => li(`<a href="#source-${esc(r.source_id)}">${esc(r.source_id)} ↗</a> → ${esc(r.claim_id)} · 来源${esc({applicable:'适用', inapplicable:'不适用', unknown:'待确认'}[r.applicability] || '待确认')}`, esc(r.reason)))];
        if (e.kind === 'evidence_review') return [heading, ...(e.data.evidence || []).map(r => li(`${esc(r.id)} → ${esc(r.claim_id)} · ${esc(r.support_degree)} · ${r.page_verified?'原文已匹配':'原文未复现'} · <a href="#source-${esc(r.source_id)}">查看${esc(r.source_id)} ↗</a>`, `原文：${esc(r.quote)} · ${esc(r.reason)}${r.support_degree === 'PARTIAL' ? ' · 仅部分支持，不能据此确定整句事实。' : ''}`))];
        return [heading];
      });
      return `<section class="role-review" id="review-${role.id}" tabindex="-1"><h4>${esc(role.name)} · ${STATE_LABEL[reviewStatus(role.id)]}</h4><p>${esc(role.duty)}</p>${rows.length ? `<ul class="inspector-list">${rows.join('')}</ul>` : '<p>尚无本次审查事件；不推断为已完成。</p>'}</section>`;
    });
    items = events.filter(e => !reviewEvents('gold').includes(e) && !reviewEvents('violet').includes(e)).map(e => {
      const s = e.data?.source_id ? state.sources.get(e.data.source_id) : null;
      const link = s ? ` · <a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.domain || '打开来源')} ↗</a>` : '';
      return li(esc(e.message) + link, e.data?.http_status ? `HTTP ${esc(e.data.http_status)}` : '', e.kind === 'validation' ? 'tone-warn' : '');
    });
    return groups.join('') + coverageDetails() + (items.length ? `<details class="method-detail"><summary>页面读取与引用程序检查</summary><ul class="inspector-list">${items.join('')}</ul></details>` : '');
  } else if (stage === 'answer') {
    const r = state.report;
    if (r) {
      const failed = r.service_failures.some(f => !f.resolved);
      items = [li(failed ? `调查状态：${esc(stopLabel(r))}` : `本次结果：${esc(reportHeadline(r))}`, esc(r.summary)), li(stopLabel(r), esc(r.stop_reason)),
        li(`${r.evidence.length} 条引用记录 · ${r.sources.length} 个来源 · ${r.independent_source_count} 个保守来源组`, `搜索 ${r.queries.length} 次 · 用时 ${(r.elapsed_ms / 1000).toFixed(1)}s`)];
    } else items = events.map(e => li(esc(e.message)));
  }
  return items.length ? `<ul class="inspector-list">${items.join('')}</ul>` : `<div class="inspector-empty">▌${esc(STAGE_INFO[stage].empty)}</div>`;
}
function caseRationale(stage) {
  const question = state.report?.question || state.question;
  const events = wf.events[stage] || [];
  if (!question || !events.length) return '';
  const rows = [];
  if (stage === 'understand') {
    const plan = events.find(e => e.kind === 'plan');
    if (plan?.data?.scope) rows.push(`调查范围：${esc(plan.data.scope)}`);
    for (const claim of state.claims) rows.push(`本次核对：${esc(claim.text)}`);
  } else if (stage === 'search' || stage === 'challenge') {
    for (const query of wf.queries.values()) {
      if (queryStage(query) !== stage || !query.reason) continue;
      rows.push(`查「${esc(query.query)}」：${esc(query.reason)}。${query.status === 'done' ? `实际返回 ${query.result_count ?? 0} 条材料。` : query.status === 'failed' ? '这次查询失败，不能据此排除相关事实。' : '查询已经发起，等待实际返回。'}`);
    }
  } else if (stage === 'verify') {
    const reviews = state.report?.source_reviews || [...state.sourceReviews.values()];
    for (const review of reviews.filter(r => r.reason).slice(0, 4)) {
      const claim = state.claims.find(c => c.id === review.claim_id);
      rows.push(`核对「${esc(claim?.text || review.claim_id)}」时，<a href="#source-${esc(review.source_id)}">${esc(review.source_id)}</a> ${esc({applicable:'适用', inapplicable:'不适用', unknown:'尚待确认'}[review.applicability] || '尚待确认')}：${esc(review.reason)}`);
    }
  } else if (stage === 'answer' && state.report) {
    for (const answer of checkedAnswers(state.report)) {
      rows.push(`回答「${esc(answer.text)}」的依据：${answer.source_ids.map(id => `<a href="#source-${esc(id)}">${esc(id)}</a>`).join('、')} 的原文已与该条回答绑定，通过来源与支持度核验。`);
      for (const evidence of state.report.evidence.filter(e => answer.evidence_ids.includes(e.id)).slice(0, 2)) {
        const source = state.report.sources.find(s => s.id === evidence.source_id);
        rows.push(`<a href="#source-${esc(evidence.source_id)}">${esc(source?.title || evidence.source_id)}</a> 本次实际引用的原文：「${esc(evidence.quote)}」`);
      }
    }
    for (const claim of state.claims.filter(c => (c.missing || []).length).slice(0, 3)) {
      rows.push(`「${esc(claim.text)}」还有待核对：${esc(claim.missing.join('；'))}`);
    }
    if (!rows.length && state.report.stop_reason) rows.push(esc(state.report.stop_reason));
  }
  if (!rows.length) return '';
  const open = $('workflow-inspector')?.querySelector('[data-method]')?.open || false;
  return `<details class="method-detail" data-method ${open ? 'open' : ''}><summary>为什么这样查这个问题（展开）</summary><p>你的问题：${esc(question)}</p><ul class="inspector-list">${rows.map(row => `<li>${row}</li>`).join('')}</ul></details>`;
}
function coverageDetails() {
  const evidence = state.report?.evidence || state.evidence;
  const reviews = state.report?.source_reviews || [...state.sourceReviews.values()];
  const rejected = reviews.filter(r => r.applicability === 'inapplicable');
  return `<details class="method-detail"><summary>主张证据覆盖与排除线索（实际记录）</summary><ul class="inspector-list">${state.claims.map(c => {
    const rows = evidence.filter(e => e.claim_id === c.id);
    return `<li>${esc(c.id)} · ${esc(c.text)}<small>${rows.length ? rows.map(e => `${esc(e.id)} → <a href="#source-${esc(e.source_id)}">${esc(e.source_id)}</a> · ${esc(e.support_degree || '未分级')} · ${e.page_verified === true && e.quote_verified === true ? '原文与逐字匹配已记录' : '原文或逐字匹配未确认'}`).join('；') : '暂无引用绑定；未覆盖不等于主张错误'}</small></li>`;
  }).join('') || '<li>尚无待核查主张</li>'}</ul><p>排除只针对对应主张的适用性，不代表整个来源不可信。</p>${rejected.length ? `<ul class="inspector-list">${rejected.map(r => `<li><a href="#source-${esc(r.source_id)}">${esc(r.source_id)}</a> → ${esc(r.claim_id)}<small>${esc(r.reason || '审查未提供理由')}</small></li>`).join('')}</ul>` : '<p>没有已记录的不适用来源判断，不推断排除理由。</p>'}</details>`;
}
function renderWorkflow() {
  const root = $('workflow-cards'); if (!root) return;
  for (const card of root.querySelectorAll('[data-workflow]')) {
    const stage = card.dataset.workflow, st = stageStatus(stage), count = stageCount(stage);
    card.className = `workflow-card ${st}${wf.selected === stage ? ' selected' : ''}`;
    card.setAttribute('aria-pressed', String(wf.selected === stage));
    card.querySelector('.workflow-state').textContent = STATE_LABEL[st] + (count && st !== 'idle' ? ` · ${count}` : '');
    card.querySelector('.workflow-icon').hidden = true;
    if (!card.querySelector('.role-portraits')) card.querySelector('.workflow-copy').insertAdjacentHTML('beforebegin', `<span class="role-portraits">${ROLES[stage].map(r => `<img class="role-avatar" src="${ROLE_PNG[r.id]}" alt="${esc(r.name)}" width="64" height="64">`).join('')}</span>`);
    if (!card.querySelector('.role-names')) card.querySelector('.workflow-copy').insertAdjacentHTML('beforeend', `<span class="role-names">${ROLES[stage].map(r => `<span class="role-badge role-${r.id}">${esc(r.name)}</span>`).join('')}</span><span class="card-open-hint">查看本次记录 ↗</span>`);
  }
  const stage = wf.selected, info = STAGE_INFO[stage], st = stageStatus(stage);
  $('workflow-inspector').innerHTML = `<div class="inspector-mark">${info.num}</div><div class="inspector-main"><span class="inspector-kicker">${state.history ? '历史回放' : '本次调查'} · ${esc(info.kicker)} · ${STATE_LABEL[st]}</span><strong class="inspector-title">${esc(info.title)}</strong><div class="role-roster">${ROLES[stage].map(r => `<div class="role-profile">${ROLE_PNG[r.id] ? `<img class="role-avatar" src="${esc(ROLE_PNG[r.id])}" alt="${esc(r.name)}头像">` : ''}<${stage === 'verify' ? `a href="#review-${r.id}"` : 'span'} class="role-badge role-${r.id}">${esc(r.name)} · ${esc(r.duty)}</${stage === 'verify' ? 'a' : 'span'}></div>`).join('')}</div><div class="inspector-sub">1 · 本次在做什么</div><p class="inspector-body">${wf.events[stage].length ? esc(wf.events[stage].at(-1).message) : '这一阶段尚未执行。'}</p><div class="inspector-sub">2 · 实际记录</div>${inspectorBody(stage)}${caseRationale(stage)}</div>`;
  $('workflow-inspector').querySelectorAll('.role-avatar').forEach(img => img.addEventListener('error', () => img.remove(), {once:true}));
}
function reset() {
  state.report = null; state.selected = null; state.sources.clear(); state.sourceReviews.clear(); state.evidence = []; state.claims = []; state.searches = 0; state.started = 0; state.history = false; state.questionType = 'verification'; state.question = '';
  workflowReset();
  $('empty-state').hidden = false; $('report-area').hidden = true; $('sources-section').hidden = true;
  $('timeline').innerHTML = ''; $('source-detail').textContent='来源可点击复核'; $('time-detail').textContent='显示实际执行耗时';
  $('answer-sections').innerHTML = ''; $('conflict-area').innerHTML = ''; $('scope-area').innerHTML = ''; $('live-label').textContent='LIVE LOG';
  $('case-number').textContent='NO CASE YET'; $('desk-title').textContent='证据，先于判断'; metrics(); clearError();
}
function log(event, replay=false) {
  const li = document.createElement('li'); li.dataset.kind = event.kind;
  li.innerHTML = `<span class="timeline-node"></span><div><strong>${esc(event.message)}</strong><time>${((event.elapsed_ms||0)/1000).toFixed(1)}s · ${esc(event.kind)}</time>${event.data?.query ? `<p class="query-text">${esc(event.data.query)}</p>` : ''}</div>`;
  $('timeline').appendChild(li); $('timeline').scrollTop = $('timeline').scrollHeight;
  if (replay) { workflowFeed(event); return; }
  if (['plan','candidate_claims'].includes(event.kind)) {state.claims=event.data?.claims || state.claims; state.questionType=event.data?.question_type || state.questionType; showClaims();}
  if (event.kind==='search_start') state.searches++;
  if (event.kind==='search_result') {
    for (const source of event.data.sources||[]) state.sources.set(source.id, source);
    renderSources();
  }
  if (event.kind==='source_check') {
    const source = state.sources.get(event.data.source_id);
    if (source) Object.assign(source, {url_status:event.data.url_status, http_status:event.data.http_status, page_digest:event.data.page_digest, final_url:event.data.final_url, fetched_at:event.data.fetched_at});
    renderSources();
  }
  if (event.kind==='source_review') {
    for (const review of event.data.reviews || []) state.sourceReviews.set(`${review.claim_id}:${review.source_id}`, review);
    renderClaims();
  }
  if (event.kind==='evidence_review') {
    if (event.data?.status === 'degraded') {
      state.evidence = [...new Map([...state.evidence, ...(event.data.evidence || [])].map(e => [e.id,e])).values()];
    } else state.evidence = event.data.evidence || state.evidence;
    state.claims=event.data.claims || state.claims; renderClaims();renderAnswers();renderSources();
  }
  if (event.kind==='analysis') status('分析证据', true);
  if (event.kind==='counter') status('主动寻找反证', true);
  if (event.kind==='verification') status('核验来源', true);
  if (event.kind==='source_review_start') status('判断来源适用性', true);
  if (event.kind==='evidence_review_start') status('判断原文支持度', true);
  if (event.kind==='followup') status('根据证据缺口补查', true);
  workflowFeed(event);
  if (event.kind === 'error' || event.data?.status === 'degraded') renderAnswers();
  metrics();
}
function showClaims() {
  $('empty-state').hidden = true; $('report-area').hidden = false;
  $('verdict-banner').innerHTML='<div class="verdict-top"><span class="verdict-label">调查进行中</span><span class="mono">EVIDENCE FIRST</span></div><p>候选主张尚待核查。执行情况以实际事件为准；检索摘要只作线索。</p>';
  renderClaims(); renderAnswers();
}
function normalizeReport(report) {
  return {claims:[], sources:[], source_reviews:[], evidence:[], conflicts:[], events:[], queries:[], limitations:[], service_failures:[], elapsed_ms:0, independent_source_count:0, ...report};
}
function checkedAnswers(report = null) {
  const r = report || {claims:state.claims, evidence:state.evidence, sources:[...state.sources.values()], source_reviews:[...state.sourceReviews.values()]};
  const proposals = Array.isArray(r.answer_items) ? r.answer_items : r.claims.filter(c => ['支持','反驳'].includes(c.verdict)).map(c => ({claim_id:c.id, text:c.verdict === '支持' ? c.text : `现有原文反驳了“${c.text}”`, evidence_ids:r.evidence.filter(e => e.claim_id === c.id).map(e => e.id)}));
  return proposals.flatMap(item => {
    const c = r.claims.find(c => c.id === item.claim_id);
    if (!c || !['支持','反驳'].includes(c.verdict) || !item.text) return [];
    const evidence = r.evidence.filter(e => e.claim_id === c.id && (item.evidence_ids || []).includes(e.id) && e.quote_verified === true && e.page_verified === true && e.support_degree === (c.verdict === '支持' ? 'FULL' : 'CONTRADICT') && e.relation === (c.verdict === '支持' ? 'support' : 'counter') && r.sources.some(s => s.id === e.source_id) && r.source_reviews.some(v => v.claim_id === c.id && v.source_id === e.source_id && v.applicability === 'applicable'));
    if (!evidence.length) return [];
    return [{...item, evidence_ids:evidence.map(e => e.id), source_ids:[...new Set(evidence.map(e => e.source_id))]}];
  });
}
function renderAnswers() {
  const report = state.report, claims = report?.claims || state.claims, answers = checkedAnswers(report);
  const known = new Set(answers.map(a => a.claim_id));
  const unknown = claims.filter(c => !known.has(c.id) || (c.missing || []).length);
  const failures = report ? report.service_failures : STAGES.flatMap(s => wf.events[s]).filter(e => e.kind === 'error').map(e => ({stage:e.data?.stage || stageOf(e), diagnostic:e.message, resolved:false}));
  const links = item => item.source_ids.map(id => {const s=state.sources.get(id);return `<a href="${esc(safeUrl(s?.url))}" target="_blank" rel="noopener noreferrer" aria-label="打开来源 ${esc(id)}：${esc(s?.title || '')}">[${esc(id)}] ↗</a>`;}).join(' ');
  $('answer-sections').innerHTML = `<section class="answer-known"><h3>已查明${answers.length ? ` · ${answers.length} 项` : ''}</h3>${answers.length ? `<ul>${answers.map(a => `<li>${esc(a.text)} <span class="answer-citations">${links(a)}</span><small>主张 ${esc(a.claim_id)} · 证据 ${esc(a.evidence_ids.join('、'))}</small></li>`).join('')}</ul>` : '<p>目前没有通过核验门槛的事实。候选主张和搜索摘要不会作为确定答案。</p>'}</section><section class="answer-unknown"><h3>尚未查明</h3>${unknown.length ? `<ul>${unknown.map(c => `<li><strong>${esc(c.text)}</strong><p>${esc(c.conclusion || '尚未通过完整核验。')}${(c.missing || []).length ? ` 缺口：${esc(c.missing.join('；'))}` : ''}</p>${c.next_action ? `<small>下一步：${esc(c.next_action)}</small>` : ''}</li>`).join('')}</ul>` : `<p>${answers.length ? '已列主张暂无额外证据缺口；调查是否闭合仍以停止原因及服务执行状态为准。' : state.running ? '调查进行中，待取得可核查主张。' : '未形成可核查候选，不将无结果解释为不存在。'}</p>`}</section>${failures.length ? `<section class="service-failures"><h3>服务执行异常 · 与材料不足分开记录</h3><ul>${failures.map(f => `<li>${esc(f.stage)} · ${f.resolved ? '后续已恢复（保留本次失败记录）' : '尚未恢复，调查未闭合'}<small>${esc(f.diagnostic || '服务未完成')}</small></li>`).join('')}</ul></section>` : ''}`;
}
function citationContext(evidence, source) {
  if (!evidence.quote_verified || !evidence.page_verified || !source?.page_text || !evidence.quote) return '';
  const position = source.page_text.indexOf(evidence.quote);
  if (position < 0) return '';
  const context = source.page_text.slice(Math.max(0, position-160), position+evidence.quote.length+80);
  return `<details class="citation-context"><summary>查看这段引用的相邻原文</summary><blockquote>${esc(context)}</blockquote></details>`;
}
function renderClaims() {
  const claims = state.report?.claims || state.claims;
  if (!state.selected || !claims.some(c=>c.id===state.selected)) state.selected=claims[0]?.id;
  $('claim-tabs').innerHTML=claims.map(c=>`<button class="${c.id===state.selected?'selected':''}" data-claim="${esc(c.id)}" aria-pressed="${c.id===state.selected}">${esc(c.id)} · ${esc(c.verdict==='调查中'?'待核查':displayVerdict(c.verdict) || '待核查')}</button>`).join('');
  const claim = claims.find(c=>c.id===state.selected);
  if (!claim) { $('claim-detail').innerHTML=''; return; }
  const evidence = (state.report?.evidence||state.evidence).filter(e=>e.claim_id===claim.id);
  const relationName={support:'支持证据', counter:'反证 / 限制', context:'背景材料'};
  $('claim-detail').innerHTML=`<div class="claim-content"><h4>${esc(claim.text)}</h4>${claim.original_question ? `<p class="claim-context">保留原问题：${esc(claim.original_question)}</p>` : ''}<div class="claim-context">对象：${esc(claim.entity||'待澄清')}　时间：${esc(claim.time_scope||'未知')}<br>条件：${esc(claim.conditions||'未知')}</div>${claim.conclusion?`<p class="claim-conclusion">${esc(claim.conclusion)}</p>`:''}<div class="evidence-header"><span>主张 ↔ 证据</span><span>${evidence.length} 条引用绑定</span></div>${evidence.length?evidence.map(e=>{
    const s=state.sources.get(e.source_id); return `<article class="evidence-card ${esc(e.relation)}"><div class="evidence-meta"><span>${esc(relationName[e.relation] || '待判断')} · ${esc(e.support_degree||'未分级')}</span><span class="mono">${esc(e.id)} → ${esc(e.source_id)}</span></div><blockquote>“${esc(e.quote)}”</blockquote>${citationContext(e,s)}<p>${esc(e.reason)}${e.support_degree === 'PARTIAL' ? ' 仅片段部分支持，不代表整句事实部分成立；缺少的条件仍需核查。' : ''}</p><a href="${esc(safeUrl(s?.url))}" target="_blank" rel="noopener noreferrer">${esc(s?.title||e.source_id)} ↗</a><div class="evidence-check">${e.quote_verified === true ? '✓ 逐字匹配' : '引用匹配未确认'}${e.quote_origin==='page_text'?'本次读取的原文':'元宝返回文字'} · ${e.page_verified?'✓ 原文已复现':'原文尚未复现'}${Number.isInteger(e.quote_start)?` · 字符 ${e.quote_start}–${e.quote_end}`:''}</div></article>`;
  }).join(''):`<div class="no-evidence">${state.running?'正在查找并核对证据，暂不提前下结论。':'本轮没有满足来源及逐字片段校验的直接证据。没有找到证据，不等于找到了错误证据。'}</div>`}${state.report?`<div class="gap-box"><h5>◌ 仍然缺少什么</h5><ul>${(claim.missing || []).map(m=>`<li>${esc(m)}</li>`).join('')||'<li>请人工复核语义与适用条件，系统不承诺绝对正确。</li>'}</ul><p><strong>下一步：</strong>${esc(claim.next_action)}</p></div><p class="claim-context">证据强度：${esc(claim.strength)}。不是校准后的概率。</p>`:''}</div>`;
  const reviews = (state.report?.source_reviews || [...state.sourceReviews.values()]).filter(r=>r.claim_id===claim.id);
  if (reviews.length) $('claim-detail').insertAdjacentHTML('beforeend', `<details class="scope-box"><summary>来源适用性判断 · ${reviews.length} 项</summary>${reviews.map(r=>`<p><strong>${esc(r.source_id)} · ${esc({applicable:'适用',inapplicable:'不适用',unknown:'待确认'}[r.applicability])}</strong> · ${r.is_primary?'一手候选':'一手身份未确认'}<br>${esc(r.reason)}<br>对象 ${esc(r.entity_match)} · 时间 ${esc(r.time_match)} · 条件 ${esc(r.conditions_match)}</p>`).join('')}</details>`);
  if (state.report) $('claim-detail').insertAdjacentHTML('beforeend',reviewPanel(claim));
}
function reviewPanel(claim) {
  const reviews=(state.report.reviews||[]).filter(r=>r.claim_id===claim.id);
  const conclusions=(state.report.manual_conclusions||[]).filter(c=>c.claim_id===claim.id);
  return `<section class="review-panel"><h4>人工复核台 <span class="tag">不改变自动结论</span></h4><p>没有模型也可以审查材料：选择真实来源，粘贴连续逐字片段，再说明它与主张的关系。操作者标注不等于独立事实认证。</p>
    ${reviews.map(r=>`<article class="evidence-card ${esc(r.relation)}"><div class="evidence-meta"><span>${esc(r.label)}</span><span>${esc(r.id)} → ${esc(r.source_id)}</span></div><blockquote>${esc(r.quote)}</blockquote><p>${esc(r.reason)}</p><div class="evidence-check">${esc(r.reviewer)} · ${date(r.at)} · ${r.page_verified?'亦匹配实时网页':'仅匹配检索返回内容'}</div></article>`).join('')}
    ${conclusions.map(c=>`<div class="manual-conclusion"><strong>人工判断：${esc(c.verdict)}</strong><p>${esc(c.conclusion)}</p><small>${esc(c.reviewer)} · ${esc(c.review_ids.join(', '))} · ${esc(c.label)}</small></div>`).join('')}
    <details id="review-editor"><summary>＋ 添加逐字证据标注</summary><form id="review-form" class="review-form"><label>来源<select name="source_id" required>${[...state.sources.values()].map(s=>`<option value="${esc(s.id)}">${esc(s.id)} · ${esc(s.title.slice(0,55))}</option>`).join('')}</select></label><div id="review-source-text" class="review-source-text" tabindex="0"></div><label>连续逐字引用（10～2000 字符）<textarea name="quote" minlength="10" maxlength="2000" required rows="3" placeholder="从上方材料中复制，不得改写或拼接…"></textarea></label><div class="review-row"><label>证据关系<select name="relation"><option value="context">背景 / 待核查</option><option value="support">支持主张</option><option value="counter">反对 / 限制</option></select></label><label>操作者署名<input name="reviewer" maxlength="60" required placeholder="署名而非账号认证"></label></div><label>关系解释<textarea name="reason" minlength="4" maxlength="1500" required rows="2" placeholder="对象和条件是否匹配？这段材料能证明什么，不能证明什么？"></textarea></label><button class="secondary" type="submit">校验片段并保存标注</button><p class="review-feedback" role="status"></p></form></details>
    ${reviews.length?`<details><summary>＋ 记录人工结论</summary><form id="conclusion-form" class="review-form"><p>本结论会保留署名和依据，与系统自动结论分开。所有本主张标注将一并关联。</p><div class="review-row"><label>人工判断<select name="verdict"><option>证据不足</option><option>未知</option><option>支持</option><option>反驳</option><option>部分成立</option></select></label><label>操作者署名<input name="reviewer" required maxlength="60"></label></div><label>结论及边界<textarea name="conclusion" minlength="4" maxlength="2000" required rows="3"></textarea></label><button class="secondary" type="submit">保存署名结论</button><p class="review-feedback" role="status"></p></form></details>`:''}</section>`;
}
function updateReviewText() {
  const select=$('review-form')?.elements.namedItem('source_id');
  const source=select?state.sources.get(select.value):null;
  if ($('review-source-text')) $('review-source-text').textContent=source?.text||'该来源没有可读文本，不能以它生成逐字引用。';
}
async function saveReview(form,action) {
  const feedback=form.querySelector('.review-feedback'); const submit=form.querySelector('button[type="submit"]');
  const data=Object.fromEntries(new FormData(form)); data.claim_id=state.selected;
  if(action==='conclusions') data.review_ids=(state.report.reviews||[]).filter(r=>r.claim_id===state.selected).map(r=>r.id);
  submit.disabled=true; feedback.textContent='正在校验并保存…';
  try { const report=await (await api(`/api/investigations/${state.report.id}/${action}`,{method:'POST',body:JSON.stringify(data)})).json();renderReport(report);updateReviewText(); }
  catch(error){feedback.textContent=error.message;}
  finally{submit.disabled=false;}
}
function renderSources() {
  const expanded = $('other-sources')?.open || false;
  const evidence = state.report?.evidence || state.evidence;
  const cited = new Set(evidence.map(e => e.source_id));
  const verified = new Set(checkedAnswers(state.report).flatMap(a => a.source_ids));
  const cards = new Map();
  $('sources-section').hidden = !state.sources.size;
  for (const s of state.sources.values()) cards.set(s.id, `<article class="source-card" id="source-${esc(s.id)}" tabindex="-1"><div class="source-meta"><span class="source-id">${esc(s.id)}</span><span>${esc(s.domain)}</span><span>${esc(s.source_type)}</span></div><h4><a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title||s.url)} ↗</a></h4><p>${esc((s.passage||s.text||'无可读摘要').slice(0,240))}</p><div class="source-flags"><span class="flag">${esc(s.url_status)}</span><span class="flag ${s.is_repost?'warn':''}">${s.is_repost?'转载/相似 · 不重复计数':'独立性待核验'}</span><span class="flag">${esc(s.level)}</span></div><details><summary>查看出处与审计信息</summary><p>发布日期：${esc(s.date)}（搜索返回，未独立核定）<br>检索时间：${date(s.retrieved_at)}<br>请求 ID：${esc(s.request_id||'无')}<br>命中查询：${esc(s.query_ids?.join(', '))}<br>保守来源组：${esc(s.independence_group)}<br>${esc(s.independence_note)}</p><p class="digest">SHA-256 ${esc(s.digest)}</p><p>${esc(s.url)}</p></details></article>`);
  const used = [...state.sources.values()].filter(s => cited.has(s.id)).sort((a,b) => Number(verified.has(b.id))-Number(verified.has(a.id)));
  const other = [...state.sources.values()].filter(s => !cited.has(s.id));
  $('source-list').innerHTML = `<p class="source-group-label">实际引用的来源 · ${used.length} 个（引用绑定不等于审查通过）</p>${used.map(s => cards.get(s.id)).join('') || '<p class="source-group-label">尚无实际引用绑定；已检索来源保留在下方。</p>'}${other.length ? `<details id="other-sources" class="other-sources" ${expanded ? 'open' : ''}><summary>其他检索来源 · ${other.length} 个（展开保留记录）</summary><div class="source-list">${other.map(s => cards.get(s.id)).join('')}</div></details>` : ''}`;
}
function renderReport(report, fromHistory=false) {
  report=normalizeReport(report);
  state.report=report; state.claims=report.claims; state.sources=new Map(report.sources.map(s=>[s.id,s])); state.searches=report.queries.length; state.questionType=report.question_type || 'verification'; state.question=report.question; state.history=fromHistory || state.history;
  $('empty-state').hidden=true; $('report-area').hidden=false; $('desk-title').textContent='调查结果';
  $('case-number').textContent='CASE '+report.id.slice(0,8).toUpperCase();
  const color=['未知','证据不足','部分成立'].includes(report.verdict)?'warn':report.verdict==='反驳'?'counter':'';
  const incomplete=['service_incomplete','service_unavailable'].includes(report.stop_code) || report.service_failures.some(f => !f.resolved);
  const headline = reportHeadline(report);
  $('desk-title').textContent='调查结果 · '+headline;
  $('verdict-banner').innerHTML=`<div class="verdict-top"><span class="verdict-label ${color}">◎ ${esc(headline)}</span><span class="mono">${state.history ? '历史报告回放' : '本次结果'} · ${esc(stopLabel(report))}${incomplete && report.stop_code !== 'service_incomplete' ? ' · 服务未恢复，调查未闭合' : ''}</span></div><p>${incomplete ? '服务执行尚未完成，本轮调查未闭合；已通过核验的内容仍保留在下方。' : esc(report.summary)}</p>${incomplete ? `<details class="verdict-detail"><summary>查看当前证据判断与报告摘要</summary><p>${esc(displayVerdict(report.verdict))} · ${esc(report.summary)}</p></details>` : ''}<div class="analysis-mode">${esc(report.analysis_mode)} · 结论不是绝对事实认证</div>`;
  renderClaims(); renderSources(); renderAnswers();
  updateReviewText();
  $('conflict-area').innerHTML=report.conflicts.map(c=>`<div class="conflict-box"><h4>⇄ ${esc(c.kind)} · ${esc(c.source_ids.join(' ↔ '))}</h4><p>${esc(c.explanation)}</p><span class="mono">${c.resolved?'已给出解释，仍请复核':'未消除，保留冲突'} · ${esc(c.review)}</span></div>`).join('');
  $('scope-area').innerHTML=`<div class="scope-box"><strong>为什么停止调查 · ${esc(stopLabel(report))}</strong><br>${esc(report.stop_reason)}<details><summary>调查范围与局限</summary><p>${esc(report.scope)}</p><ul>${report.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details></div>`;
  $('source-detail').textContent=`${report.independent_source_count} 个保守来源组 · 非原创性认证`;
  $('time-detail').textContent=report.first_result_ms?`首批来源 ${(report.first_result_ms/1000).toFixed(1)}s`:'未获得可用来源';
  if (fromHistory) { $('timeline').innerHTML=''; workflowReset(); for (const e of report.events) log(e,true); $('question').value=report.question; }
  $('live-label').textContent=state.history ? '历史事件回放' : '本次执行记录';
  workflowFinish();
  status('调查已保存'); metrics(); loadHistory();
}
async function consumeEvents(response) {
  const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer=''; let completed=false;
  while(true) {
    const {done,value}=await reader.read(); if(done) break;
    buffer+=decoder.decode(value,{stream:true});
    let boundary;
    while((boundary=buffer.indexOf('\n\n'))>=0) {
      const block=buffer.slice(0,boundary); buffer=buffer.slice(boundary+2);
      const type=block.match(/^event: (.+)$/m)?.[1]; const data=block.match(/^data: (.+)$/m)?.[1];
      if(!data) continue; const event=JSON.parse(data);
      if(type==='progress') log(event);
      if(type==='complete') {completed=true; if(event.report) renderReport(event.report); else throw new Error('调查异常中止，没有生成报告。请重试并检查本地诊断。');}
    }
  }
  if(!completed) throw new Error('实时连接中断。调查可能仍在服务器运行，可稍后刷新历史记录。');
}
async function start(event) {
  event?.preventDefault(); if(state.running) return;
  const question=$('question').value.trim(); if(question.length<4) return showError('请至少输入 4 个字。');
  reset(); state.question=question; state.running=true; state.started=Date.now(); $('start').disabled=true; $('cancel').hidden=false; $('new-case').disabled=true;
  status('建立调查',true); state.timer=setInterval(metrics,100);
  try {
    const {id}=await (await api('/api/investigations',{method:'POST',body:JSON.stringify({question})})).json(); state.job=id;
    $('case-number').textContent='CASE '+id.slice(0,8).toUpperCase();
    await consumeEvents(await api(`/api/investigations/${id}/events`));
  } catch(error) {showError(error.message);status('需要检查');wf.interrupted=true;renderWorkflow();renderAnswers();}
  finally {state.running=false;clearInterval(state.timer);$('start').disabled=false;$('cancel').hidden=true;$('new-case').disabled=false;metrics();}
}
async function loadHistory() {
  try {const history=await (await api('/api/history')).json();$('history').innerHTML=history.length?history.map(h=>`<button data-history="${esc(h.id)}">${esc(h.question.slice(0,36))}${h.question.length>36?'…':''}<small>${esc(reportHeadline(h))} · ${(h.elapsed_ms/1000).toFixed(1)}s</small></button>`).join(''):'<p class="muted">还没有已保存的调查</p>';} catch {}
}
async function health() {
  try {const data=await (await api('/api/health')).json();$('connection').textContent=data.search_configured?`元宝密钥已配置${data.reasoning_model?' · '+data.reasoning_model:''}`:'元宝密钥未配置'; if(!data.reasoning_configured) showError('当前未启用推理服务：仅执行真实元宝搜索和来源核验，不自动判断事实真假。不会自动选用其他模型。');} catch(error){$('connection').textContent='服务需检查';showError(error.message);}
}
function download(content,name,type) {const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([content],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
$('workflow-cards').addEventListener('click',e=>{const b=e.target.closest('[data-workflow]');if(b){wf.selected=b.dataset.workflow;wf.pinned=true;renderWorkflow();}});
$('investigation-form').addEventListener('submit',start);
$('claim-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-claim]');if(b){state.selected=b.dataset.claim;renderClaims();updateReviewText();}});
$('claim-detail').addEventListener('change',e=>{if(e.target.name==='source_id')updateReviewText();});
$('claim-detail').addEventListener('submit',e=>{if(e.target.id==='review-form'||e.target.id==='conclusion-form'){e.preventDefault();saveReview(e.target,e.target.id==='review-form'?'reviews':'conclusions');}});
$('history').addEventListener('click',async e=>{const b=e.target.closest('[data-history]');if(!b||state.running)return;try{reset();renderReport(await (await api('/api/investigations/'+b.dataset.history)).json(),true);if($('history-dialog').open)$('history-dialog').close();}catch(error){showError(error.message);}});
$('new-case').addEventListener('click',()=>{if(!state.running){reset();status('准备就绪');$('question').value='';$('question').focus();}});
$('cancel').addEventListener('click',async()=>{if(state.job){await api(`/api/investigations/${state.job}/cancel`,{method:'POST'});status('停止后续请求',true);}});
$('refresh-history').addEventListener('click',loadHistory);
$('auth-button').addEventListener('click',()=>{const token=prompt('请输入部署者提供的访问口令；仅保存在当前标签页会话中。');if(token!==null){sessionStorage.setItem('ed-token',token);clearError();health();loadHistory();}});
$('export-md').addEventListener('click',async()=>{if(!state.report)return;try{const text=await (await api(`/api/investigations/${state.report.id}/export`)).text();download(text,`证据侦探-${state.report.id}.md`,'text/markdown;charset=utf-8');}catch(error){showError(error.message);}});
$('export-json').addEventListener('click',()=>{if(state.report)download(JSON.stringify(state.report,null,2),`evidence-${state.report.id}.json`,'application/json');});
$('history-toggle').addEventListener('click', () => {
  $('history-dialog-content').appendChild($('history'));
  $('history-dialog').showModal();
  loadHistory();
});
$('history-close').addEventListener('click', () => $('history-dialog').close());
$('history-dialog').addEventListener('close', () => {
  $('history-home').appendChild($('history'));
  $('history-toggle').focus();
});
function revealAnchor(hash) {
  let id; try { id = decodeURIComponent(hash.slice(1)); } catch { return; }
  const target = document.getElementById(id);
  if (!target || !id.startsWith('source-')) return;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
  target.scrollIntoView({block:'center'}); target.focus({preventScroll:true});
}
document.addEventListener('click', e => {
  const link = e.target.closest('a[href^="#source-"]');
  if (link) { e.preventDefault(); history.replaceState(null, '', link.getAttribute('href')); revealAnchor(link.getAttribute('href')); }
});
window.addEventListener('hashchange', () => revealAnchor(location.hash));
workflowReset();health();loadHistory();
