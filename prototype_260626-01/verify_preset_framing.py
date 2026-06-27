"""期間プリセット枠表示の回帰テスト（prototype_260626-01）。

禁止する退行: 期間プリセット（3か月/6か月/1年/…）をクリックしたとき、playhead が
区間開始(replayStart)へジャンプして「最新足が左端へ移動」してしまうバグ。

正しい挙動（本テストが保証する契約）:
  - クリック後も playhead は present（最新足）に留まる    → window.__rpbar === slider.max
  - 可視範囲の右端が present 近傍にある（最新足＝右端）    → visibleRange.to ≈ slider.max
  - 可視範囲の左端が present より十分過去にズームしている  → visibleRange.from < slider.max
  - 「全期間」は左端=0 までズームアウト                    → visibleRange.from ≈ 0

前提: proto_server.py を別プロセスで起動済み。使い方: python3 verify_preset_framing.py [PORT]
終了コード: 0=全契約 OK / 1=退行検出。
"""
import asyncio
import sys
from playwright.async_api import async_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
URL = f"http://127.0.0.1:{PORT}/"
MARGIN = 8  # RIGHT_MARGIN(6) + 余白。右端一致判定の許容バー数。


async def _vis(pg):
    for _ in range(10):
        r = await pg.evaluate(
            "()=>{const r=window.__rpChart.timeScale().getVisibleLogicalRange();"
            "return r?{f:Math.round(r.from),t:Math.round(r.to)}:null;}"
        )
        if r:
            return r
        await pg.wait_for_timeout(300)
    raise AssertionError("visibleLogicalRange を取得できない（チャート未描画）")


async def main():
    failures = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(URL)
        await pg.wait_for_function("window.__rpReady===true", timeout=60000)
        await pg.wait_for_timeout(1000)

        present = int(await pg.evaluate("()=>+document.getElementById('rp-slider').max"))
        labels = await pg.evaluate(
            "()=>[...document.querySelectorAll('#rp-presets button')].map(b=>b.textContent)"
        )
        if not labels:
            failures.append("プリセットボタンが1つも描画されていない")

        for label in labels:
            await pg.evaluate(
                "(l)=>[...document.querySelectorAll('#rp-presets button')]"
                ".find(x=>x.textContent===l).click()",
                label,
            )
            await pg.wait_for_timeout(2200)
            bar = int(await pg.evaluate("()=>window.__rpbar"))
            v = await _vis(pg)
            slider_min = int(await pg.evaluate("()=>+document.getElementById('rp-slider').min"))
            is_all = "全期間" in label

            # 契約0: スライダー(スクロールバー)の作用域が再生スパン開始(replayStart=可視左端)に連動
            if abs(slider_min - v["f"]) > MARGIN:
                failures.append(f"[{label}] スライダー未連動: slider.min={slider_min} replayStart(≈vis.from)={v['f']}")

            # 契約1: playhead は present に留まる（左端へジャンプしない）
            if bar != present:
                failures.append(f"[{label}] playhead が present(={present}) でなく {bar}（左端移動の退行）")
            # 契約2: 最新足＝右端（可視右端が present 近傍）
            if abs(v["t"] - present) > MARGIN:
                failures.append(f"[{label}] 最新足が右端にない: vis.to={v['t']} present={present}")
            # 契約3: 期間プリセットは過去側へズーム（左端 < present）。全期間は左端≈0。
            if is_all:
                if v["f"] > MARGIN:
                    failures.append(f"[全期間] 左端が0でない: vis.from={v['f']}")
            else:
                if not (0 <= v["f"] < present):
                    failures.append(f"[{label}] 期間ズームになっていない: vis.from={v['f']} present={present}")
            print(f"  [{label}] bar={bar} present={present} vis=[{v['f']},{v['t']}] OK"
                  if not failures or failures[-1].split(']')[0].strip('[') != label
                  else f"  [{label}] NG")

        if errs:
            failures.append(f"pageerror 発生: {errs[-3:]}")
        await b.close()

    if failures:
        print("\nREGRESSION DETECTED:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nALL CONTRACTS OK（最新足=右端・期間ズーム・全期間=左端0）")


asyncio.run(main())
