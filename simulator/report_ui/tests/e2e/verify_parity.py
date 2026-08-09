"""パリティ 18 点 E2E 検証（Playwright・試作 prototype_260623-02 完全準拠）。

設計書 §2 の 18 点それぞれに最低 1 assertion を当てる（DOM 要素 ID・系列生成・状態機械）。
試作の実挙動を一次情報とし、現行モジュール実装が同等の DOM/系列/状態を用意することを実証する。

determinism: bars + agg（balance_curve/heat/entries/pl/scatter/hold/weekorder）を埋めた
ダミー report.json を配信する。chromium 不在環境では skip（既存 verify.py 規約準拠）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from e2e import _harness  # noqa: E402  (同一ディレクトリの共有ハーネス)

WEB = Path(__file__).resolve().parents[1].parent / "web"

pytestmark = pytest.mark.e2e

_TS = 1776643200  # 2026-04-20 00:00:00 UTC（Mon・hour0）


def _free_port() -> int:
    return _harness.free_port()


def _serve(directory: str, port: int):
    return _harness.serve(directory, port)


def _trade(i, profit, balance, t0):
    return {
        "id": i, "side": "buy" if profit >= 0 else "sell", "entry_time": t0,
        "exit_time": t0 + 60, "entry_price": 100.0, "exit_price": 100.0 + profit,
        "profit": profit, "volume": "0.1", "sl": "98.0", "tp": "105.0",
        "order": i, "comment": "tp" if profit > 0 else "sl",
        "balance": balance, "hold_sec": 60, "mfe": 1.0, "mae": 0.5,
    }


def _agg(trades):
    return {
        "entries_hour": {"0": len(trades)}, "entries_session": {},
        "entries_wday": {"Mon": len(trades)}, "entries_month": {"2026-04": len(trades)},
        "pl_hour": {"0": sum(t["profit"] for t in trades)}, "pl_wday": {"Mon": 1.0},
        "pl_month": {"2026-04": 1.0},
        "balance_curve": [{"time": t["exit_time"], "value": t["balance"]} for t in trades],
        "scatter_mfe": [{"x": 1.0, "y": t["profit"], "id": t["id"]} for t in trades],
        "scatter_mae": [{"x": 0.5, "y": t["profit"], "id": t["id"]} for t in trades],
        "hold_pl": {"<1m": 1.0, "1-2m": -1.0}, "hold_cnt": {"<1m": 1, "1-2m": 1},
        "weekorder": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "heat": [{"wday": "Mon", "hour": 0, "profit": 50.0, "count": 2, "wins": 1}],
    }


def _report(net, pf):
    return {
        "Expert": "StopEntryProbe_EA", "Symbol": "JP225", "Period": "2026.04.01-04.14",
        "Initial Deposit": "10000", "Total Net Profit": f"{net:.0f}",
        "Gross Profit": "84600", "Gross Loss": "-73230", "Profit Factor": f"{pf:.2f}",
        "Sharpe Ratio": "4.83", "Total Trades": "5224",
        "Balance Drawdown Maximal": "2400 (10.50%)", "Z-Score": "-0.09",
    }


def _seg(label, trades, report):
    return {
        "label": label,
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": 4, "trades": len(trades), "period": "2026.04.01-04.14"},
        "report": report,
        "bars": [{"time": _TS + i * 60, "open": 100.0, "high": 112.0, "low": 95.0, "close": 105.0}
                 for i in range(4)],
        "trades": trades, "orders": [], "agg": _agg(trades),
    }


def _payload() -> dict:
    is_tr = [_trade(1, 50.0, 10050.0, _TS), _trade(2, -20.0, 10030.0, _TS + 60),
             _trade(3, 11340.0, 21370.0, _TS + 120)]
    oos_tr = [_trade(101, 30.0, 10030.0, _TS), _trade(102, -4050.0, 5980.0, _TS + 60)]
    deg = {
        "net": {"is": 11370.0, "oos": -4020.0, "ratio": -0.354, "delta": -15390.0},
        "profit_factor": {"is": 1.159, "oos": 0.888, "ratio": 0.766, "delta": -0.27},
        "win_rate": {"is": 56.47, "oos": 45.12, "ratio": 0.799, "delta": -11.35},
        "expectancy": {"is": 2.18, "oos": -1.65, "ratio": -0.757, "delta": -3.83},
        "payoff": {"is": 0.89, "oos": 0.7, "ratio": 0.787, "delta": -0.19},
        "return_pct": {"is": 113.7, "oos": -40.2, "ratio": -0.354, "delta": -153.9},
        "max_dd_pct": {"is": 10.5, "oos": 40.2, "ratio": 3.829, "delta": 29.7},
    }
    return {
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "initial_deposit": 10000.0, "split": "2026-04-15"},
        "segments": {"is": _seg("IS（学習）", is_tr, _report(11370, 1.16)),
                     "oos": _seg("OOS（検証）", oos_tr, _report(-4020, 0.89))},
        "summary": {
            "is": {"trades": 3, "net": 11370.0, "final_balance": 21370.0, "win_rate": 56.47,
                   "profit_factor": 1.159, "expectancy": 2.18, "payoff": 0.89,
                   "return_pct": 113.7, "max_dd_pct": -11.52},
            "oos": {"trades": 2, "net": -4020.0, "final_balance": 5980.0, "win_rate": 45.12,
                    "profit_factor": 0.888, "expectancy": -1.65, "payoff": 0.7,
                    "return_pct": -40.2, "max_dd_pct": -40.2},
        },
        "degradation": deg,
        "verdict": {"result": "fail", "reasons": ["IS黒字に対しOOS赤字＝未知区間で優位性消失"]},
        "_contract_notes": [],
    }


def _build_web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    shutil.copytree(WEB, root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "report.json").write_text(
        json.dumps(_payload(), ensure_ascii=False, separators=(",", ":")))
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
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__READY === true", timeout=8000)
    return p, browser, page, httpd, errors


def test_parity_all_18_points(tmp_path):
    """18 点を 1 ブラウザセッションで一括検証する（点番号→assertion）。"""
    p, browser, page, httpd, errors = _launch(tmp_path)
    try:
        # 点1 Balance 窓（エリア系列）: #paneBal が存在し lwc が描画されている。
        assert page.query_selector("#paneBal") is not None, "点1: #paneBal が無い"
        # 点2 Drawdown 窓（0 基準ベースライン）: #paneDD が存在。
        assert page.query_selector("#paneDD") is not None, "点2: #paneDD が無い"
        # 点1/2: 各 subpane に lwc canvas が描画される（多窓 createChart 成立）。
        bal_canvas = page.eval_on_selector_all("#paneBal canvas", "els => els.length")
        dd_canvas = page.eval_on_selector_all("#paneDD canvas", "els => els.length")
        assert bal_canvas >= 1, f"点1: Balance 窓に canvas が無い: {bal_canvas}"
        assert dd_canvas >= 1, f"点2: Drawdown 窓に canvas が無い: {dd_canvas}"

        # 点3 3窓論理レンジ同期 / 点4 クロスヘア同期: 3 つの独立チャート枠が存在する
        #   （#price-chart + #paneBal + #paneDD）。同期コードは純関数で node:test 被覆。
        assert page.query_selector("#price-chart") is not None, "点3/4: #price-chart が無い"
        chart_canvas = page.eval_on_selector_all("#price-chart canvas", "els => els.length")
        assert chart_canvas >= 1, f"点3/4: ローソク窓に canvas が無い: {chart_canvas}"

        # 点5 リサイザ rz1/rz2: 高さ可変リサイザが存在。
        assert page.query_selector("#rz1") is not None, "点5: #rz1 が無い"
        assert page.query_selector("#rz2") is not None, "点5: #rz2 が無い"

        # 点6 ⛶チャート最大化: ボタンクリックで chart モード（maxChart.on）→ 復元。
        assert page.query_selector("#maxChart") is not None, "点6: #maxChart が無い"
        page.click("#maxChart")
        assert page.eval_on_selector("#maxChart", "el => el.classList.contains('on')"), "点6: 最大化 on にならない"
        # chart 最大化中は下部 #bottom が非表示。
        assert page.eval_on_selector("#bottom", "el => el.style.display === 'none'"), "点6: 下部が隠れない"
        page.click("#maxChart")  # 復元
        assert not page.eval_on_selector("#maxChart", "el => el.classList.contains('on')"), "点6: 復元しない"

        # 点7 chartBadge: 可視取引件数 readout が存在し値が入る。
        assert page.query_selector("#chartBadge") is not None, "点7: #chartBadge が無い"
        badge = page.inner_text("#chartBadge")
        assert "trades in view" in badge, f"点7: chartBadge に件数 readout が無い: {badge!r}"

        # 点8 サマリー(Report)タブ: タブを開くと区間別 report が章立て（.rcard2）で描画。
        page.click('.mv-tab[data-tab="report"]')
        rcards = page.query_selector_all("#reportGrid .rcard2")
        assert len(rcards) >= 2, f"点8: Report 章立てが描画されない: {len(rcards)}"
        rep_txt = page.inner_text("#reportGrid")
        assert "総純損益" in rep_txt, "点8: Report に日本語ラベルが無い"

        # 点9 用語説明(Glossary)タブ＋hover tip: タブで gcard 描画・#tip 要素が body に存在。
        page.click('.mv-tab[data-tab="glossary"]')
        gcards = page.query_selector_all("#glossHost .gcard")
        assert len(gcards) >= 2, f"点9: Glossary カードが描画されない: {len(gcards)}"
        assert page.query_selector("#tip") is not None, "点9: hover tip 要素 (#tip) が無い"

        # 点10 ⛶明細最大化: ボタンで detail モード（チャート枠 #chartWrap 非表示）→ 復元。
        assert page.query_selector("#maxDetail") is not None, "点10: #maxDetail が無い"
        page.click("#maxDetail")
        assert page.eval_on_selector("#chartWrap", "el => el.style.display === 'none'"), "点10: detail で chart が隠れない"
        assert page.eval_on_selector("#graphHost", "el => el.classList.contains('gfill')"), "点10: graphs が gfill 充填されない"
        page.click("#maxDetail")  # 復元
        assert not page.eval_on_selector("#chartWrap", "el => el.style.display === 'none'"), "点10: 復元しない"

        # 点11 rz0＋3状態レイアウト: rz0 が存在し、normal/chart/detail の 3 状態を遷移できた
        #   （点6/点10 で chart/detail を確認済・rz0 の存在で 3 分割レイアウト成立）。
        assert page.query_selector("#rz0") is not None, "点11: #rz0 が無い"

        # 点12 cmpDD（分割縦線）: 比較タブの cmpDD canvas が cmpCharts.dd として隔離 init。
        page.click('.mv-tab[data-tab="compare"]')
        assert page.query_selector("#cmpDD") is not None, "点12: #cmpDD canvas が無い"
        cmp_keys = page.evaluate("() => Object.keys(window.__cmpCharts || {})")
        assert "dd" in cmp_keys, f"点12: cmpCharts.dd が無い: {cmp_keys}"
        dd_ds = page.evaluate("() => window.__cmpCharts.dd.data.datasets.length")
        assert dd_ds == 2, f"点12: cmpDD が IS/OOS 2系列でない: {dd_ds}"

        # 点13 cmpRadar: レーダーが 6 軸 IS/OOS 2系列で隔離 init。
        assert "radar" in cmp_keys, f"点13: cmpCharts.radar が無い: {cmp_keys}"
        radar = page.evaluate(
            "() => ({type: window.__cmpCharts.radar.config.type, "
            "axes: window.__cmpCharts.radar.data.labels.length, "
            "ds: window.__cmpCharts.radar.data.datasets.length})")
        assert radar["type"] == "radar", f"点13: radar 型でない: {radar}"
        assert radar["axes"] == 6 and radar["ds"] == 2, f"点13: レーダー軸/系列が 6/2 でない: {radar}"

        # 点14 cmpDeg: 劣化棒が横棒（indexAxis=y）で 6 指標の維持率を描画。
        assert "deg" in cmp_keys, f"点14: cmpCharts.deg が無い: {cmp_keys}"
        deg_n = page.evaluate("() => window.__cmpCharts.deg.data.labels.length")
        assert deg_n == 6, f"点14: 劣化棒が 6 指標でない: {deg_n}"

        # 点15 区間トグルボタン（select 廃止）: .segbtn が IS/OOS で存在し、#seg-select は無い。
        assert page.query_selector("#seg-select") is None, "点15: 旧 #seg-select が残っている"
        assert page.query_selector('.segbtn[data-seg="is"]') is not None, "点15: IS トグルが無い"
        assert page.query_selector('.segbtn[data-seg="oos"]') is not None, "点15: OOS トグルが無い"
        # トグルクリックで active が移る（IS→OOS）。
        page.click('.segbtn[data-seg="oos"]')
        page.wait_for_timeout(120)
        assert page.eval_on_selector('.segbtn[data-seg="oos"]', "el => el.classList.contains('on')"), "点15: OOS トグルが on にならない"
        page.click('.segbtn[data-seg="is"]')
        page.wait_for_timeout(120)

        # 点16 hSel 選択ラベル: ヘッダに #hSel が存在し、明細 hover で選択ラベルが入る。
        assert page.query_selector("#hSel") is not None, "点16: #hSel が無い"
        # chart→linkage の hover フックを駆動して hSel が埋まることを実証（マーカー画素 hover の代理）。
        page.evaluate("() => window.__chartEmitMarkerHover(1)")
        page.wait_for_timeout(80)
        hsel = page.inner_text("#hSel")
        assert "#1" in hsel, f"点16: hover で hSel ラベルが出ない: {hsel!r}"
        page.evaluate("() => window.__chartEmitMarkerHover(null)")

        # 点17 最上部 #summary-cards 削除: 試作に無い現行独自カードが DOM に存在しない。
        assert page.query_selector("#summary-cards") is None, "点17: #summary-cards が残存している"

        # 点18 フィルタ解除ピル＋件数: ヒートセルクリックでフィルタ→ピル/件数（明細ペイン内）。
        page.click('.mv-tab[data-tab="heat"]')
        page.click('#heatHost .heatBlock:first-child td.cell[data-w="Mon"][data-h="0"]')
        page.wait_for_timeout(120)
        # ピル/件数は明細ペイン内（フィルタは区間状態に保持され、タブ非依存で立つ）。
        pill_shown = page.eval_on_selector("#clearFilter", "el => el.style.display !== 'none'")
        assert pill_shown, "点18: フィルタ解除ピルが表示されない（display 切替）"
        count_txt = page.inner_text("#detailCount")
        assert "抽出" in count_txt and "件" in count_txt, f"点18: 件数表示が無い: {count_txt!r}"
        # 明細タブを開いてからピルを ✕ クリック（解除・ピル非表示）。
        page.click('.mv-tab[data-tab="detail"]')
        page.click("#clearFilter")
        page.wait_for_timeout(120)
        assert page.eval_on_selector("#clearFilter", "el => el.style.display === 'none'"), "点18: ピルが解除されない"

        # 全体: JS エラーが出ていない（18 点の連携が例外なく成立）。
        assert errors == [], f"page errors during parity run: {errors}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_tab_double_click_toggles_detail_max(tmp_path):
    """タブのダブルクリックで下部（明細/タブ領域）を 正常⇄拡大 トグルする。

    正常→ダブルクリック→拡大（#chartWrap 非表示）、拡大→ダブルクリック→正常（復元）。"""
    p, browser, page, httpd, errors = _launch(tmp_path)
    try:
        # 初期は正常（chartWrap が表示されている）。
        assert not page.eval_on_selector("#chartWrap", "el => el.style.display === 'none'"), "初期が正常でない"
        # 正常 → ダブルクリック → 拡大（detail max・chartWrap 非表示）。
        page.dblclick('.mv-tab[data-tab="detail"]')
        page.wait_for_timeout(120)
        assert page.eval_on_selector("#chartWrap", "el => el.style.display === 'none'"), "ダブルクリックで拡大しない"
        # 拡大 → ダブルクリック → 正常（復元）。
        page.dblclick('.mv-tab[data-tab="detail"]')
        page.wait_for_timeout(120)
        assert not page.eval_on_selector("#chartWrap", "el => el.style.display === 'none'"), "ダブルクリックで正常へ戻らない"
        assert errors == [], f"page errors: {errors}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()
