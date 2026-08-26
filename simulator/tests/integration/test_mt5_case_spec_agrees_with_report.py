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
    - `report.json` の `settings.derived.contract_size`: 段階 2 で xfail 固定 →
      **段階 3-A で緑に転じたため撤去済**（2026-08-26）。2 例目。

現時点で xfail は 1 件も無い（既知の不整合はすべて解消済み）。

**供給元スナップショットの突合（段階 3-E 準備・2026-08-26 追加）**: 段階 2 で銘柄仕様の権威は
`marketdata/symbol_specs/…/JP225.json` へ移ったが、**その権威そのものを MT5 レポートと
突き合わせるゲートは無かった**（実測: 従来の突合対象は `case.yaml` のみ。スナップショットは
reconcile の実走を通じて間接的に裏付けられるだけだった）。下の
`TestSupplierSnapshotAgreesWithTheReport` がその直接の突合を担う。あわせて
`TestCaseYamlStillMirrorsTheSupplier` が「重複が残っている間は必ず一致する」ことを固定し、
段階 3-E（`case.yaml` の `symbol:` ブロック撤去）が**挙動に影響しない**ことを機械的に保証する。
"""
from __future__ import annotations

import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    load_settlement_currency,
    load_spec_fields,
)
from simulator.tests.fixtures.mt5 import load_case
from simulator.tests.fixtures.mt5 import spec_derivation as sd

_CASE = "ma_slope_jp225_202501"
_SYMBOL = "JP225"


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


def test_report_derived_contract_size_agrees_with_the_report_itself(case):
    """`settings.derived` の申告値をレポート本体（deals）と突き合わせる。

    `settings.derived` は**レポートが出力した値ではない**（xlsx `Settings` の 8 項目に
    `contract_size` の行は存在しない・実測）。人が付けた注釈が `report.json` に同居している。

    **段階 3-A で緑に転じた（2026-08-26）**: 本検定は段階 2 で `xfail(strict=True)` として
    置かれ、`settings.derived.contract_size=10` を赤として検出していた。段階 3-A で値を
    1.0 へ是正した（実出力部 `results` / `deals` / `deals_count` / `source` は sha256 で
    不変を確認済み・変更は `derived` の 2 行のみ）。XPASS(strict) がマーカー撤去を促した。

    なお `settings.derived` と `report.json` の同居自体が SRP 違反である（実 MT5 出力と
    人の注釈が 1 ファイルに混ざる）。ブロックの**撤去**は所在の変更であり別裁定。
    """
    derived = case.expected["settings"]["derived"]["contract_size"]
    report = sd.contract_size_consistency(case.expected, float(derived))
    assert report.ok, report.describe()


# --- 供給元スナップショット（＝現在の権威）との突合 -----------------------------------


@pytest.fixture(scope="module")
def supplier():
    """銘柄仕様の**現在の権威**（MT5 端末から機械取得したスナップショット）。"""
    return load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


class TestSupplierSnapshotAgreesWithTheReport:
    """権威そのものを MT5 レポートの機械導出値と突き合わせる。

    段階 2 以降、実走が使う銘柄仕様はすべてこのスナップショットである。従来の突合対象は
    `case.yaml`（人が読むための転記）だけであり、**権威側が MT5 出力と食い違っても直接
    赤にする検定は無かった**。将来 OANDA が仕様を改定してスナップショットを取り直した
    ときに、その改定が golden と矛盾するなら本クラスが赤で知らせる（設計書 §8 の意図）。
    """

    def test_contract_size_agrees_with_the_report(self, case, supplier):
        report = sd.contract_size_consistency(case.expected, supplier["contract_size"])
        assert report.ok, report.describe()

    def test_leverage_agrees_with_the_report(self, case, supplier):
        assert supplier["leverage"] == sd.account_leverage(case.expected)

    def test_settlement_currency_agrees_with_the_report(self, case):
        assert load_settlement_currency(
            OANDA_JAPAN_MT5_LIVE, _SYMBOL
        ) == sd.settlement_currency(case.expected)

    def test_digits_covers_the_observed_price_decimals(self, case, supplier):
        # 片側検査（全価格が整数なら観測桁は過小評価される・設計書 §8）。
        assert supplier["digits"] >= sd.price_decimals(case.expected)

    def test_the_executed_volume_is_valid_on_the_supplier_volume_grid(
        self, case, supplier
    ):
        """実約定ロットが供給元の volume 格子（min / max / step）に載ること。

        RC-2 の逆側の裏付けである。`volume_min=1.0` は「EA 入力 0.1 が持ち上げられた」と
        いう主張の根拠であり、その主張は**実約定 volume が 1.0 だったこと**と噛み合って
        いなければならない。ここが食い違うなら、スナップショットか導出のどちらかが誤り。
        """
        for volume in sd.executed_volumes(case.expected):
            assert supplier["volume_min"] <= volume <= supplier["volume_max"]
            ratio = volume / supplier["volume_step"]
            assert ratio == pytest.approx(round(ratio))


class TestCaseYamlStillMirrorsTheSupplier:
    """`case.yaml` の `symbol:` ブロックが権威の忠実な転記であること（段階 3-E の前提）。

    このブロックは段階 2 以降「人が読むための転記」であり、実走はここを見ない。しかし
    **転記が残っている間は乖離し得る**——それを許すと RC-1（人が書いた値が権威のように
    振る舞う）が小さく再生する。ここで一致を固定しておけば、段階 3-E の撤去は
    「同じ値を 2 箇所に持つのをやめる」だけになり、挙動に影響しないことが機械的に言える。

    `name` は対象外（銘柄の同一性そのものであり、供給元を引くための鍵）。
    """

    #: `case.yaml` の `symbol:` キー → 権威側の同じ値を引く 8 フィールド名。
    #: 撤去時はこの表がそのまま「消す対象」の一覧になる。
    _MIRRORED_NUMERIC = ("point_size", "digits", "contract_size", "leverage")
    #: 通貨は `SymbolSpec` の 8 フィールドに含まれないため別に引く。
    _MIRRORED = _MIRRORED_NUMERIC + ("currency",)

    def test_every_mirrored_number_matches_the_supplier(self, case, supplier):
        declared = case.config["symbol"]
        mismatched = [
            key
            for key in self._MIRRORED_NUMERIC
            if float(declared[key]) != float(supplier[key])
        ]
        assert not mismatched, (
            f"case.yaml の symbol.{mismatched} が供給元スナップショットと食い違う"
        )

    def test_the_declared_currency_matches_the_supplier(self, case):
        assert case.config["symbol"]["currency"] == load_settlement_currency(
            OANDA_JAPAN_MT5_LIVE, _SYMBOL
        )

    def test_the_block_holds_nothing_beyond_the_identity_and_the_mirror(self, case):
        """転記以外の値がここに増えていないこと（増えたら権威が二重化する）。

        新しいキーが足されたら赤にする。足したい値があるなら供給元へ入れる（RC-1 の再発防止）。
        """
        assert set(case.config["symbol"]) == {"name"} | set(self._MIRRORED)


# 注記（2026-08-25 是正）: 当初ここに
# `test_case_yaml_expert_lot_agrees_with_executed_volume` を xfail(strict) で置いたが、これは
# fixture 内の 2 値（`case.yaml` の EA 入力 lot と `report.json` の実約定 volume）を比べるだけで
# **simulator のコードに依存しない**。段階 1 でも緑化し得ず、xfail の reason（「段階 1 で解消
# する」）が誤りだったため撤去した。この事実は
# `test_executed_volume_is_single_valued_and_not_the_ea_input` が緑の検定として保持する。
# simulator 側が参照実装どおり正規化することの検定は、段階 1 の TDD が自前で立てる。
