"""足内 MA 追従の回帰テスト（prototype_260626-01）。

確認/禁止する退行:
  移動平均線が「最新足更新（足内アニメ）」中にティック粒度で再計算されず固定表示になる退行。
  正常時は、再生アニメ中に forming（形成中バー暫定 OHLC）付きの /compute が moving_averages へ
  複数回飛び、描画される MA 末尾値が複数段階に変化する（ローソクに追従する）。

前提: proto_server.py 起動済み（serve.py）。MA は純 Python（mql_builtins）で rpy2 不要。
使い方: python3 verify_ma_intrabar.py [PORT]   （既定 8796）
終了コード: 0=OK / 1=退行。
"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
URL = f"http://127.0.0.1:{PORT}/"

# 描画済み MA 末尾値（{instanceId}::MA 系列の最終点）を読む。renderer 内部を辿る（E2E 観測専用）。
SAMPLE_MA_LAST = """
() => {
  const r = window.__rpController && window.__rpController._renderer;
  if (!r || !r._instances) return null;
  for (const slot of r._instances.values()) {
    for (const [key, series] of slot.lines.entries()) {
      if (key.includes('moving_averages') && key.endsWith('::MA')) {
        const d = series.data();
        if (d && d.length) return d[d.length - 1].value;
      }
    }
  }
  return null;
}
"""


async def main() -> int:
    computes = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})

        async def on_req(req):
            if req.method == "POST" and req.url.endswith("/compute"):
                try:
                    computes.append(json.loads(req.post_data or "{}"))
                except Exception:
                    computes.append({})
        pg.on("request", on_req)

        await pg.goto(URL)
        await pg.wait_for_function("window.__rpReady===true", timeout=60000)
        await pg.wait_for_function("window.__rpController!==undefined", timeout=10000)
        # 既定 MA（ema/9/close, wait_for_close=false）を適用。
        await pg.evaluate("async()=>{await window.__rpController.applyIndicator('moving_averages','default');}")
        await pg.wait_for_timeout(800)
        # 過去へ戻す（最新足だと1足送り/再生できないため）。
        await pg.evaluate("()=>[...document.querySelectorAll('#rp-presets button')].find(x=>x.textContent==='1年')?.click()")
        await pg.wait_for_timeout(1500)
        # real_ticks で1足を足内形成しつつ、forming 送信と描画 MA 末尾値の変化を観測。
        await pg.select_option("#rp-mode", "real_ticks")
        computes.clear()
        await pg.click("#rp-next")
        vals = []
        for _ in range(150):
            v = await pg.evaluate(SAMPLE_MA_LAST)
            if v is not None:
                vals.append(round(v, 4))
            await pg.wait_for_timeout(40)
            if await pg.evaluate("()=>window.__rpAnimating!==true") and len(vals) > 5:
                break
        await b.close()

    ma_forming = [c for c in computes
                  if "forming" in c and c.get("indicatorId") == "moving_averages"]
    closes = {c["forming"].get("close") for c in ma_forming}
    wfc = {json.dumps(c.get("params", {}).get("wait_for_close")) for c in ma_forming}
    distinct_ma = len(set(vals))

    print(f"  足内 forming付きMA /compute : {len(ma_forming)}（close異なり {len(closes)}）")
    print(f"  MA params.wait_for_close   : {wfc}")
    print(f"  描画MA末尾の異なり値        : {distinct_ma}（振れ {max(vals)-min(vals):.2f} 価格単位）" if vals else "  描画MA: なし")

    failures = []
    if len(ma_forming) < 2:
        failures.append("足内アニメ中に forming 付き /compute(MA) が飛んでいない（足内再計算なし＝固定）")
    if len(closes) < 2:
        failures.append("forming.close が変化していない（同一足内でティックが流れていない）")
    if distinct_ma < 3:
        failures.append(f"描画 MA 末尾値がほぼ固定（異なり {distinct_ma}）＝ローソクに追従していない")
    if wfc and wfc != {"false"}:
        failures.append(f"wait_for_close が false でない（{wfc}）＝最終足除外で足内追従不可")

    if failures:
        print("RESULT: NG（足内 MA 追従の退行）")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: OK（足内で forming 送信・MA 末尾値が多段変化＝ローソクに追従）")
    return 0


sys.exit(asyncio.run(main()))
