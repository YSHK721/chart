"""web 骨格 E2E 検証（Playwright ヘッドレス・詳細設計 §8.4・試作 verify.py 同型）。

F-1 スコープ: ローソク足チャート＋下部マルチビュー枠＋サマリーカード＋区間(IS/OOS)切替が
report.json を消費して描画されることを最小検証する。no-cache static 配信で起動する。

pytest から `test_web_skeleton_renders` を実行（マーカー e2e）。chromium 不在環境では skip。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from e2e import _harness  # noqa: E402  (同一ディレクトリの共有ハーネス)

WEB = Path(__file__).resolve().parents[1].parent / "web"
DATA = WEB / "data" / "report.json"

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    return _harness.free_port()


def _serve(directory: str, port: int):
    return _harness.serve(directory, port)


def _ensure_minimal_report():
    """report.json が無ければ最小ダミーを書く（E2E は描画結線のみ検証・実 run 非依存）。"""
    if DATA.exists():
        return
    DATA.parent.mkdir(parents=True, exist_ok=True)
    seg = {
        "label": "IS",
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "bars": 2, "trades": 1, "period": "2026.04.01-04.14"},
        "report": {},
        "bars": [
            {"time": 1000, "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0},
            {"time": 1060, "open": 105.0, "high": 112.0, "low": 102.0, "close": 108.0},
        ],
        "trades": [{"id": 1, "side": "buy", "entry_time": 1000, "exit_time": 1060,
                    "entry_price": 100.0, "exit_price": 108.0, "profit": 8.0,
                    "volume": "0.1", "sl": "98.0", "tp": "112.0", "order": 1,
                    "comment": "tp", "balance": 10008.0, "hold_sec": 60,
                    "mfe": 1.2, "mae": 0.5}],
        "orders": [],
        "agg": {"balance_curve": [{"time": 1060, "value": 10008.0}], "heat": []},
    }
    payload = {
        "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
                 "initial_deposit": 10000.0, "split": "2026-04-15"},
        "segments": {"is": seg, "oos": seg},
        "summary": {
            "is": {"trades": 1, "net": 8.0, "final_balance": 10008.0, "win_rate": 100.0,
                   "profit_factor": None, "expectancy": 8.0, "payoff": None,
                   "return_pct": 0.08, "max_dd_pct": 0.0},
            "oos": {"trades": 1, "net": 8.0, "final_balance": 10008.0, "win_rate": 100.0,
                    "profit_factor": None, "expectancy": 8.0, "payoff": None,
                    "return_pct": 0.08, "max_dd_pct": 0.0},
        },
        "degradation": {},
        "verdict": {"result": "pass", "reasons": ["OOSでも優位性を維持"]},
        "_contract_notes": [],
    }
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def test_web_skeleton_renders():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright 未導入")

    _ensure_minimal_report()
    port = _free_port()
    httpd = _serve(str(WEB), port)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                pytest.skip("chromium 未導入")
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/index.html")
            # DATA ロード完了（main.js が window.__READY を立てる）まで待機
            page.wait_for_function("window.__READY === true", timeout=8000)

            # ローソク足チャートのコンテナ存在
            assert page.query_selector("#price-chart") is not None
            # 下部マルチビュー枠の存在（試作準拠で #multiview→#bottom へ改称）
            assert page.query_selector("#bottom") is not None
            # 点17: 最上部サマリーカード（#summary-cards）は試作に無い → 削除済（完全準拠）
            assert page.query_selector("#summary-cards") is None
            # 点15: 区間トグルボタン（select 廃止 → .segbtn）が存在（IS/OOS）
            assert page.query_selector('.segbtn[data-seg="is"]') is not None
            assert page.query_selector('.segbtn[data-seg="oos"]') is not None

            # JS エラーが出ていない
            assert errors == [], f"page errors: {errors}"
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    test_web_skeleton_renders()
    print("E2E OK")
