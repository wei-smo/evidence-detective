"""Read-only acceptance of input layout and actual CJK glyph rendering."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'artifacts' / 'intake'

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'checks': [], 'fonts': {}, 'errors': []}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={'width':836,'height':686})
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
        page.evaluate('document.fonts.ready')
        assert page.locator('[data-example], .examples').count() == 0
        assert '问题与搜索材料会发送至元宝及推理服务' in page.locator('.privacy-note').inner_text()
        result['checks'].append('fixed examples removed, privacy explanation preserved')
        client = page.context.new_cdp_session(page)
        client.send('DOM.enable')
        client.send('CSS.enable')
        root = client.send('DOM.getDocument')['root']['nodeId']
        for selector in ['.input-note','.privacy-note','#start']:
            node = client.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
            fonts = client.send('CSS.getPlatformFontsForNode', {'nodeId':node})['fonts']
            result['fonts'][selector] = fonts
            assert any('Fusion Pixel' in f['familyName'] and f['glyphCount'] > 0 and f['isCustomFont'] for f in fonts)
        result['checks'].append('notes/privacy/start render actual Fusion Pixel glyphs')
        for width in [836,1440,390]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('.intake').screenshot(path=str(OUT/f'intake-{width}.png'))
        result['checks'].append('836/1440/390 no horizontal overflow')
        assert not result['errors']
        browser.close()
    (OUT/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__ == '__main__':
    main()
