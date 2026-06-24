"""F-3 最小単位ヒートマップ＋チャート連動 E2E 検証（Playwright・詳細設計 §8.4 / SPEC#3）。

検証対象（SPEC#3）:
  1. ヒートマップが描画される（5 ビュー・wday×hour セル td.cell・配色 background が非透明）。
  2. セルクリック→チャートのマーカーがフィルタ抽出される（linkage.activeFilter が該当 id Set・
     chart が当該 trade のみ抽出。②の弱 assertion 教訓に従い要素/computed-style で実効果を観測）。
  3. セルクリック→table の非該当行が dim する（computed opacity 変化で実証）。

決定論のため entry_time を既知 UTC セル（Mon hour0 / Sun hour23）に固定した小さな多取引ダミー
report.json を一時 web ルートに配信する。chromium 不在環境では skip（既存 verify.py 規約準拠）。
"""
from __future__ import annotations

import http.server
import json
import shutil
import socket
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1].parent / "web"

pytestmark = pytest.mark.e2e

# 既知 UTC entry_time（back derive / front heatmap と同規約・同定数）。
_TS_MON_H0 = 1776643200    # 2026-04-20 00:00:00 UTC（Mon hour0）
_TS_SUN_H23 = 1776639600   # 2026-04-19 23:00:00 UTC（Sun hour23）


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


def _serve(directory: str, port: int):
    handler = lambda *a, **k: _NoCacheHandler(*a, directory=directory, **k)
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def _trade(i, entry_time, side, ep, profit):
    return {
        "id": i, "side": side, "entry_time": entry_time, "exit_time": entry_time + 60,
        "entry_price": ep, "exit_price": ep + profit, "profit": profit,
        "volume": "0.1", "sl": f"{ep - 2:.1f}", "tp": f"{ep + 5:.1f}",
        "order": i, "comment": "tp" if profit > 0 else "sl",
        "balance": 10000 + profit, "hold_sec": 60, "mfe": 1.0, "mae": 0.5,
    }


def _heat_cells(trades):
    """back derive.heat_cells と同規約（weekday() Mon=0・UTC）で cells を作る（ダミー payload 用）。"""
    from datetime import datetime, timezone
    WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    acc = {}
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc)
        key = (WEEK[dt.weekday()], dt.hour)
        c = acc.setdefault(key, {"profit": 0.0, "count": 0, "wins": 0})
        c["profit"] += t["profit"]; c["count"] += 1
        if t["profit"] > 0:
            c["wins"] += 1
    return [{"wday": w, "hour": h, "profit": round(v["profit"], 1),
             "count": v["count"], "wins": v["wins"]} for (w, h), v in acc.items()]


def _multi_trade_report() -> dict:
    # id1,id3 = Mon hour0 / id2 = Sun hour23 （セルクリックで id1,id3 が抽出される設計）
    trades = [
        _trade(1, _TS_MON_H0, "buy", 100.0, 50.0),
        _trade(2, _TS_SUN_H23, "sell", 110.0, -20.0),
        _trade(3, _TS_MON_H0, "buy", 102.0, 50.0),
    ]
    bars = []
    base = _TS_SUN_H23 - 3600
    for i in range(0, 600):
        t = base + i * 60
        bars.append({"time": t, "open": 100.0 + (i % 5), "high": 112.0,
                     "low": 95.0, "close": 105.0 + (i % 3)})
    heat = _heat_cells(trades)
    seg = {
        "label": "IS",
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": len(bars), "trades": len(trades), "period": "2026.04.01-04.14"},
        "report": {},
        "bars": bars,
        "trades": trades,
        "orders": [],
        "agg": {"balance_curve": [{"time": t["exit_time"], "value": t["balance"]} for t in trades],
                "weekorder": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                "heat": heat},
    }
    return {
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "initial_deposit": 10000.0, "split": "2026-04-15"},
        "segments": {"is": seg, "oos": seg},
        "summary": {
            "is": {"trades": 3, "net": 80.0, "final_balance": 10080.0, "win_rate": 66.67,
                   "profit_factor": 5.0, "expectancy": 26.67, "payoff": 2.5,
                   "return_pct": 0.8, "max_dd_pct": 0.2},
            "oos": {"trades": 3, "net": 80.0, "final_balance": 10080.0, "win_rate": 66.67,
                    "profit_factor": 5.0, "expectancy": 26.67, "payoff": 2.5,
                    "return_pct": 0.8, "max_dd_pct": 0.2},
        },
        "degradation": {},
        "verdict": {"result": "pass", "reasons": ["OOSでも優位性を維持"]},
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
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright 未導入")
    root = _build_web_root(tmp_path)
    port = _free_port()
    httpd = _serve(str(root), port)
    p = sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception:
        httpd.shutdown()
        p.stop()
        pytest.skip("chromium 未導入")
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__READY === true", timeout=8000)
    return p, browser, page, httpd


