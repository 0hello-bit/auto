# -*- coding: utf-8 -*-
"""On-demand inspector: connect to the CDP Chrome, find the OpenAI/auth/ChatGPT
page, dump headings/inputs/buttons/body-text and save a screenshot.

Usage:  .venv\Scripts\python.exe inspect_live.py [label]
The screenshot is saved to screenshots/_inspect_<label>.png
"""
import asyncio
import os
import re
import sys
import tempfile

os.environ.setdefault("DB_FILE", os.path.join(tempfile.mkdtemp(), "t.db"))
from app.config import config  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

LABEL = sys.argv[1] if len(sys.argv) > 1 else "now"
MATCH = ("auth.openai.com", "auth.chatgpt.com", "chatgpt.com", "openai.com")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(config.cdp_endpoint)
        target = None
        all_pages = [(ctx, pg) for ctx in browser.contexts for pg in ctx.pages]
        for _, pg in all_pages:
            try:
                if any(m in (pg.url or "") for m in MATCH):
                    target = pg
            except Exception:
                pass
        if target is None:
            print("No OpenAI/ChatGPT page open. All pages:")
            for _, pg in all_pages:
                print("   ", pg.url)
            return
        print("URL  :", target.url)
        try:
            print("TITLE:", await target.title())
        except Exception:
            pass
        for sel, lbl in (("h1,h2,[role=heading]", "HEADINGS"),):
            try:
                vals = await target.eval_on_selector_all(
                    sel, 'els=>els.map(e=>(e.innerText||"").trim()).filter(Boolean).slice(0,6)')
                print(lbl, ":", vals)
            except Exception as e:
                print(lbl, "err", str(e)[:60])
        try:
            ins = await target.eval_on_selector_all(
                "input", 'els=>els.map(e=>({type:e.type,name:e.name,ph:e.placeholder,'
                'id:e.id,im:e.getAttribute("inputmode"),vis:!!(e.offsetParent)})).slice(0,12)')
            print("INPUTS:", ins)
        except Exception as e:
            print("INPUTS err", str(e)[:60])
        try:
            btns = await target.eval_on_selector_all(
                "button,[role=button]",
                'els=>els.map(e=>(e.innerText||"").replace(/\\s+/g," ").trim()).filter(Boolean).slice(0,12)')
            print("BUTTONS:", btns)
        except Exception as e:
            print("BUTTONS err", str(e)[:60])
        try:
            body = re.sub(r"\s+", " ", await target.inner_text("body"))[:700]
            print("BODY :", body)
        except Exception as e:
            print("BODY err", str(e)[:60])
        os.makedirs("screenshots", exist_ok=True)
        path = os.path.join("screenshots", f"_inspect_{LABEL}.png")
        try:
            await target.screenshot(path=path, full_page=True)
            print("SHOT :", path)
        except Exception as e:
            print("SHOT err", str(e)[:60])


asyncio.run(main())
