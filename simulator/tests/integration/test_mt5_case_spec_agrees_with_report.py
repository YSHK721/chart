"""検出ゲート: `case.yaml` の銘柄仕様が MT5 レポート（`report.json`）と整合するか。

由来: ISSUE-445 / `.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md` **段階 0**（検出ゲートの新設のみ・
値は 1 つも変えない・追加のみ）。

**このゲートが解く問題**: `case.yaml` の `symbol:` ブロックは「銘柄仕様 (実 MT5 由来の確定値)」
と名乗るが、MT5 レポートに `contract_size` の記載は一度も無い（実測 2026-08-25）。人が書いた
値を供給元と突き合わせる機構が無かったため、`contract_size=10`（真値 1.0）が fixture 作成
（2026-06-18）からライブ実接続（2026-08-25）まで検出されなかった。**本ゲートがあれば
fixture 作成時点で赤になっていた。**

期待値をテスト側にリテラルで持たない: 判定は
:mod:`simulator.tests.fixtures.mt5.spec_derivation` が `report.json` から機械導出し、
申告値は `case.yaml` から引く。両者ともテスト外の単一ソースである。

**`SymbolSpecCatalog` は本ゲートの対象にしない**（重複を作らない）。カタログの結果に効く
定数が `case.yaml` と等値であることは
`simulator/sim_ui/tests/integration/test_run_options_mt5_gate.py::test_catalog_constants_match_mt5_fixture`
が既に固定しており、本ゲートの判定は推移的にカタログへ及ぶ。

**xfail(strict) について**: ISSUE-445 で確定した**既知の不整合**は現時点で赤になるのが正しい。
CI を緑に保つため `xfail(strict=True)` で固定し、是正が入ると **unexpectedly passing** で
赤に転じて「xfail を外せ」と機械的に知らせる。

    - `case.yaml` の `contract_size`: 段階 0 で xfail 固定 → **段階 2 で緑に転じたため撤去済**
      （2026-08-25）。この機構が設計どおり働いたことの実例である。
    - `report.json` の `settings.derived.contract_size`: 段階 3 で解消（下記）。
"""
from __future__ import annotations

import pytest

from simulator.tests.fixtures.mt5 import load_case
from simulator.tests.fixtures.mt5 import spec_derivation as sd

_CASE = "ma_slope_jp225_202501"


@pytest.fixture(scope="module")
def case():
    return load_case(_CASE)


# --- 導出そのものの健全性（ゲートが機能することの証明）-------------------------------


def test_report_yields_closed_trades_to_derive_from(case):
    """導出の母数が存在する（deals が対に復元でき、判定材料がある）。"""
    trades = sd.closed_trades(case.expected)
    assert len(trades) == int(case.expected["results"]["total_trades"])


def test_derived_contract_size_is_one(case):
    """レポートから導出される `contract_size` は 1.0 である（ISSUE-445 の確定事実）。

    判定は片側検査（全決済 deal が丸め許容内に収まるか）。参考値の中央値も 1.0 に一致する。
    """
    report = sd.contract_size_consistency(case.expected, 1.0)
    assert report.ok, report.describe()
    assert sd.contract_size_estimate(case.expected) == pytest.approx(1.0)


def test_gate_rejects_a_wrong_contract_size(case):
    """**負の対照**: 誤った値は必ず棄却される（落ちないゲートは無価値であるため固定する）。"""
    assert not sd.contract_size_consistency(case.expected, 2.0).ok
    assert not sd.contract_size_consistency(case.expected, 0.5).ok


# --- 申告値との突合（現時点で緑）-----------------------------------------------------


def test_executed_volume_is_single_valued_and_not_the_ea_input(case):
    """実約定ロットは単一値であり、EA 入力 `Lot` とは別物である（RC-2 の証拠）。

    参照実装 `MA_Slope_EA.mq5:NormalizeLot()` が `SYMBOL_VOLUME_MIN` まで持ち上げた結果。
    """
    volumes = sd.executed_volumes(case.expected)
    assert len(volumes) == 1
    assert next(iter(volumes)) != float(case.config["expert"]["lot"])


def test_case_yaml_digits_covers_observed_price_decimals(case):
    """申告 `digits` は観測された価格の小数桁を下回らない（片側検査）。

    全価格がたまたま整数のときに観測桁は過小評価されるため、等値ではなく片側で検査する。
    """
    assert int(case.config["symbol"]["digits"]) >= sd.price_decimals(case.expected)


def test_case_yaml_leverage_agrees_with_report(case):
    """`case.yaml` の `leverage` はレポートの口座レバレッジと一致する。

    注記（ISSUE-445 §3.4）: `leverage` は**口座属性**であり銘柄仕様ではない
    （`mt5.symbol_info` に `leverage` は無い・実測）。所在の是正は段階 3。
    """
    assert float(case.config["symbol"]["leverage"]) == sd.account_leverage(case.expected)


def test_case_yaml_currency_agrees_with_report(case):
    assert case.config["symbol"]["currency"] == sd.settlement_currency(case.expected)


def test_case_yaml_contract_size_agrees_with_report(case):
    """`case.yaml` の `contract_size` はレポートと整合する。

    **段階 2 で緑に転じた（2026-08-25）**: 本検定は段階 0 で `xfail(strict=True)` として
    置かれ、`contract_size: 10` を赤として検出していた。段階 2 で供給元スナップショット
    （`trade_contract_size=1.0`）へ権威を移し `case.yaml` の値を 1.0 へ是正した結果、
    XPASS(strict) で「xfail を外せ」と機械的に知らされたためマーカーを撤去した。
    """
    report = sd.contract_size_consistency(
        case.expected, float(case.config["symbol"]["contract_size"])
    )
    assert report.ok, report.describe()


# --- 既知の不整合（段階 3 で解消する。解消したら xfail を外す）------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ISSUE-445: report.json の settings.derived.contract_size=10 は人が後から書いた"
        "逆算値であり、同じ report.json の deals と整合しない（真値 1.0）。report.json 自体は"
        "実 MT5 テスターの確定出力＝golden オラクルのため本段階では触らない。是正は段階 3。"
    ),
)
def test_report_derived_contract_size_agrees_with_the_report_itself(case):
    """`settings.derived` の申告値をレポート本体（deals）と突き合わせる。

    `settings.derived` は**レポートが出力した値ではない**（xlsx `Settings` の 8 項目に
    `contract_size` の行は存在しない・実測）。人が付けた注釈が `report.json` に同居して
    いるため、その誤りが `case.yaml` 是正後も**見えたまま追跡される**よう本検定を置く。
    段階 3 で `settings.derived` を是正すると XPASS(strict) で撤去を促す。
    """
    derived = case.expected["settings"]["derived"]["contract_size"]
    report = sd.contract_size_consistency(case.expected, float(derived))
    assert report.ok, report.describe()


# 注記（2026-08-25 是正）: 当初ここに
# `test_case_yaml_expert_lot_agrees_with_executed_volume` を xfail(strict) で置いたが、これは
# fixture 内の 2 値（`case.yaml` の EA 入力 lot と `report.json` の実約定 volume）を比べるだけで
# **simulator のコードに依存しない**。段階 1 でも緑化し得ず、xfail の reason（「段階 1 で解消
# する」）が誤りだったため撤去した。この事実は
# `test_executed_volume_is_single_valued_and_not_the_ea_input` が緑の検定として保持する。
# simulator 側が参照実装どおり正規化することの検定は、段階 1 の TDD が自前で立てる。
