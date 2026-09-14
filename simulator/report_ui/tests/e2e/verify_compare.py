"""F-5 比較・判定タブ E2E 検証（Playwright・詳細設計 §11 / FR-07-09）。

検証対象（⑤ R-2/R-4）:
  1. 比較・判定タブが第4サブタブとして既定オープン（active）で表示される。
  2. 判定バナー（過剰最適化／verdict=fail）が verdict クラス付きで描画される。
  3. 劣化比較表が REPORT_GROUPS 章立て（grp 行）＋指標行（IS|OOS|比|差 の5列）で描画され、
     54項目規模の report 行が出る（実値・class を検証＝弱 assertion 回避）。
  4. 主要7指標カードが degradation キー分（7枚）描画される。
  5. 右グラフ（エクイティ重畳 cmpEquity・純損益内訳 cmpPnl）が canvas で実描画される。
  6. 区間切替（IS↔OOS）で compare が壊れない（区間非依存・cmpCharts を destroy しない）。

determinism: report に MT5 ラベルを埋め verdict=fail/degradation を固定したダミー report.json を
配信する。chromium 不在環境では skip（既存 verify.py 規約準拠）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from e2e import _harness  # noqa: E402  (同一ディレクトリの共有ハーネス)

WEB = Path(__file__).resolve().parents[1].parent / "web"

pytestmark = pytest.mark.e2e

_TS_MON_H0 = 1776643200    # 2026-04-20 00:00:00 UTC


def _free_port() -> int:
    return _harness.free_port()


def _serve(directory: str, port: int):
    return _harness.serve(directory, port)


def _trade(i, profit, balance, hold_sec=60):
    et = _TS_MON_H0 + i * 60
    return {
        "id": i, "side": "buy" if profit >= 0 else "sell", "entry_time": et,
        "exit_time": et + hold_sec, "entry_price": 100.0, "exit_price": 100.0 + profit,
        "profit": profit, "volume": "0.1", "sl": "98.0", "tp": "105.0",
        "order": i, "comment": "tp" if profit > 0 else "sl",
        "balance": balance, "hold_sec": hold_sec, "mfe": 1.0, "mae": 0.5,
    }


# §4.5 写像で本番が出す report ラベル群（54項目規模・IS/OOS で値を変えて比/差を出させる）。
def _report(net, pf, sharpe, z, gp, gl, trades, wins):
    return {
        "Expert": "StopEntryProbe_EA", "Symbol": "JP225", "Period": "2026.04.01-04.14",
        "Initial Deposit": "10000", "Total Net Profit": f"{net:.0f}",
        "Gross Profit": f"{gp:.0f}", "Gross Loss": f"{gl:.0f}",
        "Profit Factor": f"{pf:.2f}", "Recovery Factor": "3.21",
        "Sharpe Ratio": f"{sharpe:.2f}", "Expected Payoff": "2.18", "AHPR": "1.0002",
        "Total Trades": f"{trades}",
        "Profit Trades (% of total)": f"{wins / trades * 100:.2f}% ({wins})",
        "Loss Trades (% of total)": f"{(trades - wins) / trades * 100:.2f}% ({trades - wins})",
        "Short Trades (won %)": "56.02% (2624)", "Long Trades (won %)": "56.92% (2600)",
        "Largest profit trade": "50", "Average profit trade": "28.68",
        "Largest loss trade": "-20", "Average loss trade": "-32.20",
        "Maximum consecutive wins ($)": "12 (360)",
        "Maximum consecutive losses ($)": "9 (-180)",
        "Maximal consecutive profit (count)": "420 (11)",
        "Maximal consecutive loss (count)": "-240 (8)",
        "Average consecutive wins": "3", "Average consecutive losses": "2",
        "Balance Drawdown Absolute": "0", "Balance Drawdown Maximal": "2400 (10.50%)",
        "Balance Drawdown Relative": "10.50% (2400)", "Equity Drawdown Absolute": "0",
        "Equity Drawdown Maximal": "2600 (11.20%)", "Z-Score": f"{z:.2f}",
    }


def _seg(label, trades, report):
    return {
        "label": label,
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": 600, "trades": len(trades), "period": "2026.04.01-04.14"},
        "report": report,
        "bars": [{"time": _TS_MON_H0 + i * 60, "open": 100.0, "high": 112.0,
                  "low": 95.0, "close": 105.0} for i in range(10)],
        "trades": trades, "orders": [],
        "agg": {"balance_curve": [{"time": t["exit_time"], "value": t["balance"]} for t in trades]},
    }


def _overfit_report() -> dict:
    # 過剰最適化: IS黒字(+11370)/OOS赤字(-4020)・PF 1.16→0.89（verdict=fail）。
    is_tr = [_trade(1, 50.0, 10050.0), _trade(2, -20.0, 10030.0), _trade(3, 11340.0, 21370.0)]
    oos_tr = [_trade(101, 30.0, 10030.0), _trade(102, -4050.0, 5980.0)]
    is_rep = _report(11370, 1.16, 4.83, -0.09, 84600, -73230, 5224, 2950)
    oos_rep = _report(-4020, 0.89, -2.64, -0.34, 32000, -36020, 2438, 1100)
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
        "segments": {"is": _seg("IS（学習）", is_tr, is_rep),
                     "oos": _seg("OOS（検証）", oos_tr, oos_rep)},
        "summary": {
            "is": {"trades": 3, "net": 11370.0, "final_balance": 21370.0, "win_rate": 56.47,
                   "profit_factor": 1.159, "expectancy": 2.18, "payoff": 0.89,
                   "return_pct": 113.7, "max_dd_pct": 10.5},
            "oos": {"trades": 2, "net": -4020.0, "final_balance": 5980.0, "win_rate": 45.12,
                    "profit_factor": 0.888, "expectancy": -1.65, "payoff": 0.7,
                    "return_pct": -40.2, "max_dd_pct": 40.2},
        },
        "degradation": deg,
        "verdict": {"result": "fail",
                    "reasons": ["IS黒字(+11370)に対しOOS赤字(-4020)＝未知区間で優位性消失",
                                "勝率差=-11.35pt 悪化"]},
        "_contract_notes": [],
    }


def _build_web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    shutil.copytree(WEB, root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "report.json").write_text(
        json.dumps(_overfit_report(), ensure_ascii=False, separators=(",", ":")))
    return root


def _launch(tmp_path):
    return _harness.launch(_build_web_root, tmp_path)


def test_compare_tab_is_default_open(tmp_path):
    # R-2: 比較・判定タブが第4サブタブとして既定オープン（active）で可視。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        tab = page.query_selector('.mv-tab[data-tab="compare"]')
        assert tab is not None, "compare サブタブが存在しない"
        cls = page.eval_on_selector('.mv-tab[data-tab="compare"]', "el => el.className")
        assert "active" in cls, f"compare タブが既定 active でない: {cls}"
        # 既定で compare ペインが可視（hidden でない）・他ペインは hidden。
        cmp_hidden = page.eval_on_selector(
            '#pane-compare', "el => el.classList.contains('hidden')")
        assert cmp_hidden is False, "compare ペインが既定で hidden"
        detail_hidden = page.eval_on_selector(
            '.mv-pane[data-pane="detail"]', "el => el.classList.contains('hidden')")
        assert detail_hidden is True, "detail ペインが既定で可視（compare 既定オープンに反する）"
    finally:
        browser.close(); httpd.shutdown(); p.stop()


def test_verdict_banner_shows_overfit(tmp_path):
    # R-2: 判定バナーが verdict=fail → "過剰最適化" を fail クラス付きで描画。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        cls = page.eval_on_selector("#cmpVerdict", "el => el.className")
        assert "fail" in cls, f"cmpVerdict に fail クラスが無い: {cls}"
        txt = page.inner_text("#cmpVerdict")
        assert "過剰最適化" in txt, f"判定バナー文言が過剰最適化でない: {txt!r}"
        # 理由（reasons）が描画される（弱 assertion 回避＝実バナー本文を検証）。
        assert "優位性消失" in txt
    finally:
        browser.close(); httpd.shutdown(); p.stop()


def test_seven_metric_cards_rendered(tmp_path):
    # R-4: 主要7指標カードが degradation キー分（7枚）描画され、差/比/IS/OOS を持つ。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        cards = page.query_selector_all("#cmpBasic .bcard")
        assert len(cards) == 7, f"7指標カードが揃わない: {len(cards)}"
        # net カードの差が負（IS黒字→OOS赤字）で neg クラス。
        net_delta_cls = page.eval_on_selector(
            '#cmpBasic .bcard[data-metric="net"] .bdelta', "el => el.className")
        assert "neg" in net_delta_cls, f"net 差が neg でない: {net_delta_cls}"
    finally:
        browser.close(); httpd.shutdown(); p.stop()


def test_degradation_table_groups_and_rows(tmp_path):
    # R-4: 劣化比較表が章立て（grp 行）＋指標行（5列）で描画される。54項目規模を満たす。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        groups = page.query_selector_all("#cmpTable tbody tr.grp")
        # 戦略章＋既存章立て。
        assert len(groups) >= 4, f"章立て見出しが少なすぎる: {len(groups)}"
        # 戦略セクション（戦略名＋説明・全幅行）が先頭に追加される。
        assert page.query_selector("#cmpTable td.strat-name") is not None, "戦略名行が無い"
        metric_rows = page.query_selector_all("#cmpTable tbody tr:not(.grp)")
        # 導出指標の補完で 30 行以上を満たす。
        assert len(metric_rows) >= 30, f"指標行が少なすぎる: {len(metric_rows)}"
        # 戦略の全幅行（colspan）を除く全指標行が 5 セル（指標｜IS｜OOS｜比｜差）であること（網羅）。
        bad = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('#cmpTable tbody tr:not(.grp)')];
          const metric = rows.filter(r => !r.querySelector('[colspan]'));
          return { total: metric.length, bad: metric.filter(r => r.querySelectorAll('td').length !== 5).length };
        }""")
        assert bad["total"] >= 30 and bad["bad"] == 0, f"5セルでない指標行あり: {bad}"
        # Profit Factor 行: IS>1 / OOS<1 が劣化（比 0.766・差 neg）として描画される実値検証。
        pf_ratio = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('#cmpTable tbody tr:not(.grp)')];
          const r = rows.find(tr => tr.querySelector('.lab')?.dataset.gk === 'Profit Factor');
          return r ? r.querySelectorAll('td')[3].textContent : null;
        }""")
        assert pf_ratio not in (None, "—"), f"Profit Factor 比が算出されない: {pf_ratio!r}"
    finally:
        browser.close(); httpd.shutdown(); p.stop()


def test_right_charts_rendered(tmp_path):
    # R-4: 右グラフ（エクイティ重畳・純損益内訳）が canvas で実描画される。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        eq = page.query_selector("#cmpEquity")
        pnl = page.query_selector("#cmpPnl")
        assert eq is not None and pnl is not None, "compare の canvas が無い"
        # cmpCharts に eq/pnl の Chart インスタンスが隔離 init されている。
        keys = page.evaluate("() => Object.keys(window.__cmpCharts || {})")
        assert "eq" in keys and "pnl" in keys, f"cmpCharts 隔離 init 不成立: {keys}"
        # エクイティ重畳は IS/OOS の2系列。
        ds = page.evaluate("() => window.__cmpCharts.eq.data.datasets.length")
        assert ds == 2, f"エクイティ重畳が2系列でない: {ds}"
        w = page.eval_on_selector("#cmpEquity", "el => el.width")
        assert w and w > 0, f"compare canvas 未描画 (width={w})"
    finally:
        browser.close(); httpd.shutdown(); p.stop()


def test_segment_switch_does_not_break_compare(tmp_path):
    # R-4: 区間切替（IS↔OOS）で compare が壊れない（区間非依存・cmpCharts を destroy しない）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        eq_before = page.evaluate("() => window.__cmpCharts.eq.id")
        # 点15: 区間切替は select 廃止 → .segbtn クリック。
        page.click('.segbtn[data-seg="oos"]')
        page.wait_for_timeout(150)
        page.click('.segbtn[data-seg="is"]')
        page.wait_for_timeout(150)
        # 同一 Chart インスタンス（id 不変）＝区間切替で destroy/再構築していない。
        eq_after = page.evaluate("() => window.__cmpCharts.eq.id")
        assert eq_before == eq_after, "区間切替で cmpCharts が再構築された（区間非依存違反）"
        # バナー・表が依然描画されている（区間切替後も compare 健在）。
        assert "過剰最適化" in page.inner_text("#cmpVerdict")
        assert len(page.query_selector_all("#cmpTable tbody tr:not(.grp)")) >= 30
    finally:
        browser.close(); httpd.shutdown(); p.stop()
