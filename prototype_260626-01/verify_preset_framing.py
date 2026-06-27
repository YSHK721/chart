"""期間プリセット枠表示の回帰テスト（prototype_260626-01）。

禁止する退行: 期間プリセット（3か月/6か月/1年/…）をクリックしたとき、playhead が
区間開始(replayStart)へジャンプして「最新足が左端へ移動」してしまうバグ。

正しい挙動（本テストが保証する契約）:
  - クリック後 playhead=present、最新足は右端           → __rpbar===slider.max, vis.to≈present
  - 期間プリセットは直近 N 期間の窓幅にズーム            → 0 <= vis.from < present
  - 「全期間」は左端=0 までズームアウト                  → vis.from ≈ 0
  - 連動: スライダーを動かすと幅一定の窓が履歴をパンする  → スクラブで vis.from/to が共に移動し、
    窓幅(to-from)は不変、最新足(=playhead bar)は右端のまま（＝「ただ拡大」でない）

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
            is_all = "全期間" in label
            before = len(failures)

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

            # 契約4（連動の核心）: スライダーを中間へ動かすと、幅一定の窓が履歴をパンする
            #   ＝vis.from/to が共に過去側へ移動・窓幅は不変・最新足(bar)は右端のまま（「ただ拡大」でない）。
            width0 = v["t"] - v["f"]
            sm = await pg.evaluate("()=>({mn:+document.getElementById('rp-slider').min,mx:+document.getElementById('rp-slider').max})")
            mid = int((sm["mn"] + sm["mx"]) / 2)
            await pg.evaluate("(val)=>{const s=document.getElementById('rp-slider');s.value=val;s.dispatchEvent(new Event('input'));}", mid)
            await pg.wait_for_timeout(2000)
            bar2 = int(await pg.evaluate("()=>window.__rpbar"))
            v2 = await _vis(pg)
            width1 = v2["t"] - v2["f"]
            if abs(v2["t"] - bar2) > MARGIN:
                failures.append(f"[{label}] スクラブ後 最新足が右端でない: vis.to={v2['t']} bar={bar2}")
            if not is_all:
                # 全期間は左端0固定（パンしない）が正。有限プリセットのみ「幅一定でパン」を要求。
                if abs(width1 - width0) > MARGIN:
                    failures.append(f"[{label}] スクラブで窓幅が変化（パンでなくズーム）: {width0}->{width1}")
                if v2["f"] >= v["f"]:
                    failures.append(f"[{label}] スクラブで窓が左へパンしない（連動なし）: from {v['f']}->{v2['f']}")
            # 後始末: 全期間へ戻して次プリセットへ影響させない
            await pg.evaluate("()=>{const b=[...document.querySelectorAll('#rp-presets button')].find(x=>/全期間/.test(x.textContent));if(b)b.click();}")
            await pg.wait_for_timeout(1500)

            print(f"  [{label}] vis=[{v['f']},{v['t']}] →scrub bar={bar2} vis=[{v2['f']},{v2['t']}] "
                  + ("OK" if len(failures) == before else "NG"))

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
