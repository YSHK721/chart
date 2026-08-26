"""検出ゲート: 銘柄仕様の**権威**が MT5 レポート（`report.json`）と整合するか。

由来: ISSUE-445 / `.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`。段階 0 で新設（検出ゲートの
新設のみ・値は 1 つも変えない）、段階 3-E2 で突合対象を権威 1 本へ絞った。

**このゲートが解く問題**: 銘柄仕様には「人が書いた値が権威のように振る舞う」経路があった
（RC-1）。MT5 レポートに `contract_size` の記載は一度も無い（実測 2026-08-25）にもかかわらず、
`case.yaml` に人が書いた `contract_size=10`（真値 1.0）が fixture 作成（2026-06-18）から
ライブ実接続（2026-08-25）まで検出されなかった。**本ゲートがあれば fixture 作成時点で
赤になっていた。**

**現在の突合対象は供給元スナップショット**
（`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`・MT5 端末から機械取得）である。
段階 2 で銘柄仕様の権威はここへ移り、段階 3-E2（2026-08-26）で `case.yaml` の `symbol:`
ブロックから重複していた 5 キーを撤去した。よって「申告値」と呼べるものは `case.yaml` に
もう存在しない。`TestSupplierSnapshotAgreesWithTheReport` が権威 ↔ レポートの直接突合を担う。

期待値をテスト側にリテラルで持たない: 判定は
:mod:`simulator.tests.fixtures.mt5.spec_derivation` が `report.json` から機械導出し、
比較相手は供給元スナップショットから引く。両者ともテスト外の単一ソースである。

**`SymbolSpecCatalog` は本ゲートの対象にしない**（重複を作らない）。カタログの定数が供給元
スナップショットと等値であること、およびその定数がレポート導出値と整合することは
`simulator/sim_ui/tests/integration/test_run_options_mt5_gate.py` が既に固定している。

**`case.yaml` について残る検定は 1 件だけ**: `TestCaseYamlHoldsOnlyTheIdentity` が
「`symbol:` に `name` 以外が生え直さない」ことを固定する。値の一致検定はすべて権威側へ
移ったが、この 1 件だけは引き継ぎ先が無い（下のクラス docstring 参照）。

**xfail(strict) について**: ISSUE-445 で確定した**既知の不整合**は現時点で赤になるのが正しい。
CI を緑に保つため `xfail(strict=True)` で固定し、是正が入ると **unexpectedly passing** で
赤に転じて「xfail を外せ」と機械的に知らせる。

    - `case.yaml` の `contract_size`: 段階 0 で xfail 固定 → **段階 2 で緑に転じたため撤去済**
      （2026-08-25）。この機構が設計どおり働いたことの実例である。
    - `report.json` の `settings.derived.contract_size`: 段階 2 で xfail 固定 →
      **段階 3-A で緑に転じたため撤去済**（2026-08-26）。2 例目。

現時点で xfail は 1 件も無い（既知の不整合はすべて解消済み）。
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


# --- レポート内部の整合 ---------------------------------------------------------------


def test_executed_volume_is_single_valued_and_not_the_ea_input(case):
    """実約定ロットは単一値であり、EA 入力 `Lot` とは別物である（RC-2 の証拠）。

    参照実装 `MA_Slope_EA.mq5:NormalizeLot()` が `SYMBOL_VOLUME_MIN` まで持ち上げた結果。
    """
    volumes = sd.executed_volumes(case.expected)
    assert len(volumes) == 1
    assert next(iter(volumes)) != float(case.config["expert"]["lot"])


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


class TestCaseYamlHoldsOnlyTheIdentity:
    """`case.yaml` の `symbol:` が銘柄の**同一性だけ**を持つこと（RC-1 の再発防止）。

    段階 3-E2（2026-08-26）で `point_size` / `digits` / `contract_size` / `leverage` /
    `currency` を撤去し、`name` だけを残した。値の一致を確かめていた検定はすべて
    `TestSupplierSnapshotAgreesWithTheReport`（権威 ↔ レポート）と
    `test_run_options_mt5_gate.py`（カタログ ↔ 供給元・カタログ ↔ レポート導出）へ
    引き継がれた。

    **本検定だけは引き継ぎ先が無い**——「`case.yaml` に権威値が生え直さない」ことを
    見ているテストは他に 1 件も存在しない（実測）。これを失うと、将来誰かが `symbol:` に
    `swap_long:` などを書き足してもどのテストも赤にならず、RC-1（人が書いた値が権威の
    ように振る舞う）が小さく再生する。よって値の突合が消えた後も本クラスは残す。

    `name` は撤去対象外（銘柄の同一性そのものであり、供給元スナップショットを引く鍵）。
    """

    def test_the_block_holds_nothing_beyond_the_identity(self, case):
        """`symbol:` のキーは `name` ただ 1 つ。

        新しいキーが足されたら赤にする。足したい値があるなら供給元スナップショットへ入れる。
        """
        assert set(case.config["symbol"]) == {"name"}


# 注記（2026-08-25 是正）: 当初ここに
# `test_case_yaml_expert_lot_agrees_with_executed_volume` を xfail(strict) で置いたが、これは
# fixture 内の 2 値（`case.yaml` の EA 入力 lot と `report.json` の実約定 volume）を比べるだけで
# **simulator のコードに依存しない**。段階 1 でも緑化し得ず、xfail の reason（「段階 1 で解消
# する」）が誤りだったため撤去した。この事実は
# `test_executed_volume_is_single_valued_and_not_the_ea_input` が緑の検定として保持する。
# simulator 側が参照実装どおり正規化することの検定は、段階 1 の TDD が自前で立てる。
