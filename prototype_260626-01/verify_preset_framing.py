"""期間プリセット枠表示の回帰テスト（prototype_260626-01）。

禁止する退行: 期間プリセット（3か月/6か月/1年/…）をクリックしたとき、playhead が
区間開始(replayStart)へジャンプして「最新足が左端へ移動」してしまうバグ。

正しい挙動（本テストが保証する契約）:
  - 再生位置(playhead) = present − 期間分(replayStart)    → __rpbar === __rpReplayStart
  - 最新リビール足(=bar)は右端、左へ窓が伸びる           → vis.to≈bar かつ vis.from<bar（左端移動でない）
  - 連動: スライダーを前進させると幅一定の窓が右へパンする → scrub で bar/vis.to 増、窓幅(to-from)不変

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
            rs = int(await pg.evaluate("()=>window.__rpReplayStart"))
            v = await _vis(pg)
            is_all = "全期間" in label
            before = len(failures)

            # 契約1: 再生位置(playhead) = present − 期間分(replayStart)
            if bar != rs:
                failures.append(f"[{label}] 再生位置が replayStart でない: bar={bar} replayStart={rs}")
            if not is_all and not (0 < rs < present):
                failures.append(f"[{label}] replayStart が present−期間分になっていない: rs={rs} present={present}")
            if is_all and rs != 0:
                failures.append(f"[全期間] replayStart が 0 でない: rs={rs}")
            # 契約2: 最新リビール足(=bar)は右端、左へ窓が伸びる（左端移動バグの禁止）
            if abs(v["t"] - bar) > MARGIN:
                failures.append(f"[{label}] 最新足が右端にない: vis.to={v['t']} bar={bar}")
            if not is_all and not (v["f"] < bar):
                failures.append(f"[{label}] 窓が左へ伸びていない（最新足左端の疑い）: vis.from={v['f']} bar={bar}")

            # 契約3（連動の核心）: スライダーを前進させると幅一定の窓が右へパンする
            width0 = v["t"] - v["f"]
            mx = int(await pg.evaluate("()=>+document.getElementById('rp-slider').max"))
            fwd = int((bar + mx) / 2)  # 現在の再生位置より未来側へスクラブ＝前進再生相当
            await pg.evaluate("(val)=>{const s=document.getElementById('rp-slider');s.value=val;s.dispatchEvent(new Event('input'));}", fwd)
            await pg.wait_for_timeout(2000)
            bar2 = int(await pg.evaluate("()=>window.__rpbar"))
            v2 = await _vis(pg)
            width1 = v2["t"] - v2["f"]
            if abs(v2["t"] - bar2) > MARGIN:
                failures.append(f"[{label}] スクラブ後 最新足が右端でない: vis.to={v2['t']} bar={bar2}")
            if v2["t"] <= v["t"]:
                failures.append(f"[{label}] スクラブ前進で窓が右へパンしない（連動なし）: to {v['t']}->{v2['t']}")
            if not is_all and abs(width1 - width0) > MARGIN:
                failures.append(f"[{label}] スクラブで窓幅が変化（パンでなくズーム）: {width0}->{width1}")
            # 後始末: 全期間へ戻して次プリセットへ影響させない
            await pg.evaluate("()=>{const b=[...document.querySelectorAll('#rp-presets button')].find(x=>/全期間/.test(x.textContent));if(b)b.click();}")
            await pg.wait_for_timeout(1500)

            print(f"  [{label}] bar=replayStart={bar} vis=[{v['f']},{v['t']}] →scrub→ bar={bar2} vis=[{v2['f']},{v2['t']}] "
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