def _open_heat_tab(page):
    """ヒートマップタブを開いて heatHost を可視化する。"""
    page.click('.mv-tab[data-tab="heat"]')
    page.wait_for_selector("#heatHost td.cell", timeout=4000)


def test_heatmap_renders_five_views_with_colored_cells(tmp_path):
    # SPEC#3.1: ヒートマップ（5 ビュー）が wday×hour セルで描画され、配色が実適用される。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_heat_tab(page)
        # 5 ビュー（heatBlock）
        blocks = page.query_selector_all("#heatHost .heatBlock")
        assert len(blocks) == 5, f"expected 5 heat views, got {len(blocks)}"
        # wday×hour セルが描画される（td.cell が 1 つ以上）
        cells = page.query_selector_all("#heatHost td.cell")
        assert len(cells) > 0, "no heat cells rendered"
        # 配色が実適用（背景が透明でない＝CSS/inline background が効いている）
        bg = page.eval_on_selector(
            "#heatHost td.cell", "el => getComputedStyle(el).backgroundImage")
        assert bg and bg != "none", f"heat cell has no background color: {bg!r}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_heatmap_cell_click_filters_chart_markers(tmp_path):
    # SPEC#3.2（最重要・連動）: Mon hour0 セルをクリック→linkage.activeFilter が {1,3}（該当 trade）。
    # chart はこの filter で当該 trade のみ抽出する（linkage 経由の連動を要素で実証）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_heat_tab(page)
        # 損益ビュー（先頭 heatBlock）の Mon hour0 セルをクリック
        page.click('#heatHost .heatBlock:first-child td.cell[data-w="Mon"][data-h="0"]')
        page.wait_for_function(
            "window.__linkage && window.__linkage.activeFilter && "
            "window.__linkage.activeFilter.size === 2", timeout=4000)
        ids = page.evaluate(
            "Array.from(window.__linkage.activeFilter).sort((a,b)=>a-b)")
        assert ids == [1, 3], f"cell click did not extract trades 1,3: {ids}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_heatmap_cell_click_dims_unmatched_table_rows(tmp_path):
    # SPEC#3.2（table 連動）: セルクリック→非該当行(id=2)が dim（computed opacity が下がる）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_heat_tab(page)

        def opacity(data_id):
            return page.eval_on_selector(
                f'#tradeTable tbody tr.tw[data-id="{data_id}"]',
                "el => getComputedStyle(el).opacity")

        before = opacity(2)
        page.click('#heatHost .heatBlock:first-child td.cell[data-w="Mon"][data-h="0"]')
        page.wait_for_function(
            "window.__linkage && window.__linkage.activeFilter && "
            "window.__linkage.activeFilter.size === 2", timeout=4000)
        after = opacity(2)  # id=2 は非該当 → dim
        assert float(after) < float(before), (
            f"unmatched row not dimmed: before={before} after={after}")
        # 該当行(id=1)は dim しない（不透明のまま）
        assert float(opacity(1)) > float(after), "matched row should not be dimmed"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()
