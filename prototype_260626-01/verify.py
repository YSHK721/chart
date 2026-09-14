"""prototype_260626-01 自動検証（実3インジ＋凡例）: 凡例・目玉トグル・各インジ表示を撮影。
前提: proto_server.py を別プロセスで起動済み。 使い方: python3 verify.py [PORT]"""
import asyncio, os, sys
from playwright.async_api import async_playwright
PORT = sys.argv[1] if len(sys.argv) > 1 else "8795"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(OUT, exist_ok=True)
URL = f"http://127.0.0.1:{PORT}/"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 800})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(URL)
        await pg.wait_for_function("window.__ready===true", timeout=60000)

        async def gotoBar(frac):
            mx = int(await pg.eval_on_selector("#slider", "el=>+el.max")); f = int(mx*frac)
            await pg.evaluate("(f)=>{const s=document.getElementById('slider');s.value=f;s.dispatchEvent(new Event('input'));}", f)
            await pg.wait_for_function("(f)=>window.__bar===f", arg=f, timeout=60000); await pg.wait_for_timeout(400)
        async def shot(name):
            await pg.screenshot(path=f"{OUT}/{name}.png")
            print(f"--- {name} ---\n" + await pg.eval_on_selector("#readout", "el=>el.textContent"))
            ng = await pg.eval_on_selector_all("#legend .row", "els=>els.length")
            print(f"  legend rows={ng}\n")

        await gotoBar(0.8); await shot("01_three_indicators_legend")
        # profit_band グループを目玉でOFF → 28線消える
        await pg.evaluate("""()=>{const heads=[...document.querySelectorAll('#legend .ghead')];
          const h=heads.find(x=>x.textContent.includes('profit_band')); if(h)h.querySelector('.eye').click();}""")
        await pg.wait_for_timeout(2500); await shot("02_profitband_hidden")
        ch = await pg.eval_on_selector("#chart", "el=>el.getBoundingClientRect().height")
        print(f"chart height px = {ch}")
        print("PAGEERRORS:", errs[-5:])
        await b.close()
asyncio.run(main())
