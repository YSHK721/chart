"""F-2 取引明細テーブル＋双方向ハイライト E2E 検証（Playwright・詳細設計 §8.4 / SPEC §2.2）。

検証対象（SPEC#2）:
  1. 取引明細テーブルが描画される（SPEC 11列・行数 = trades 件数）。
  2. 列ヘッダクリックでソートできる（行順が変わる）。
  3. チャート売買マーカー hover → 該当明細行ハイライト。
  4. 明細行 hover → チャート該当マーカー強調（linkage の hoverTradeId が更新される）。

決定論のため小さな多取引ダミー report.json を一時 web ルートに書いて配信する
（実 report.json 5224 件・MARKER_CAP 超を避け FIRST 原則を満たす）。
chromium 不在環境では skip（既存 verify.py の skip 規約準拠）。
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


def _trade(i, side, ep, xp, profit, comment):
    """trades[] 16キー（詳細設計 §4.1）相当のダミー行。"""
    t0 = 1000 + i * 120
    return {
        "id": i, "side": side, "entry_time": t0, "exit_time": t0 + 60,
        "entry_price": ep, "exit_price": xp, "profit": profit,
        "volume": "0.1", "sl": f"{ep - 2:.1f}", "tp": f"{ep + 5:.1f}",
        "order": i, "comment": comment, "balance": 10000 + profit,
        "hold_sec": 60, "mfe": 1.0, "mae": 0.5,
    }


def _multi_trade_report() -> dict:
    trades = [
        _trade(1, "buy", 100.0, 105.0, 50.0, "tp"),
        _trade(2, "sell", 110.0, 108.0, -20.0, "sl"),
        _trade(3, "buy", 102.0, 107.0, 50.0, "tp"),
    ]
    bars = []
    for i in range(0, 600):
        t = 1000 + i * 60
        bars.append({"time": t, "open": 100.0 + (i % 5), "high": 112.0,
                     "low": 95.0, "close": 105.0 + (i % 3)})
    seg = {
        "label": "IS",
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": len(bars), "trades": len(trades), "period": "2026.04.01-04.14"},
        "report": {},
        "bars": bars,
        "trades": trades,
        "orders": [],
        "agg": {"balance_curve": [{"time": t["exit_time"], "value": t["balance"]} for t in trades],
                "heat": []},
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
    """web/ をコピーして data/report.json を小さな多取引ダミーに差し替えた一時ルートを作る。"""
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


def _open_detail_tab(page):
    """取引明細サブタブを開く（F-5 で比較・判定タブが既定オープンになったため、
    明細行の hover / ソートヘッダ click（要素の可視性が必要な操作）の前に明示的に開く）。"""
    page.click('.mv-tab[data-tab="detail"]')
    page.wait_for_selector('#tradeTable tbody tr.tw', state="visible", timeout=4000)


def test_detail_table_renders_11_cols_and_row_count(tmp_path):
    p, browser, page, httpd = _launch(tmp_path)
    try:
        # SPEC 11列ヘッダ
        ths = page.query_selector_all("#tradeTable thead th")
        assert len(ths) == 11, f"expected 11 cols, got {len(ths)}"
        # 行数 = trades 件数（3）
        rows = page.query_selector_all("#tradeTable tbody tr.tw")
        assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
        # Symbol 列が meta.symbol を描画
        body = page.inner_text("#tradeTable tbody")
        assert "JP225" in body
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_detail_table_cell_values_preserved_as_text(tmp_path):
    # 🟡B 回帰保護: セル描画を textContent 化しても 11 列の値表示が同一であること。
    # 各行の Type 列(buy/sell) と Symbol 列(JP225) が正確に表示される（振る舞い不変）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        # id=1 行の各セルテキストを取得し、射影された値が欠落なく表示されることを確認
        texts = page.eval_on_selector(
            '#tradeTable tbody tr.tw[data-id="1"]',
            "tr => Array.from(tr.children).map(td => td.textContent)")
        assert len(texts) == 11, f"expected 11 cells, got {len(texts)}: {texts}"
        # 射影値（Symbol=JP225 / Type=buy / Volume=0.1）がそのまま表示される
        assert "JP225" in texts, f"symbol cell missing: {texts}"
        assert "buy" in texts, f"type cell missing: {texts}"
        assert "0.1" in texts, f"volume cell missing: {texts}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_detail_table_sort_reorders_rows(tmp_path):
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_detail_tab(page)
        def first_row_id():
            return page.eval_on_selector(
                "#tradeTable tbody tr.tw", "el => el.dataset.id")
        # 初期は order 昇順前提（id=1 が先頭）
        before = first_row_id()
        # Price 列ヘッダをクリックしてソート（昇順）。price は 100,110,102 → 昇順先頭は id=1(100)
        page.click('#tradeTable thead th[data-k="price"]')
        asc = first_row_id()
        # もう一度クリックで降順 → 先頭は id=2(110)
        page.click('#tradeTable thead th[data-k="price"]')
        desc = first_row_id()
        assert asc != desc, f"sort did not reorder: asc={asc} desc={desc}"
        assert asc == "1" and desc == "2", f"asc={asc} desc={desc} before={before}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_row_hover_updates_linkage_hover(tmp_path):
    # 明細行 hover → linkage.hoverTradeId 更新（→ チャートマーカー強調の駆動）
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_detail_tab(page)
        page.hover('#tradeTable tbody tr.tw[data-id="2"]')
        page.wait_for_function("window.__linkage && window.__linkage.hoverTradeId === 2",
                               timeout=4000)
        hid = page.evaluate("window.__linkage.hoverTradeId")
        assert hid == 2
        # 行に hl クラスが付く
        cls = page.eval_on_selector('#tradeTable tbody tr.tw[data-id="2"]',
                                    "el => el.className")
        assert "hl" in cls
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def _bg(page, data_id):
    """該当行の computed backgroundColor を返す（CSS が実適用されているかの観測）。"""
    return page.eval_on_selector(
        f'#tradeTable tbody tr.tw[data-id="{data_id}"]',
        "el => getComputedStyle(el).backgroundColor")


def test_row_hover_applies_visible_highlight_color(tmp_path):
    # 🔴必須(SPEC#2.3): 行 hover で hl が付くだけでなく、computed backgroundColor が
    # hover 前から変化する（= .hl の CSS ルールが実在し画面上で可視）。
    # className のみ検証していた既存テストの弱 assertion を補強する。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_detail_tab(page)
        before = _bg(page, 2)            # hover 前の背景色
        page.hover('#tradeTable tbody tr.tw[data-id="2"]')
        page.wait_for_function("window.__linkage && window.__linkage.hoverTradeId === 2",
                               timeout=4000)
        after = _bg(page, 2)             # hover 後（hl 付与後）の背景色
        assert after != before, (
            f"row hover did not change background (hl CSS missing?): "
            f"before={before!r} after={after!r}")
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_marker_hover_applies_visible_highlight_color(tmp_path):
    # 🔴必須(SPEC#2.3 双方向): マーカー hover→該当行も computed backgroundColor が変化する。
    # chart→linkage→行ハイライトの結線が「画面上で可視」であることを computed-style で観測。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        before = _bg(page, 3)
        page.evaluate("window.__chartEmitMarkerHover && window.__chartEmitMarkerHover(3)")
        page.wait_for_function("window.__linkage && window.__linkage.hoverTradeId === 3",
                               timeout=4000)
        after = _bg(page, 3)
        assert after != before, (
            f"marker hover did not change row background (hl CSS missing?): "
            f"before={before!r} after={after!r}")
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_highlight_survives_column_sort(tmp_path):
    # 🟡A 回帰保護: hover 中に列ソートしても hl が維持される（renderRows が
    # linkage.hoverTradeId を真実源に hl を再付与する）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        _open_detail_tab(page)
        # id=2 を hover した状態でソート列をクリック
        page.hover('#tradeTable tbody tr.tw[data-id="2"]')
        page.wait_for_function("window.__linkage && window.__linkage.hoverTradeId === 2",
                               timeout=4000)
        page.click('#tradeTable thead th[data-k="price"]')  # tbody 全再生成が走る
        # 再生成後も id=2 の行に hl が残っている
        cls = page.eval_on_selector('#tradeTable tbody tr.tw[data-id="2"]',
                                    "el => el.className")
        assert "hl" in cls, f"hl lost after sort: {cls}"
        # computed-style でも可視である
        assert _bg(page, 2) != _bg(page, 1), "highlighted row not visually distinct after sort"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


def test_marker_hover_highlights_detail_row(tmp_path):
    # チャートマーカー hover → 該当行ハイライト。実ブラウザでのマーカー画素 hover は
    # 不安定なため、chart モジュールが onMarkerHover で登録した実コールバック（= linkage.setHover）を
    # __chartEmitMarkerHover 経由で駆動し、chart→linkage→行ハイライトの結線を検証する。
    # （wiring を外すと本テストは fail する＝弱 assertion でないことをミューテーションで確認済）。
    p, browser, page, httpd = _launch(tmp_path)
    try:
        # chart モジュールの登録済みマーカー hover 通知を駆動（id=3 を hover 相当）
        page.evaluate("window.__chartEmitMarkerHover && window.__chartEmitMarkerHover(3)")
        page.wait_for_function("window.__linkage && window.__linkage.hoverTradeId === 3",
                               timeout=4000)
        cls = page.eval_on_selector('#tradeTable tbody tr.tw[data-id="3"]',
                                    "el => el.className")
        assert "hl" in cls, f"row 3 not highlighted on marker hover: {cls}"
    finally:
        browser.close()
        httpd.shutdown()
        p.stop()


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path as _P
        test_detail_table_renders_11_cols_and_row_count(_P(d) / "a")
    print("E2E table OK")
