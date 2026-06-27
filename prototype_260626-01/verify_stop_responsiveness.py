"""「停止」ボタン即応性の回帰テスト（prototype_260626-01）。

禁止する退行: 再生中に「停止」を押しても、現在足の足内更新ループ
(animateForming, 最大 ANIM_CAP×ANIM_MS ≒ 数秒)が最後まで走り切るまで
止まらない（タイムラグ）バグ。

契約: 停止クリックから足内更新が中断する(window.__rpAnimating===false)までの
実時間が THRESHOLD_MS 未満。かつ停止後 bar が進まない・ボタンが「▶ 再生」へ戻る。

前提: proto_server.py を別プロセスで起動済み。使い方: python3 verify_stop_responsiveness.py [PORT]
終了コード: 0=即応 OK / 1=退行（ラグ過大）。
"""
import asyncio
import sys
import time
from playwright.async_api import async_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
URL = f"http://127.0.0.1:{PORT}/"
THRESHOLD_MS = 500  # 人が「即時」と感じる上限。退行(数秒)はこれを大きく超える。


async def main():
    failures = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})
        await pg.goto(URL)
        await pg.wait_for_function("window.__rpReady===true", timeout=60000)
        await pg.wait_for_timeout(1000)

        # 足内更新が長いモード＋未来足を確保する再生開始位置（present−1年）にする。
        await pg.select_option("#rp-mode", "every_tick")
        await pg.evaluate("()=>[...document.querySelectorAll('#rp-presets button')].find(x=>x.textContent==='1年').click()")
        await pg.wait_for_timeout(2000)

        await pg.click("#rp-play")  # ▶ 再生
        await pg.wait_for_function("()=>window.__rpAnimating===true", timeout=10000)
        await pg.wait_for_timeout(120)  # 足内更新の途中へ

        t0 = time.perf_counter()
        await pg.click("#rp-play")  # 停止
        await pg.wait_for_function("()=>window.__rpAnimating===false", timeout=10000)
        lag = (time.perf_counter() - t0) * 1000

        bar1 = await pg.evaluate("()=>window.__rpbar")
        await pg.wait_for_timeout(700)
        bar2 = await pg.evaluate("()=>window.__rpbar")
        label = await pg.evaluate("()=>document.getElementById('rp-play').textContent")

        print(f"  停止→足内更新中断 ラグ = {lag:.0f} ms (閾値 {THRESHOLD_MS} ms)")
        print(f"  停止後 bar 不変: {bar1}=={bar2} ({bar1 == bar2}) / ボタン='{label}'")
        if lag >= THRESHOLD_MS:
            failures.append(f"停止ラグが過大: {lag:.0f}ms >= {THRESHOLD_MS}ms")
        if bar1 != bar2:
            failures.append(f"停止後も bar が進行: {bar1}->{bar2}")
        if "再生" not in label:
            failures.append(f"停止後ボタンが『再生』に戻らない: '{label}'")
        await b.close()

    if failures:
        print("\nREGRESSION DETECTED:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nOK: 停止ボタンは即応（タイムラグなし）")


asyncio.run(main())
