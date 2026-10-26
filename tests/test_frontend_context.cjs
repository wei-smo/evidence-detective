const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8').split("$('workflow-cards').addEventListener")[0];
vm.runInNewContext(source + `
assert.equal(typeof caseRationale, 'function');
assert.equal(typeof citationContext, 'function');
assert.ok(citationContext({quote:'venv — 创建虚拟环境。3.3 新版功能。',page_verified:true,quote_verified:true},
  {page_text:'Python 标准库 » 软件打包和分发 » venv — 创建虚拟环境。3.3 新版功能。'}).includes('Python 标准库'));
assert.equal(typeof displayVerdict, 'function');
assert.equal(displayVerdict('证据不足'), '尚未查明');
assert.equal(reportHeadline({stop_code:'service_incomplete'}), '执行未完成');
assert.equal(reportHeadline({question_type:'open_question', answer_count:0, verdict:'证据不足'}), '尚未查明');
wf.events = Object.fromEntries(STAGES.map(s => [s, []]));
assert.equal(caseRationale('answer'), '', 'no fabricated explanation before a question executes');
state.question = '张飞是谁？';
wf.queries.set('Q1', {id:'Q1', query:'张飞 历史人物 史料', purpose:'direct', reason:'核对历史张飞的身份', status:'done', result_count:8});
wf.events.search.push({kind:'search_start', data:{id:'Q1'}});
const why = caseRationale('search');
assert.ok(why.includes('张飞是谁？'));
assert.ok(why.includes('张飞 历史人物 史料'));
assert.ok(why.includes('核对历史张飞的身份'));
assert.ok(!why.includes('能导出') && !why.includes('论文原文') && !why.includes('通俗原理'));
state.question = '<script>alert(1)</script>';
assert.ok(!caseRationale('search').includes('<script>'), 'question is escaped');
state.question = '张飞是谁？';
state.report = {question:state.question, claims:[{id:'C1', text:'张飞是蜀汉将领', verdict:'支持'}],
  answer_items:[{claim_id:'C1', text:'张飞是蜀汉将领', evidence_ids:['E1']}],
  sources:[{id:'S1',title:'本次引用的历史材料'}],
  source_reviews:[{claim_id:'C1',source_id:'S1',applicability:'applicable'}],
  evidence:[{id:'E1',claim_id:'C1',source_id:'S1',quote:'这段本次取得的原文明确描述张飞的将领身份。',
    quote_verified:true,page_verified:true,support_degree:'FULL',relation:'support'}]};
state.claims=state.report.claims;
wf.events.answer.push({kind:'stopped'});
assert.ok(caseRationale('answer').includes('这段本次取得的原文明确描述张飞的将领身份。'));
assert.ok(caseRationale('answer').includes('本次引用的历史材料'));
`, {assert, URL, document:{getElementById:() => null}});
console.log('PASS: question-specific rationale, real query purpose, no generic method before execution, escaped content');
