"""SymbolSpecCatalog の MT5 突合 bit-exact ゲート（Phase 6 拡張・最重要）.

裁定（憶測禁止）: フォームが供給する JP225 の**結果に効く定数**は MT5 突合 fixture の
case.yaml を唯一のオラクルとする。本ゲートは 2 段:
    (a) **実保証**。SymbolSpecCatalog の JP225 プロファイルの結果に効く定数（contract_size/
        digits/point_size/leverage）が case.yaml と直接等値であり、stops_level==0 であること。
        フォーム供給定数の MT5 一致は本 (a) の等値 assert が担保する。
    (b) **補助（現状 vacuous）**。その定数で build_interactor→実走した結果が case.yaml 由来
        定数の直接実走と bit-exact 一致すること＝定数が run へ流れ非クラッシュ・決定的である
        ことのみ確認する。**本 _oscillating_csv は TC24051901 で trades=0（実測 2026-08-12）**の
        ため定数差分が結果に効かず、(b) は「定数の実感度」を独立には拘束しない（実保証は (a)）。
        意味ある negative control（TC が建玉を出す CSV での定数感度検定）は非 cheap のため ISSUE 化。
volume は結果に効かない（gate-neutral）ため突合対象にしない。build_interactor 既存引数は無改変。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from simulator.main import run_backtest
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"


def _jp225_profile():
    return [p for p in SymbolSpecCatalog().datasets() if p.symbol == "JP225"][0]


def test_catalog_constants_match_mt5_fixture():
    # (a) 結果に効く定数 == case.yaml 権威値
    sym = load_case(_CASE).config["symbol"]
    jp = _jp225_profile()
    assert jp.contract_size == float(sym["contract_size"])
    assert jp.digits == int(sym["digits"])
    assert jp.point_size == float(sym["point_size"])
    assert jp.leverage == float(sym["leverage"])
    assert jp.stops_level == 0
    assert jp.symbol == sym["name"] and jp.period == "M1"


def test_catalog_settlement_currency_matches_mt5_fixture():
    """決済通貨も case.yaml を唯一のオラクルとする（A-2・D-10 の供給源恒久化）。

    出典（実測・憶測禁止）:
        case.yaml の ``symbol.currency``（＝銘柄仕様ブロック・「実 MT5 由来の確定値」）。
        期待値をここにリテラルで書かない（fixture から引く＝単一ソース）。
    """
    sym = load_case(_CASE).config["symbol"]
    assert "currency" in sym, "case.yaml に symbol.currency が無い（オラクル不在）"
    jp = _jp225_profile()
    assert jp.settlement_currency == sym["currency"]


def test_catalog_settlement_currency_agrees_with_report_oracle():
    """最終オラクル（expected/report.json）とも一致することを固定する。

    case.yaml は「人が読むためのメタ要約」（case.yaml 冒頭コメント）であり、数値の最終
    オラクルは report.json 側。report.json の 2 箇所が独立に JPY を示す（実測）:
        - ``settings.currency``（L19）= 実 MT5 テスターの**口座通貨**。
        - ``settings.derived.note``（L29）``0.1lot*10=1 JPY per price unit``
          = **損益が JPY 建てで発生する**＝銘柄の決済（profit）通貨が JPY。
    後者が決済通貨の直接証拠であり、前者との一致は「本 fixture では口座通貨＝決済通貨」
    （N-11 非該当のケース）であることを示す。両方を固定して取り違えを検出する。
    """
    case = load_case(_CASE)
    settings = case.expected["settings"]
    assert settings["currency"] == case.config["symbol"]["currency"]
    # 決済（profit）通貨の直接証拠。ここも fixture から引き、期待値をリテラルで持たない。
    assert f"1 {case.config['symbol']['currency']} per price unit" in settings["derived"]["note"]
    assert _jp225_profile().settlement_currency == settings["currency"]


def _oscillating_csv(path: Path) -> Path:
    # 強い上下動で MADiff ゼロクロスを起こし TC が建玉を出す（contract_size が profit に効く）。
    rows = []
    for i in range(60):
        c = 100.0 + (5.0 if i % 2 == 0 else -5.0) + (i % 7)
        rows.append({
            "time": f"2024-01-01 {i // 60:02d}:{i % 60:02d}:00",
            "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": 100, "spread": 0,
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta_from(profile_like, csv: Path) -> dict:
    p = profile_like
    return dict(
        data_path=str(csv), symbol="JP225", period="M1", ea_name="TC24051901",
        initial_deposit=10000.0,
        contract_size=p["contract_size"], digits=p["digits"], point_size=p["point_size"],
        leverage=p["leverage"], stops_level=p["stops_level"],
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        ma_period=2, ma_method="sma", lot_size=0.1,
        stop_loss_points=50, take_profit_points=100,
    )


def test_catalog_spec_reproduces_fixture_run_bit_exact(tmp_path: Path):
    # (b) カタログ定数で実走 == case.yaml 定数で実走（bit-exact）
    csv = _oscillating_csv(tmp_path / "osc.csv")
    jp = _jp225_profile()
    catalog_spec = {
        "contract_size": jp.contract_size, "digits": jp.digits, "point_size": jp.point_size,
        "leverage": jp.leverage, "stops_level": jp.stops_level,
    }
    sym = load_case(_CASE).config["symbol"]
    fixture_spec = {
        "contract_size": float(sym["contract_size"]), "digits": int(sym["digits"]),
        "point_size": float(sym["point_size"]), "leverage": float(sym["leverage"]),
        "stops_level": 0,
    }

    out_a, out_b = tmp_path / "a", tmp_path / "b"
    out_a.mkdir(); out_b.mkdir()
    code_a, _ = run_backtest(output_dir=out_a, **_meta_from(catalog_spec, csv))
    code_b, _ = run_backtest(output_dir=out_b, **_meta_from(fixture_spec, csv))
    assert code_a == 0 and code_b == 0
    a = (out_a / "stats.json").read_text(encoding="utf-8")
    b = (out_b / "stats.json").read_text(encoding="utf-8")
    assert a == b, "カタログ定数と case.yaml 定数の実走結果が食い違う（定数不一致）"
    # 申し送り（🟡-1・実測 2026-08-12）: 本 _oscillating_csv は TC24051901 で trades=0 を出す
    # （実測）。そのため (b) は「0 建玉どうしの一致」を突合しており、定数を変えても結果が動かない
    # ＝実質 vacuous。意味ある negative control には TC が建玉を出す CSV が要る（非 cheap）＝ISSUE 化。
