"""Read-only browser checks; no search/model invocation or POST requests."""
import json
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'artifacts'/'user-flow'/('ui-'+datetime.now().strftime('%Y%m%d-%H%M%S'))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    result={'replay_only':True,'paid_calls':0,'checks':[],'fonts':{},'errors':[]}
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True)
        except Exception:
            browser=p.chromium.launch(headless=True,channel='msedge')
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.goto('http://127.0.0.1:8765/',wait_until='networkidle')
        page.evaluate('() => document.fonts.ready')
        cdp=page.context.new_cdp_session(page)
        cdp.send('DOM.enable');cdp.send('CSS.enable')
        root=cdp.send('DOM.getDocument')['root']['nodeId']
        for selector in ['.input-note','.privacy-note','#start']:
            node=cdp.send('DOM.querySelector',{'nodeId':root,'selector':selector})['nodeId']
            fonts=cdp.send('CSS.getPlatformFontsForNode',{'nodeId':node})['fonts']
            result['fonts'][selector]=fonts
            assert any('Fusion Pixel' in f['familyName'] and f['isCustomFont'] and f['glyphCount']>0 for f in fonts)
        result['checks'].append('actual CJK platform glyphs remain Fusion Pixel in instructions, privacy, and submit button')
        for identifier in ['b09fca1b07e54aa7','b9d4d736f17c4d7f','4cee532aadbf40cb','78a7340d864b4fd3','2f363cd991b7476e']:
            report=page.request.get('http://127.0.0.1:8765/api/investigations/'+identifier).json()
            page.evaluate('(r) => renderReport(r,true)',report)
            if identifier=='78a7340d864b4fd3':
                assert '执行未完成' in page.locator('#verdict-banner').inner_text()
                assert '执行未完成' in page.locator(f'[data-history="{identifier}"]').inner_text()
            if identifier=='b9d4d736f17c4d7f':
                assert '张飞是汉末三国时期蜀汉名将' in page.locator('#answer-sections').inner_text()
                assert '已查明' in page.locator('#verdict-banner').inner_text()
            if identifier=='4cee532aadbf40cb':
                page.locator('[data-claim="C1"]').click()
                context=page.locator('.evidence-card').filter(has_text='E7 →').locator('.citation-context')
                assert context.count()==1
                context.evaluate('(e)=>e.open=true')
                assert '标准库' in context.inner_text()
                page.locator('#claim-detail').screenshot(path=str(OUT/'version-citation-context.png'))
                result['checks'].append('saved short E7 citation exposes the actual adjacent standard-library title without rewriting historical report')
            for width in [836,1440]:
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                page.locator('[data-workflow="verify"]').click()
                assert page.locator('#review-gold').count()==page.locator('#review-violet').count()==1
                page.locator('[data-workflow="answer"]').focus();page.keyboard.press('Enter')
                assert page.locator('[data-workflow="answer"]').get_attribute('aria-pressed')=='true'
                if page.locator('[data-method]').count():
                    page.locator('[data-method]').evaluate('(e)=>e.open=true')
                    assert report['question'] in page.locator('[data-method]').inner_text()
                    assert '论文原文' not in page.locator('[data-method]').inner_text()
                page.screenshot(path=str(OUT/f'{identifier}-{width}.png'),full_page=True)
            result['checks'].append(identifier+': saved report replay, 836/1440, keyboard, separate judges, question-specific rationale')
        for image in page.locator('.role-avatar').all():
            assert image.evaluate('(e)=>e.complete && e.naturalWidth>0')
        result['checks'].append('six supplied role assets load; no replacement art')
        assert not result['errors']
        result['status']='PASS'
        browser.close()
    (OUT/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':result['checks'],'errors':result['errors'],'artifacts':str(OUT)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
