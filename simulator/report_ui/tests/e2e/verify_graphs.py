"""SPEC#4 インタラクティブグラフ＋チャート連動 E2E 検証（Playwright・詳細設計 §8.4 / SPEC#4）。

検証対象（SPEC#4）:
  1. グラフ（棒×複数・相関散布・保有時間棒）が canvas で描画される。
  2. グラフ要素クリック→linkage.activeFilter が該当 trade id Set（filter 抽出を window.__linkage で実証・
     ②の弱 assertion 教訓に従い activeFilter の実値で検証）。
  3. 区間切替（IS↔OOS）で Chart.js の "Canvas is already in use" 例外が出ない（R-3 destroy）。

決定論のため entry_time を既知 UTC セル（Mon hour0 / Sun hour23）に固定し、agg に
entries_*/pl_*/scatter_mfe/scatter_mae/hold_pl/hold_cnt を埋めたダミー report.json を配信する。
chromium 不在環境では skip（既存 verify.py 規約準拠）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from e2e import _harness  # noqa: E402  (同一ディレクトリの共有ハーネス)

WEB = Path(__file__).resolve().parents[1].parent / "web"

pytestmark = pytest.mark.e2e

# 既知 UTC entry_time（back derive / front graphs と同規約・同定数）。
_TS_MON_H0 = 1776643200    # 2026-04-20 00:00:00 UTC（Mon hour0）
_TS_SUN_H23 = 1776639600   # 2026-04-19 23:00:00 UTC（Sun hour23）

_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HB = [(0, 60, "<1m"), (60, 120, "1-2m"), (120, 300, "2-5m"), (300, 600, "5-10m"),
       (600, 1800, "10-30m"), (1800, 3600, "30-60m"), (3600, 10 ** 9, ">1h")]


def _free_port() -> int:
    return _harness.free_port()


def _serve(directory: str, port: int):
    return _harness.serve(directory, port)


def _trade(i, entry_time, side, ep, profit, hold_sec=60):
    return {
        "id": i, "side": side, "entry_time": entry_time, "exit_time": entry_time + hold_sec,
        "entry_price": ep, "exit_price": ep + profit, "profit": profit,
        "volume": "0.1", "sl": f"{ep - 2:.1f}", "tp": f"{ep + 5:.1f}",
        "order": i, "comment": "tp" if profit > 0 else "sl",
        "balance": 10000 + profit, "hold_sec": hold_sec, "mfe": 1.0 + i, "mae": 0.5 + i,
    }


def _agg(trades):
    """back derive と同規約で entries/pl/scatter/hold を作る（ダミー payload 用）。"""
    from datetime import datetime, timezone

    def _sess(h):
        return "Asia" if 0 <= h < 7 else ("Europe" if 7 <= h < 13 else "USA")

    entries_hour = {h: 0 for h in range(24)}
    entries_session = {"Asia": 0, "Europe": 0, "USA": 0}
    entries_wday = {w: 0 for w in _WEEK}
    entries_month = {}
    pl_hour = {h: 0.0 for h in range(24)}
    pl_wday = {w: 0.0 for w in _WEEK}
    pl_month = {}
    hold_pl = {lab: 0.0 for _, _, lab in _HB}
    hold_cnt = {lab: 0 for _, _, lab in _HB}
    for t in trades:
        edt = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc)
        xdt = datetime.fromtimestamp(t["exit_time"], tz=timezone.utc)
        eh, ew = edt.hour, _WEEK[edt.weekday()]
        entries_hour[eh] += 1
        entries_session[_sess(eh)] += 1
        entries_wday[ew] += 1
        em = edt.strftime("%Y-%m")
        entries_month[em] = entries_month.get(em, 0) + 1
        pr = t["profit"]
        pl_hour[xdt.hour] += pr
        pl_wday[_WEEK[xdt.weekday()]] += pr
        xm = xdt.strftime("%Y-%m")
        pl_month[xm] = pl_month.get(xm, 0.0) + pr
        for lo, hi, lab in _HB:
            if lo <= t["hold_sec"] < hi:
                hold_pl[lab] += pr
                hold_cnt[lab] += 1
                break
    return {
        "entries_hour": entries_hour, "entries_session": entries_session,
        "entries_wday": entries_wday, "entries_month": entries_month,
        "pl_hour": {h: round(v, 1) for h, v in pl_hour.items()},
        "pl_wday": {w: round(v, 1) for w, v in pl_wday.items()},
        "pl_month": {m: round(v, 1) for m, v in pl_month.items()},
        "balance_curve": [{"time": t["exit_time"], "value": t["balance"]} for t in trades],
        "scatter_mfe": [{"x": t["mfe"], "y": t["profit"], "id": t["id"]} for t in trades],
        "scatter_mae": [{"x": t["mae"], "y": t["profit"], "id": t["id"]} for t in trades],
        "hold_pl": hold_pl, "hold_cnt": hold_cnt, "weekorder": _WEEK,
        "heat": _heat_cells(trades),
    }


def _heat_cells(trades):
    from datetime import datetime, timezone
    acc = {}
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc)
        key = (_WEEK[dt.weekday()], dt.hour)
        c = acc.setdefault(key, {"profit": 0.0, "count": 0, "wins": 0})
        c["profit"] += t["profit"]; c["count"] += 1
        if t["profit"] > 0:
            c["wins"] += 1
    return [{"wday": w, "hour": h, "profit": round(v["profit"], 1),
             "count": v["count"], "wins": v["wins"]} for (w, h), v in acc.items()]


def _seg(label, trades):
    bars = []
    base = _TS_SUN_H23 - 3600
    for i in range(0, 600):
        t = base + i * 60
        bars.append({"time": t, "open": 100.0 + (i % 5), "high": 112.0,
                     "low": 95.0, "close": 105.0 + (i % 3)})
    return {
        "label": label,
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": len(bars), "trades": len(trades), "period": "2026.04.01-04.14"},
        "report": {}, "bars": bars, "trades": trades, "orders": [],
        "agg": _agg(trades),
    }


def _multi_trade_report() -> dict:
    # id1,id3 = Mon hour0 / id2 = Sun hour23 （hour0 棒クリックで id1,id3 が抽出される設計）
    is_tr = [
        _trade(1, _TS_MON_H0, "buy", 100.0, 50.0, hold_sec=30),
        _trade(2, _TS_SUN_H23, "sell", 110.0, -20.0, hold_sec=90),
        _trade(3, _TS_MON_H0, "buy", 102.0, 50.0, hold_sec=30),
    ]
    # OOS の id は IS と重複させない（🟡-2: OOS 散布クリックが IS 配列を誤参照したら
    #   id が IS 側になり検出可能になるよう、id を 101/102 に分離する）。
    oos_tr = [
        _trade(101, _TS_MON_H0, "buy", 100.0, 10.0, hold_sec=30),
        _trade(102, _TS_SUN_H23, "sell", 110.0, -5.0, hold_sec=90),
    ]
    return {
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "initial_deposit": 10000.0, "split": "2026-04-15"},
        "segments": {"is": _seg("IS", is_tr), "oos": _seg("OOS", oos_tr)},
        "summary": {
            "is": {"trades": 3, "net": 80.0, "final_balance": 10080.0, "win_rate": 66.67,
                   "profit_factor": 5.0, "expectancy": 26.67, "payoff": 2.5,
                   "return_pct": 0.8, "max_dd_pct": 0.2},
            "oos": {"trades": 2, "net": 5.0, "final_balance": 10005.0, "win_rate": 50.0,
                    "profit_factor": 2.0, "expectancy": 2.5, "payoff": 2.0,
                    "return_pct": 0.05, "max_dd_pct": 0.1},
        },
        "degradation": {}, "verdict": {"result": "pass", "reasons": ["OOSでも優位性を維持"]},
        "_contract_notes": [],
    }


def _build_web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    shutil.copytree(WEB, root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "report.json").write_text(
        json.dumps(_multi_trade_report(), ensure_ascii=False, separators=(",", ":")))
    return root


def _launch(tmp_path):
    return _harness.launch(_build_web_root, tmp_path)


def _open_graph_tab(page):
    page.click('.mv-tab[data-tab="graph"]')
    page.wait_for_selector("#graphGrid canvas", timeout=4000)


def test_graphs_render_canvases(tmp_path):
    # SPEC#4.1: グラフタブで 9 グラフ（棒×6・散布×2・保有時間×1）が canvas で描画される。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_graph_tab(page)
        canvases = page.query_selector_all("#graphGrid canvas")
        assert len(canvases) == 9, f"expected 9 graph canvases, got {len(canvases)}"
        # Chart.js が実描画している（canvas が幅を持つ＝レイアウト済）。
        w = page.eval_on_selector("#graphGrid canvas", "el => el.width")
        assert w and w > 0, f"graph canvas not rendered (width={w})"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_graph_bar_click_filters_chart_markers(tmp_path):
    # SPEC#4.2（最重要・連動）: Entries by hours の hour0 棒クリック→activeFilter={1,3}。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_graph_tab(page)
        # Chart.js の onClick を直接駆動（canvas 座標依存を避け、登録済みハンドラを呼ぶ）。
        # gEH(Entries by hours) chart の dataset 要素 index=0(hour0) をクリック相当でディスパッチ。
        page.wait_for_function(
            "() => { const cs = Object.values(window.__graphsCharts||{}); "
            "return cs.length >= 9; }", timeout=4000)
        page.evaluate("""() => {
          const ch = window.__graphsCharts.eh;       // Entries by hours
          const meta = ch.getDatasetMeta(0);
          const el = meta.data[0];                    // hour0 の棒
          ch.options.onClick(null, [{index: 0, element: el, datasetIndex: 0}], ch);
        }""")
        page.wait_for_function(
            "window.__linkage && window.__linkage.activeFilter && "
            "window.__linkage.activeFilter.size === 2", timeout=4000)
        ids = page.evaluate("Array.from(window.__linkage.activeFilter).sort((a,b)=>a-b)")
        assert ids == [1, 3], f"hour0 bar click did not extract trades 1,3: {ids}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_scatter_oos_point_click_extracts_oos_trade_id(tmp_path):
    # 🟡-2（正当性バグ回帰）: IS 区間表示中に散布の OOS 系列（datasetIndex=1）の点を
    #   クリック → activeFilter は OOS の trade id（101）。IS 配列を誤参照すると IS id に
    #   なるため、id 分離（IS=1.. / OOS=101..）で誤参照を検出可能にしている。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_graph_tab(page)
        page.wait_for_function(
            "() => { const cs = Object.values(window.__graphsCharts||{}); "
            "return cs.length >= 9; }", timeout=4000)
        page.evaluate("""() => {
          const ch = window.__graphsCharts.cf;        // Correlation (Profits, MFE)
          const meta = ch.getDatasetMeta(1);          // dataset1 = OOS 系列
          const el = meta.data[0];                    // OOS 先頭点（id=101 を期待）
          ch.options.onClick(null, [{index: 0, element: el, datasetIndex: 1}], ch);
        }""")
        page.wait_for_function(
            "window.__linkage && window.__linkage.activeFilter && "
            "window.__linkage.activeFilter.size === 1", timeout=4000)
        ids = page.evaluate("Array.from(window.__linkage.activeFilter)")
        assert ids == [101], f"OOS scatter click did not extract OOS trade id 101: {ids}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_graph_canvases_have_nonzero_height(tmp_path):
    # 🟡-3（縦潰れ回帰・②の弱 assertion 教訓）: width だけでなく高さも実効検証する。
    #   CSS 未移植時は .graph-grid が display:grid 無効＝ブロック単列となり、各 canvas が
    #   ペイン高に膨らむ（実測 592px）か、grid 有効でも .cv に min-height 無いと 0 付近に潰れる。
    #   移植後は grid-auto-rows:210px に追従して 0 < 高さ <= 300（行高+padding 内）に収まる。
    #   下限 0 で「潰れ」を、上限で「grid 無効のブロック膨張（単列・不可読）」を同時に検出する。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_graph_tab(page)
        page.wait_for_selector("#graphGrid canvas", timeout=4000)
        # .graph-grid が実際に 2 列グリッドとして機能している（display:grid・行高固定）。
        grid = page.eval_on_selector(
            ".graph-grid",
            "el => ({disp: getComputedStyle(el).display, "
            "cols: getComputedStyle(el).gridTemplateColumns.split(' ').length})")
        assert grid["disp"] == "grid", f".graph-grid is not display:grid: {grid}"
        assert grid["cols"] == 2, f".graph-grid is not 2-column: {grid}"
        heights = page.eval_on_selector_all(
            "#graphGrid canvas", "els => els.map(el => el.clientHeight)")
        assert len(heights) == 9, f"expected 9 canvases, got {len(heights)}"
        assert all(0 < h <= 300 for h in heights), (
            f"graph canvas height out of bounds (collapsed or grid-less block): {heights}")
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_scatter_hold_datasets_are_seg_independent_is_oos(tmp_path):
    # 🟡-1（二重表示回帰）: OOS 区間表示時も散布/保有時間の dataset0=IS・dataset1=OOS で
    #   固定（seg 非依存）。旧実装は dataset0=cur（OOS 区間で OOS）となり OOS が二重表示された。
    #   id 分離（IS=1,2,3 / OOS=101,102）で dataset0 が IS データであることを実証する。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_graph_tab(page)
        page.click('.segbtn[data-seg="oos"]')   # 点15: OOS 区間に切替（select 廃止 → .segbtn）
        page.wait_for_function(
            "() => { const cs = Object.values(window.__graphsCharts||{}); "
            "return cs.length >= 9; }", timeout=4000)
        ds = page.evaluate("""() => {
          const ch = window.__graphsCharts.cf;       // Correlation (Profits, MFE)
          return {
            d0: ch.data.datasets[0].data.map(p => p.id),
            d1: ch.data.datasets[1].data.map(p => p.id),
            l0: ch.data.datasets[0].label, l1: ch.data.datasets[1].label,
          };
        }""")
        # dataset0 は IS（id 1,2,3）・dataset1 は OOS（id 101,102）。OOS の二重表示でない。
        assert ds["l0"] == "IS" and ds["l1"] == "OOS", f"dataset labels wrong: {ds}"
        assert sorted(ds["d0"]) == [1, 2, 3], f"dataset0 is not IS scatter (seg-dependent?): {ds}"
        assert sorted(ds["d1"]) == [101, 102], f"dataset1 is not OOS scatter: {ds}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_segment_switch_no_chart_double_bind(tmp_path):
    # SPEC#4.3（R-3）: 区間切替（IS↔OOS）で Chart.js の "Canvas is already in use" が出ない。
    p, browser, page, httpd = _launch(tmp_path)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open_graph_tab(page)
        # IS→OOS→IS と切替（毎回 buildGraphs が destroy→再構築する）。点15: .segbtn クリック。
        for v in ("oos", "is", "oos"):
            page.click(f'.segbtn[data-seg="{v}"]')
            page.wait_for_function(
                "() => { const cs = Object.values(window.__graphsCharts||{}); "
                "return cs.length >= 9; }", timeout=4000)
        # canvas は再構築されても 9 枚（二重バインドで増えない・例外も出ない）。
        canvases = page.query_selector_all("#graphGrid canvas")
        assert len(canvases) == 9, f"after switches expected 9 canvases, got {len(canvases)}"
        assert not any("already in use" in e for e in errors), (
            f"Chart.js double-bind error on segment switch: {errors}")
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()
