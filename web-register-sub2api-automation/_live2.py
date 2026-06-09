# -*- coding: utf-8 -*-
import asyncio, os, tempfile
os.environ['DB_FILE'] = os.path.join(tempfile.mkdtemp(), 't.db')
from app.config import config
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(config.cdp_endpoint)
        target = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                try:
                    if 'auth.openai.com' in pg.url:
                        target = pg
                except Exception:
                    pass
        if target is None:
            print('no auth page. pages:', [pg.url for ctx in browser.contexts for pg in ctx.pages]); return
        print('URL:', target.url)
        try: print('title:', await target.title())
        except: pass
        try:
            heads = await target.eval_on_selector_all('h1,h2,[role="heading"]', 'els=>els.map(e=>(e.innerText||"").trim()).filter(Boolean).slice(0,5)')
            print('headings:', heads)
        except Exception as e: print('head err', str(e)[:50])
        try:
            ins = await target.eval_on_selector_all('input', 'els=>els.map(e=>({type:e.type,name:e.name,ph:e.placeholder,id:e.id,im:e.getAttribute("inputmode")})).slice(0,10)')
            print('inputs:', ins)
        except Exception as e: print('inp err', str(e)[:50])
        try:
            btns = await target.eval_on_selector_all('button', 'els=>els.map(e=>(e.innerText||"").replace(/\\s+/g," ").trim()).filter(Boolean).slice(0,10)')
            print('buttons:', btns)
        except Exception as e: print('btn err', str(e)[:50])
        try:
            body_txt = (await target.inner_text('body'))
            import re
            body_txt = re.sub(r'\\s+', ' ', body_txt)[:500]
            print('body text:', body_txt)
        except Exception as e: print('body err', str(e)[:50])
        await target.screenshot(path='screenshots/_live_phone.png', full_page=True)
        print('shot: screenshots/_live_phone.png')

asyncio.run(main())
