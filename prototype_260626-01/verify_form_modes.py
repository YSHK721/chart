"""最新足更新モードの回帰テスト（prototype_260626-01）。

確認/禁止する退行:
  (A) 「最新足更新」モードが全てティックで行われていないか＝各モードが固有のストリームを生成する。
      始値のみ=1更新 / 数学=1 / 1分OHLC・実ティック・全ティック合成=多数。
  (B) 長い形成の途中で 1足送り・モード切替が握り潰される（旧 `if(animating) return`）退行。
      形成中に open_only へ切替えて 1足送りすると、新モードが反映される（__rpForm.mode==open_only）。

前提: proto_server.py 起動済み。使い方: python3 verify_form_modes.py [PORT]
終了コード: 0=OK / 1=退行。
"""
import asyncio
import sys
from playwright.async_api import async_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
URL = f"http://127.0.0.1:{PORT}/"
# mode 値 -> 期待する足内更新ステップ数。math は「足内更新なし(0)」、始値のみは1更新、他は多数(>1)。
EXPECT = {"open_only": 1, "math": 0, "ohlc_1min": -1, "real_ticks": -1, "every_tick": -1}  # -1 = ">1"
ORDER = ("open_only", "math", "ohlc_1min", "real_ticks", "every_tick")


async def main():
    failures = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})
        await pg.goto(URL)
        await pg.wait_for_function("window.__rpReady===true", timeout=60000)
        await pg.wait_for_timeout(1000)
        await pg.evaluate("()=>[...document.querySelectorAll('#rp-presets button')].find(x=>x.textContent==='1年').click()")
        await pg.wait_for_timeout(2000)

        async def step(mode):
            # 前形成の完了を待ってからモードを切替え、1足送りで __rpForm を得る。
            try:
                await pg.wait_for_function("()=>window.__rpAnimating!==true", timeout=8000)
            except Exception:
                pass
            await pg.select_option("#rp-mode", mode)
            await pg.evaluate("()=>window.__rpForm=null")
            await pg.click("#rp-next")
            try:
                await pg.wait_for_function("()=>window.__rpForm!==null", timeout=8000)
            except Exception:
                pass
            return await pg.evaluate("()=>window.__rpForm")

        # --- (A) モード別ストリーム ---
        seen = {}
        for m in ORDER:
            f = await step(m)
            seen[m] = f
            print(f"  [{m}] -> {f}")
            exp = EXPECT[m]
            if not f or f.get("mode") != m:
                failures.append(f"[{m}] が反映されない（__rpForm={f}）")
            elif exp == 0 and f["n"] != 0:
                failures.append(f"[{m}] は足内更新なし(0)のはずが n={f['n']}（math が足内アニメする退行）")
            elif exp == 1 and f["n"] != 1:
                failures.append(f"[{m}] は1更新のはずが n={f['n']}")
            elif exp == -1 and f["n"] <= 1:
                failures.append(f"[{m}] が足内更新されない n={f['n']}（ティック/分足で更新されない退行）")

        # --- (A2) 実ティックは1分OHLCより明確に細かい（粒度分離・「同粒度」退行の禁止） ---
        n_rt = (seen.get("real_ticks") or {}).get("n", 0)
        n_oh = (seen.get("ohlc_1min") or {}).get("n", 0)
        print(f"  粒度: 実ティック n={n_rt} / 1分OHLC n={n_oh}（実ティックが明確に多いはず）")
        if not (n_rt >= 2 * n_oh):
            failures.append(f"実ティックと1分OHLCの粒度が分離されていない: 実ティック={n_rt} 1分OHLC={n_oh}")

        # --- (A3) 始値モードの足内アニメが激遅でない（1点なのに総時間スリープする退行の禁止） ---
        import time as _t
        try:
            await pg.wait_for_function("()=>window.__rpAnimating!==true", timeout=8000)
        except Exception:
            pass
        await pg.select_option("#rp-mode", "open_only")
        _t0 = _t.perf_counter()
        await pg.click("#rp-next")
        await pg.wait_for_function("()=>window.__rpAnimating===true", timeout=8000)
        await pg.wait_for_function("()=>window.__rpAnimating!==true", timeout=15000)
        open_ms = (_t.perf_counter() - _t0) * 1000
        print(f"  始値モード 足内アニメ所要 ≈ {open_ms:.0f} ms（上限クランプで <1000ms のはず）")
        if open_ms >= 1000:
            failures.append(f"始値モードが激遅: {open_ms:.0f}ms（1点で総時間スリープの退行）")

        # --- (B) 形成中の切替が握り潰されない（supersede） ---
        try:
            await pg.wait_for_function("()=>window.__rpAnimating!==true", timeout=8000)
        except Exception:
            pass
        await pg.select_option("#rp-mode", "real_ticks")
        await pg.evaluate("()=>window.__rpForm=null")
        await pg.click("#rp-next")  # 長いティック形成を開始
        await pg.wait_for_function("()=>window.__rpAnimating===true", timeout=8000)
        await pg.wait_for_timeout(150)  # 形成途中
        await pg.select_option("#rp-mode", "open_only")
        await pg.evaluate("()=>window.__rpForm=null")
        await pg.click("#rp-next")  # 形成中に新モードで1足送り
        try:
            await pg.wait_for_function("()=>window.__rpForm && window.__rpForm.mode==='open_only'", timeout=8000)
            ok = True
        except Exception:
            ok = False
        f2 = await pg.evaluate("()=>window.__rpForm")
        print(f"  (B) 形成中に open_only へ切替+1足送り -> {f2}")
        if not ok:
            failures.append(f"形成中の 1足送り/モード切替が握り潰された（__rpForm={f2}）")

        await b.close()

    if failures:
        print("\nREGRESSION DETECTED:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nOK: 各モードは固有ストリームで更新され、形成中の切替も即反映される")


asyncio.run(main())
