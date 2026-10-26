"""Read-only UI acceptance, using saved real reports; no paid search calls."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / 'artifacts' / 'acceptance' / 'workflow'
IDS = ['2f363cd991b7476e', '7afd2065a9c34b4b', 'baf4bd27ac3f4df1']

def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    results = {'checks': [], 'errors': [], 'fonts': []}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={'width':836,'height':686})
        page.on('pageerror', lambda e: results['errors'].append(str(e)))
        for identifier in IDS:
            page.set_viewport_size({'width':1440,'height':1000})
            page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
            page.locator(f'[data-history="{identifier}"]').click()
            page.wait_for_function("() => document.querySelector('#run-status').textContent.includes('历史') || document.querySelector('#verdict-banner').textContent.includes('结论不是绝对事实认证')")
            for width in [836,1440]:
                page.set_viewport_size({'width':width,'height':686 if width==836 else 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('[data-workflow="verify"]').click()
                assert page.locator('#review-gold').count() == 1
                assert page.locator('#review-violet').count() == 1
                assert page.locator('#workflow-cards .role-badge').count() == 6
                page.locator('#workflow-cards').scroll_into_view_if_needed()
                page.screenshot(path=str(FOLDER / f'{identifier}-{width}.png'))
                results['checks'].append(f'{identifier}: {width}px, no horizontal overflow, five cards/six identities/separate judges')
            page.locator('[data-workflow="search"]').click()
            search_text = page.locator('#workflow-inspector').inner_text()
            page.locator('[data-workflow="challenge"]').click()
            challenge_text = page.locator('#workflow-inspector').inner_text()
            if identifier == IDS[1]:
                assert '缺口补查' in search_text
                assert '缺口补查' not in challenge_text
                results['checks'].append('L02 actual followup shown under search, not challenge')
            page.locator('[data-workflow="verify"]').focus()
            page.keyboard.press('Enter')
            assert page.locator('[data-workflow="verify"]').get_attribute('aria-pressed') == 'true'
            results['checks'].append(f'{identifier}: keyboard Enter opens card')
        results['fonts'] = page.evaluate("Array.from(document.fonts).map(f=>({family:f.family,status:f.status}))")
        results['font_sample'] = page.locator('.input-note').evaluate('(e)=>getComputedStyle(e).fontFamily')
        assert not results['errors']
        browser.close()
    (FOLDER/'checks.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
