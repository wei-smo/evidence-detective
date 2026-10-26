"""A fresh real browser submission plus question-specific UI acceptance.

This invokes the locally configured Yuanbao/model services once for the question.
The saved report and screenshots are new evidence, not historical replay.
"""
import json
import argparse
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'artifacts' / 'user-flow' / datetime.now().strftime('%Y%m%d-%H%M%S')
BASE = 'http://127.0.0.1:8765'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume-id', help='Reconnect the same newly submitted job; never sends a second POST')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'fresh_real_query': True, 'question': '张飞是谁？', 'checks': [], 'errors': []}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={'width':1440, 'height':1000})
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        try:
            page.goto(BASE, wait_until='networkidle')
            for card in page.locator('[data-workflow]').all():
                card.click()
                assert page.locator('[data-method]').count() == 0, 'no rationale before any question executes'
            assert '通俗原理' not in page.locator('#workflow-inspector').inner_text()
            assert '论文原文' not in page.locator('#workflow-inspector').inner_text()
            result['checks'].append('generic method examples and paper links withdrawn; no invented rationale on empty form')
            page.locator('#question').fill(result['question'])
            if args.resume_id:
                identifier = args.resume_id
                result['resumed_same_fresh_job'] = True
                page.evaluate("""(id) => { reset(); state.question = '张飞是谁？'; state.running = true;
                    state.job=id; api('/api/investigations/'+id+'/events')
                        .then(consumeEvents).finally(()=>{state.running=false;}); }""", identifier)
            else:
                with page.expect_response(lambda r: r.url.endswith('/api/investigations') and r.request.method == 'POST') as started:
                    page.locator('#start').click()
                identifier = started.value.json()['id']
            result['report_id'] = identifier
            print('REAL_BROWSER_JOB '+identifier, flush=True)
            page.wait_for_function("() => document.querySelector('#run-status').textContent === '调查已保存'", timeout=330000)
            report = page.request.get(BASE+'/api/investigations/'+identifier).json()
            (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            result.update({k:report[k] for k in ['stop_code','elapsed_ms','verdict']})
            result['answer_count'] = len(report.get('answer_items',[]))
            result['search_count'] = len(report['queries'])
            result['service_failures'] = report['service_failures']
            assert report['question_type'] == 'open_question'
            for stage in ['understand','search','challenge','verify','answer']:
                page.locator(f'[data-workflow="{stage}"]').click()
                why = page.locator('[data-method]')
                assert why.count() == 1, stage+' needs actual question-specific rationale'
                why.evaluate('(e)=>e.open=true')
                text = why.inner_text()
                assert result['question'] in text
                assert '能导出' not in text and '通俗原理' not in text and '论文原文' not in text
                if stage in ['search','challenge']:
                    assert any(q['query'] in text for q in report['queries']
                               if (q['purpose']=='counter') == (stage=='challenge'))
            result['checks'].append('five stages explain this question using actual query purposes, reviews, and checked answers')
            assert page.locator('.role-badge').count() >= 6
            assert page.locator('#review-gold').count() == 0  # answer stage, separate review remains on verify
            page.locator('[data-workflow="verify"]').click()
            assert page.locator('#review-gold').count() == page.locator('#review-violet').count() == 1
            page.locator('[data-workflow="answer"]').click()
            for width in [836,1440]:
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if width == 836:
                    page.locator('#history-toggle').click()
                    assert page.locator('#history-dialog').is_visible()
                    assert page.locator(f'[data-history="{identifier}"]').count() == 1
                    page.locator('#history-close').click()
                page.locator('[data-method]').evaluate('(e)=>e.open=true')
                page.locator('#workflow-inspector').screenshot(path=str(OUT/f'why-{width}.png'))
                page.screenshot(path=str(OUT/f'report-{width}.png'),full_page=True)
            result['checks'].append('836/1440 no overflow; 836 history opens and includes the fresh query')
            assert not result['errors'], result['errors']
            assert report.get('answer_items'), 'real question still lacks a checked answer; do not declare success'
            assert '已查明' in page.locator('#verdict-banner').inner_text()
            assert '证据不足' not in page.locator('#verdict-banner').inner_text()
            result['checks'].append('fresh question returns checked answer with source links, not a material-shortage placeholder')
            result['status'] = 'PASS'
        except Exception as exc:
            result['status'] = 'FAIL'
            result['failure'] = str(exc)
            page.screenshot(path=str(OUT/'failure.png'),full_page=True)
            raise
        finally:
            (OUT/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
            print('ARTIFACTS '+str(OUT),flush=True)
            browser.close()


if __name__ == '__main__':
    main()
