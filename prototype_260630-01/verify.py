"""prototype_260630-01 Market Profile ビューア 自動検証（スクショ撮影）。
前提: python3 prototype_260630-01/mp_server.py 8810 を起動済み。
使い方: python3 prototype_260630-01/verify.py [PORT]"""
import asyncio, os, sys
from playwright.async_api import async_playwright
PORT = sys.argv[1] if len(sys.argv) > 1 else "8810"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
URL = f"http://127.0.0.1:{PORT}/"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 800})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(URL)
        await pg.wait_for_function("window.__ready===true", timeout=60000)
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=f"{OUT}/web_shot.png")
        print("composite readout:", await pg.eval_on_selector("#readout", "el=>el.textContent"))

        # sessions ON
        await pg.evaluate("()=>{const c=document.getElementById('sessions');c.checked=true;c.dispatchEvent(new Event('change'));}")
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=f"{OUT}/web_shot_sessions.png")
        print("status:", await pg.eval_on_selector("#status", "el=>el.textContent"))
        print("heat canvas size:", await pg.eval_on_selector("#heat", "el=>el.width+'x'+el.height"))
        print("PAGEERRORS:", errs[-5:])
        await b.close()

asyncio.run(main())
