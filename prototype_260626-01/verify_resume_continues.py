"""「停止→再生」で現在足を続きから再開する回帰テスト（prototype_260626-01）。

禁止する退行: 足の形成途中で「停止」し、再び「再生」を押すと、現在足を飛ばして
次の足から再開してしまうバグ（playLoop が先に drive(bar+1) で前進するため）。

契約:
  - 形成途中で停止→再生したとき、再生直後の bar は停止時の bar と同じ（次足へ飛ばない）。
  - 再開後はその足を完走して通常前進する（固着しない＝bar が停止時より先へ進む）。

前提: proto_server.py 起動済み。使い方: python3 verify_resume_continues.py [PORT]
終了コード: 0=OK / 1=退行。
"""
import asyncio
import sys
from playwright.async_api import async_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
URL = f"http://127.0.0.1:{PORT}/"


async def main():
    failures = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})
        await pg.goto(URL)
        await pg.wait_for_function("window.__rpReady===true", timeout=60000)
        await pg.wait_for_timeout(1000)

        # 形成を長く（多ティック）＋未来足を確保。
        await pg.select_option("#rp-mode", "every_tick")
        await pg.evaluate("()=>[...document.querySelectorAll('#rp-presets button')].find(x=>x.textContent==='1年').click()")
        await pg.wait_for_timeout(2000)

        await pg.click("#rp-play")  # ▶ 再生
        await pg.wait_for_function("()=>window.__rpAnimating===true", timeout=10000)
        await pg.wait_for_timeout(120)  # 形成の途中で
        bar_stop = await pg.evaluate("()=>window.__rpbar")

        await pg.click("#rp-play")  # 停止
        await pg.wait_for_function("()=>window.__rpAnimating===false", timeout=10000)

        await pg.click("#rp-play")  # 再生（続きから期待）
        await pg.wait_for_function("()=>window.__rpAnimating===true", timeout=10000)
        await pg.wait_for_timeout(120)
        bar_resume = await pg.evaluate("()=>window.__rpbar")

        # 契約1: 再生直後は同じ足を継続（次足へ飛ばない）
        if bar_resume != bar_stop:
            failures.append(f"再生で次足へ飛んだ（続きから再開しない）: 停止 bar={bar_stop} → 再生直後 bar={bar_resume}")

        # 契約2: 再開後は完走して通常前進する（固着しない）
        try:
            await pg.wait_for_function("(b)=>window.__rpbar > b", arg=bar_stop, timeout=15000)
            advanced = True
        except Exception:
            advanced = False
        if not advanced:
            failures.append(f"再開後に前進しない（固着）: bar={await pg.evaluate('()=>window.__rpbar')} stop={bar_stop}")

        print(f"  停止 bar={bar_stop} / 再生直後 bar={bar_resume}（同一なら続きから）/ その後前進={advanced}")
        await b.close()

    if failures:
        print("\nREGRESSION DETECTED:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nOK: 停止→再生で現在足を続きから再開し、その後通常前進する")


asyncio.run(main())
