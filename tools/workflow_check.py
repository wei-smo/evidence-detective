"""Read-only browser acceptance plus explicitly synthetic frontend protocol scenarios.

Never starts investigations or submits reviews. Synthetic scenarios are UI checks,
not real search results and cannot satisfy L01-L03 or independent review A15.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--report-id', action='append', default=[], help='Read and screenshot a specific genuine saved report; may repeat')
    args = parser.parse_args()
    folder = ROOT / 'artifacts' / 'frontend-workflow' / datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True, exist_ok=False)
    result = {'executed_at': datetime.now().astimezone().isoformat(), 'executor': 'frontend implementer / Chromium',
              'scope': 'read-only saved reports and synthetic UI event scenarios; no live query',
              'checks': [], 'errors': [], 'A13': 'image loading checked; visual crop review recorded separately',
              'A15': 'NOT_RUN: requires an independent reviewer', 'L01-L03': 'NOT_RUN'}

    def check(condition, name):
        result['checks'].append({'name': name, 'status': 'PASS' if condition else 'FAIL'})
        if not condition:
            raise AssertionError(name)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('pageerror', lambda error: result['errors'].append(str(error)))
        try:
            page.goto(args.base_url, wait_until='networkidle')
            check(page.locator('[data-workflow]').count() == 5, 'A11: five workflow cards')
            check(page.locator('.role-names .role-badge').count() == 6, 'A11: six named text identities')
            check(page.locator('#workflow-cards img').count() == 6, 'six supplied character portraits in five cards')
            page.wait_for_function("Array.from(document.querySelectorAll('#workflow-cards img')).every(i => i.complete && i.naturalWidth > 0)")
            check(page.locator('#workflow-cards img').evaluate_all('(items)=>items.every(i=>i.naturalWidth>0)'), 'all six portrait assets load')
            for stage in ('understand', 'search', 'challenge', 'verify', 'answer'):
                page.locator(f'[data-workflow="{stage}"]').focus()
                page.keyboard.press('Enter')
                check(page.locator(f'[data-workflow="{stage}"]').get_attribute('aria-pressed') == 'true', f'A11: keyboard entry {stage}')
                text = page.locator('#workflow-inspector').inner_text()
                check(text.index('1 · 本次在做什么') < text.index('2 · 实际记录') < text.index('3 · 为什么这样查'), f'A11: task / record / method order {stage}')
                if not page.locator('[data-method]').evaluate('e => e.open'):
                    page.locator('[data-method] summary').click()
                check(page.locator('[data-method] a').is_visible(), f'A11: original paper link {stage}')
            page.locator('[data-workflow="verify"]').click()
            check(page.locator('#review-gold').count() == page.locator('#review-violet').count() == 1, 'A10: separate source and support review sections')
            font = page.evaluate("document.fonts.ready.then(() => ({family:getComputedStyle(document.querySelector('#question')).fontFamily,loaded:document.fonts.check('14px \"Detective CJK\"')}))")
            result['font'] = font
            check(font['loaded'] and 'Detective CJK' in font['family'], 'A12: installed Chinese font loaded in input')
            client = page.context.new_cdp_session(page)
            client.send('DOM.enable')
            client.send('CSS.enable')
            root = client.send('DOM.getDocument')['root']['nodeId']
            actual_fonts = {}
            for selector in ('#question', '.inspector-title', '#review-gold h4'):
                node = client.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
                fonts = client.send('CSS.getPlatformFontsForNode', {'nodeId': node})['fonts']
                actual_fonts[selector] = [f['familyName'] for f in fonts if f['glyphCount'] > 0]
            result['platform_fonts'] = actual_fonts
            check(all(any('Fusion Pixel' in f for f in fonts) for fonts in actual_fonts.values()), 'A12: browser confirms actual Fusion Pixel CJK glyphs for input / explanation / review')
            for width, height in ((836, 686), (1440, 1100), (390, 844)):
                page.set_viewport_size({'width': width, 'height': height})
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'A12: {width}px home no horizontal overflow')
                page.screenshot(path=str(folder / f'home-{width}.png'), full_page=True)
            check(page.locator('.metrics>div').first.evaluate("e => getComputedStyle(e).boxShadow === 'none' && getComputedStyle(e).cursor === 'default'"), 'A12: pure metrics have no button shadow/cursor')

            # Existing saved report: genuine historical data, never called a new query.
            history = page.request.get(args.base_url + '/api/history').json()
            if history:
                saved = page.request.get(args.base_url + '/api/investigations/' + history[0]['id']).json()
                result['historical_report'] = {'id': saved['id'], 'has_answer_items': 'answer_items' in saved}
                page.evaluate('r => { reset(); renderReport(r, true); }', saved)
                check('历史' in page.locator('#live-label').inner_text(), 'A14: genuine saved report explicitly marked historical')
                check(page.locator('#timeline li').count() == len(saved.get('events', [])), 'A14: historical events replay without fabricated stopped event')
                check(page.locator('#source-list a').count() >= len(saved.get('sources', [])), 'A14: historical sources remain clickable')
                with page.expect_download() as download:
                    page.locator('#export-json').click()
                exported = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
                check(exported['id'] == saved['id'], 'A14: JSON export preserves saved report')
                with page.expect_download() as download:
                    page.locator('#export-md').click()
                check(Path(download.value.path()).read_text(encoding='utf-8').startswith('# 证据侦探'), 'A14: existing Markdown endpoint downloads saved report')
                for width in (836, 1440):
                    page.set_viewport_size({'width': width, 'height': 686 if width == 836 else 1100})
                    check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'A12/A14: {width}px actual saved report no overflow')
                    page.screenshot(path=str(folder / f'historical-{width}.png'), full_page=True)
            for identifier in args.report_id:
                response = page.request.get(args.base_url + '/api/investigations/' + identifier)
                check(response.ok, f'real saved report {identifier} is available')
                report = response.json()
                page.evaluate('r => { reset(); renderReport(r, true); }', report)
                result.setdefault('real_saved_reports', []).append({
                    'id': identifier, 'verdict': report['verdict'], 'stop_code': report.get('stop_code'),
                    'answer_count': len(report.get('answer_items', [])), 'service_failure_count': len(report.get('service_failures', [])),
                    'elapsed_ms': report['elapsed_ms'], 'searches': len(report['queries']), 'classification': 'genuine saved report, not a new query'})
                if report.get('answer_items'):
                    check(page.locator('.answer-known li').count() > 0, f'real saved report {identifier}: checked answers visible')
                if report.get('service_failures'):
                    check(page.locator('.service-failures').count() == 1, f'real saved report {identifier}: service failures visible')
                    if all(f.get('resolved') for f in report['service_failures']):
                        check('后续已恢复' in page.locator('.service-failures').inner_text(), f'real saved report {identifier}: historical failures explicitly recovered')
                        check('degraded' not in page.locator('[data-workflow="verify"]').get_attribute('class') and 'failed' not in page.locator('[data-workflow="verify"]').get_attribute('class'), f'real saved report {identifier}: recovered review is not stuck failed')
                check(not page.evaluate("Array.from(document.querySelectorAll('[data-workflow]')).some(e => getComputedStyle(e).overflow === 'hidden' || e.scrollWidth > e.clientWidth + 2)"), f'real saved report {identifier}: workflow card text not clipped')
                for width in (836, 1440):
                    page.set_viewport_size({'width': width, 'height': 686 if width == 836 else 1100})
                    check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'real saved report {identifier}: {width}px no overflow')
                    page.screenshot(path=str(folder / f'report-{identifier}-{width}.png'), full_page=True)

            # Explicit synthetic protocol regression: not evidence of a real investigation.
            page.evaluate("""() => {
                reset(); state.running=true; state.question='前端协议场景：不是实际查询';
                log({kind:'started',message:'协议场景开始',data:{}});
                log({kind:'plan',message:'开放题检索目标',data:{question_type:'open_question',claims:[]}});
                log({kind:'search_start',message:'直接搜索',data:{id:'Q1',query:'协议直接',purpose:'direct',round:1,status:'running'}});
                log({kind:'search_result',message:'直接返回',data:{id:'Q1',query:'协议直接',purpose:'direct',round:1,status:'done',sources:[]}});
                log({kind:'candidate_claims_start',message:'整理候选，尚未核实',data:{}});
            }""")
            check('active' in page.locator('[data-workflow="understand"]').get_attribute('class'), 'A10 synthetic: candidate extraction start returns to understand')
            page.evaluate("""() => {
                log({kind:'candidate_claims',message:'未取得候选',data:{status:'degraded',accepted_count:0,claims:[],question_type:'open_question'}});
                log({kind:'source_review_start',message:'来源审查开始',data:{}});
                log({kind:'source_review',message:'来源审查返回',data:{status:'completed',reviews:[]}});
                log({kind:'evidence_review_start',message:'支持度审查开始',data:{}});
                log({kind:'error',message:'支持度审查超时',data:{stage:'analyze',diagnostic:'timeout'}});
                log({kind:'evidence_review',message:'支持度审查降级',data:{status:'degraded',evidence:[],claims:[]}});
            }""")
            page.locator('[data-workflow="verify"]').click()
            check('记录已返回' in page.locator('#review-gold').inner_text() and '降级' in page.locator('#review-violet').inner_text(), 'A10 synthetic: independent review completion and degraded statuses')
            page.evaluate("""() => {
                wf.pinned=false;
                log({kind:'followup',message:'回到寻找证据补查',data:{round:3}});
                log({kind:'search_start',message:'缺口补查开始',data:{id:'Q3',query:'协议补查',purpose:'followup',round:3,status:'running'}});
            }""")
            check(page.locator('[data-workflow="search"]').get_attribute('aria-pressed') == 'true', 'A09/A10 synthetic: followup purpose routes back to search')
            check('协议补查' in page.locator('#workflow-inspector').inner_text(), 'A09 synthetic: actual followup query visible in search')
            page.locator('[data-workflow="challenge"]').click()
            check('协议补查' not in page.locator('#workflow-inspector').inner_text(), 'A09 synthetic: followup never mislabeled counter')
            page.evaluate("""() => {
                log({kind:'search_start',message:'首轮反证',data:{id:'Q4',query:'协议反证',purpose:'counter',round:1,status:'running'}});
                log({kind:'error',message:'反证服务失败',data:{id:'Q4',query:'协议反证',purpose:'counter',round:1,status:'failed'}});
            }""")
            check('协议反证' in page.locator('#workflow-inspector').inner_text(), 'A10 synthetic: counter routes by purpose even round one')
            check('服务失败' in page.locator('[data-workflow="challenge"]').inner_text(), 'A10 synthetic: failed query not stuck searching')

            fixture = {
                'id': 'frontendfixture1', 'question': '前端协议场景，不是实际查询', 'question_type': 'open_question',
                'verdict': '证据不足', 'summary': '协议场景：保留先前通过的事实，审查超时。',
                'stop_code': 'service_incomplete', 'stop_reason': '后续审查超时，调查未闭合',
                'claims': [{'id': 'C1', 'text': '协议场景：已核查的原子事实', 'verdict': '支持', 'original_question': '原问题', 'missing': []},
                           {'id': 'C2', 'text': '协议场景：未知条件', 'verdict': '证据不足', 'missing': ['缺少条件证据']}],
                'sources': [{'id': 'S1', 'title': '协议测试来源', 'url': 'https://docs.python.org/3/library/venv.html', 'text': 'This is a frontend fixture, not retrieved evidence.'}],
                'source_reviews': [{'source_id': 'S1', 'claim_id': 'C1', 'applicability': 'applicable', 'is_primary': True}],
                'evidence': [{'id': 'E1', 'source_id': 'S1', 'claim_id': 'C1', 'quote': 'This is a frontend fixture, not retrieved evidence.', 'quote_verified': True, 'page_verified': True, 'support_degree': 'FULL', 'relation': 'support'}],
                'answer_items': [{'claim_id': 'C1', 'text': '协议场景：已核查的原子事实', 'evidence_ids': ['E1'], 'source_ids': ['S1']}],
                'service_failures': [{'stage': 'analyze', 'diagnostic': 'timeout', 'resolved': False}],
                'events': [], 'queries': [], 'elapsed_ms': 5000,
            }
            page.evaluate('r => renderReport(r)', fixture)
            check('已核查的原子事实' in page.locator('.answer-known').inner_text() and '[S1]' in page.locator('.answer-known').inner_text(), 'A04/A07 synthetic: checked fact and citation survive later timeout')
            check('未知条件' in page.locator('.answer-unknown').inner_text() and 'timeout' in page.locator('.service-failures').inner_text(), 'A04/A07 synthetic: unknowns and service failures remain separate')
            check('调查未闭合' in page.locator('#verdict-banner').inner_text(), 'A07 synthetic: nonempty answer_items does not close investigation')
            fixture['stop_code'] = 'time_budget'
            page.evaluate('r => renderReport(r)', fixture)
            check('调度时间预算已用尽' in page.locator('#verdict-banner').inner_text() and 'timeout' in page.locator('.service-failures').inner_text(), 'A07 synthetic: time budget stop and unresolved service error both remain visible')
            check('已核查的原子事实' in page.locator('.answer-known').inner_text(), 'A07 synthetic: time budget does not clear checked answers')
            fixture.pop('answer_items')
            fixture.pop('question_type')
            page.evaluate('r => renderReport(r)', fixture)
            check('已核查的原子事实' in page.locator('.answer-known').inner_text(), 'A14 synthetic: old report falls back to checked claims and evidence')
            fixture['evidence'][0]['support_degree'] = 'PARTIAL'
            page.evaluate('r => renderReport(r)', fixture)
            check('已核查的原子事实' not in page.locator('.answer-known').inner_text(), 'A05/A08 synthetic: PARTIAL cannot become a checked answer')
            fixture['evidence'][0]['support_degree'] = 'FULL'
            fixture['evidence'][0]['page_verified'] = False
            page.evaluate('r => renderReport(r)', fixture)
            check('已核查的原子事实' not in page.locator('.answer-known').inner_text(), 'A08 synthetic: search snippet cannot become a checked answer')
            fixture['evidence'][0]['page_verified'] = True
            fixture['stop_code'] = 'sufficient'
            fixture['service_failures'][0]['resolved'] = True
            page.evaluate('r => renderReport(r)', fixture)
            check('后续已恢复' in page.locator('.service-failures').inner_text() and '尚未恢复' not in page.locator('.service-failures').inner_text(), 'A07 synthetic: recovered timeout is historical, not active failure')
            check(not result['errors'], 'no browser JavaScript exceptions')
        except Exception as exc:
            result['failure'] = str(exc)
            page.screenshot(path=str(folder / 'failure.png'), full_page=True)
            raise
        finally:
            (folder / 'checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(str(folder / 'checks.json'))
            print(json.dumps({'checks': len(result['checks']), 'failed': [c for c in result['checks'] if c['status'] == 'FAIL'], 'errors': result['errors']}, ensure_ascii=True))
            browser.close()


if __name__ == '__main__':
    main()
