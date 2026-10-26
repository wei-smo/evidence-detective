"""Browser smoke test. History is read-only; --live calls configured services."""
import argparse
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--report-id', help='Check a saved report without new service calls')
    args = parser.parse_args()
    folder = ROOT / 'artifacts' / 'acceptance' / args.report_id / 'browser' if args.report_id else ROOT / 'artifacts' / 'browser'
    folder.mkdir(parents=True, exist_ok=True)
    results = {'browser': 'Chromium', 'checks': [], 'errors': []}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel='msedge')
        context = browser.new_context(viewport={'width': 1440, 'height': 1100}, device_scale_factor=1)
        page = context.new_page()
        page.on('pageerror', lambda e: results['errors'].append(str(e)))
        page.goto(args.base_url, wait_until='networkidle')
        page.get_by_role('heading', name='让每一个结论， 都有迹可循。').wait_for()
        health = page.request.get(args.base_url + '/api/health').json()
        assert ('元宝密钥已配置' if health['search_configured'] else '元宝密钥未配置') in page.locator('#connection').inner_text()
        if health['reasoning_configured']:
            assert health['reasoning_model'] in page.locator('#connection').inner_text()
        else:
            assert '未启用推理服务' in page.locator('#error-banner').inner_text()
        results['checks'].append('desktop home + actual configured model state')
        workflow_cards = page.locator('#workflow-cards [data-workflow]')
        assert workflow_cards.count() == 5
        workflow_cards.nth(2).click()
        assert workflow_cards.nth(2).get_attribute('aria-pressed') == 'true'
        assert '站在对立面再查一遍' in page.locator('#workflow-inspector').inner_text()
        workflow_cards.nth(4).click()
        assert workflow_cards.nth(4).get_attribute('aria-pressed') == 'true'
        assert '证据够才下结论' in page.locator('#workflow-inspector').inner_text()
        results['checks'].append('workflow cards switch inspector details and selected state')
        page.screenshot(path=str(folder / 'desktop-home.png'), full_page=True)
        history = page.locator('[data-history]')
        if history.count() or args.report_id:
            selected = page.locator(f'[data-history="{args.report_id}"]') if args.report_id else history.first
            selected.wait_for()
            report_id = selected.get_attribute('data-history')
            report = page.request.get(args.base_url + '/api/investigations/' + report_id).json()
            results['report_id'] = report_id
            selected.click()
            page.wait_for_function("() => document.querySelector('#verdict-banner').textContent.includes('结论不是绝对事实认证')")
            assert '调查进行中' not in page.locator('#verdict-banner').inner_text()
            assert page.locator('#metric-searches').inner_text() == str(len(report['queries']))
            assert report['verdict'] in page.locator('#verdict-banner').inner_text()
            assert report['stop_reason'] in page.locator('#scope-area').inner_text()
            assert page.locator('.source-card').count() > 0
            assert page.locator('#review-form').count() == 1
            page.locator('#review-editor').evaluate('(e) => e.open = true')
            assert len(page.locator('#review-source-text').inner_text()) > 10
            results['checks'].append('history replay preserves final verdict/counts + source inspector')
            if report.get('source_reviews'):
                for claim in report['claims']:
                    page.locator(f'[data-claim="{claim["id"]}"]').click()
                    assert '来源适用性判断' in page.locator('#claim-detail').inner_text()
                    for item in report['evidence']:
                        if item['claim_id'] == claim['id']:
                            assert item['support_degree'] in page.locator('#claim-detail').inner_text()
                            assert item['quote'] in page.locator('#claim-detail').inner_text()
                assert page.locator('#timeline li[data-kind="source_review"]').count() > 0
                assert page.locator('#timeline li[data-kind="evidence_review"]').count() > 0
                results['checks'].append('all claims expose separate source judgments, actual support labels and quotes; v2 events replay')
                page.locator(f'[data-claim="{report["claims"][0]["id"]}"]').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(folder / 'mobile-report.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1100})
            with page.expect_download() as download:
                page.locator('#export-md').click()
            download.value.save_as(str(folder / 'exported-report.md'))
            assert (folder / 'exported-report.md').read_text(encoding='utf-8').startswith('# 证据侦探')
            results['checks'].append('actual Markdown report download')
            page.screenshot(path=str(folder / 'desktop-report.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.reload(wait_until='networkidle')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(folder / 'mobile-home.png'), full_page=True)
        results['checks'].append('390px responsive viewport has no horizontal overflow')
        if args.live:
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.locator('#question').fill('Python 的 venv 模块是否从 Python 3.3 开始加入标准库，并用于创建隔离的虚拟环境？')
            page.locator('#start').click()
            page.wait_for_function("() => document.querySelector('#run-status').textContent === '调查已保存'", timeout=360000)
            assert page.locator('#timeline li[data-kind="search_result"]').count() >= 2
            assert page.locator('#timeline li[data-kind="counter"]').count() >= 1
            assert page.locator('#timeline li[data-kind="stopped"]').count() == 1
            assert page.locator('.source-card').count() > 0
            automatic_verdict = page.locator('#verdict-banner').inner_text()
            results['checks'].append('real live investigation: SSE search/counter/stop + final verdict')
            page.screenshot(path=str(folder / 'live-result.png'), full_page=True)
            page.locator('#review-editor').evaluate('(e) => e.open = true')
            text=page.locator('#review-source-text').inner_text()
            form=page.locator('#review-form')
            form.locator('[name="quote"]').fill('这不是检索返回中的真实片段，必须拒绝保存。')
            form.locator('[name="reason"]').fill('浏览器测试：验证错误引用会被拒绝。')
            form.locator('[name="reviewer"]').fill('自动化测试（非事实核查）')
            form.get_by_role('button').click()
            page.get_by_role('status').filter(has_text='引用必须').wait_for()
            form.locator('[name="quote"]').fill(text[:80])
            form.get_by_role('button').click()
            page.get_by_text('自动化测试（非事实核查）',exact=False).first.wait_for()
            assert page.locator('#verdict-banner').inner_text() == automatic_verdict
            results['checks'].append('manual review rejects invented quote, accepts exact quote, keeps system verdict unchanged')
            page.locator('#report-area').scroll_into_view_if_needed()
            page.screenshot(path=str(folder / 'review-proof.png'), full_page=True)
        assert not results['errors'], results['errors']
        browser.close()
    (folder / 'checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
