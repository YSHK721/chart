"""3 本の `NormalizeLot` は同一ではない — 共通化を禁ずる回帰固定（ISSUE-445 段階 3-B）。

**性格**: 特性化テスト（Red 駆動ではない）。個々の分岐は
``test_ma_slope_pending_normalize_lot.py`` / ``test_stop_entry_probe_normalize_lot.py`` が
Red 駆動で固定済みであり、本ファイルは「2 つの原典が**同じ入力に対し異なる出力を返す**」
という関係そのものを 1 か所に固定し、将来の「同じに見えるからまとめる」を赤にする。

出典（実測・各ファイルを読んで確認）:

* ``simulator/tests/fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5:157``
* ``simulator/tests/confirmation/2026-03_ma-limit/ea.mq5:299``
  → 上記 2 本の本体は同一（``step > 0`` のときだけ丸め、``step <= 0`` なら丸めず
  ``digits = 2``。``digits = (int)MathCeil(-MathLog10(step))`` を 0 で下限クランプ）。
* ``simulator/tests/confirmation/2026-04_stop-probe/ea.mq5:159``
  → **別物**。``vstep <= 0`` のとき ``vstep = (vmin > 0) ? vmin : 0.01`` に置換して
  **必ず丸める**（丸めをスキップしない）。``digits`` は
  ``(int)MathMax(0.0, MathCeil(-MathLog10(vstep) - 1e-9))`` と 1e-9 を引く。

適用箇所も異なる（本ファイルの対象外・各テストが固定）:
``2026-03_ma-limit`` は ``PlaceEntry``＝**発注のたび**／``2026-04_stop-probe`` は
``OnInit``＝**起動時 1 回**（かつ ``<=0`` は発注スキップではなく起動失敗）。
"""
from __future__ import annotations

import math

import pandas as pd
import pytest


_PENDING_CONFIG = {
    "slope_shift": 1,
    "slope_min_points": 1.0,
    "point_size": 0.1,
    "digits": 1,
    "stops_level": 0,
    "entry_type": "limit",
    "entry_offset_points": 50.0,
    "stop_loss_points": 200,
    "take_profit_points": 400,
}
_PROBE_CONFIG = {
    "point_size": 0.1,
    "digits": 1,
    "stops_level": 0,
    "entry_offset_points": 100.0,
    "stop_loss_points": 200,
    "take_profit_points": 500,
}


def _pending_volume(**spec) -> float:
    from simulator.adapter.strategy.ma_slope_pending import MaSlopePending

    ind = type(
        "Ind",
        (),
        {
            "_d": {
                "ema": pd.Series([100.0, 100.3, 100.5]),
                "open": pd.Series([100.0, 100.0, 100.0]),
                "spread": pd.Series([50.0, 50.0, 50.0]),
            },
            "get": lambda self, name: self._d[name],
        },
    )()
    strat = MaSlopePending()
    strat.on_init(dict(_PENDING_CONFIG, **spec), ind)
    orders = strat.on_new_bar(2, ind, None)
    assert len(orders) == 1
    return orders[0].volume


def _probe_volume(**spec) -> float:
    from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe

    strat = StopEntryProbe()
    strat.on_init(dict(_PROBE_CONFIG, **spec), None)
    orders = strat.on_tick(0, 52969.8, 52974.8, None)
    assert len(orders) == 2
    return orders[0].volume


def test_non_positive_step_makes_the_two_originals_disagree():
    # Arrange: step=0.0 / min=3.0 / max=0.0 / lot=10.0 — 両原典が食い違う入力。
    #   2026-03_ma-limit : 丸めスキップ → v=10.0 → NormalizeDouble(10.0, 2) = 10.0
    #   2026-04_stop-probe: vstep=vmin=3.0 → MathRound(10/3)*3 = 9.0 → digits=0 → 9.0
    spec = dict(lot_size=10.0, volume_min=3.0, volume_max=0.0, volume_step=0.0)

    # Act
    pending = _pending_volume(**spec)
    probe = _probe_volume(**spec)

    # Assert: 同一入力・異なる出力（＝1 つの実装に畳めない）
    assert pending == pytest.approx(10.0)
    assert probe == pytest.approx(9.0)
    assert pending != probe


def test_digits_formulas_are_not_the_same_function_on_doubles():
    # Arrange: digits 式そのものの差（1e-9 のイプシロンの有無）。両式が一致する入力しか
    # 存在しないなら「digits は共通化してよい」ことになるため、一致しない入力の存在を示す。
    # 実測: math.log10(1e-5) == -5.0 ちょうどであり、依頼文が挙げた step=1e-5 では
    # Python 上は割れない（下の parametrize で確認する）。割れるのは 1.0 の直下の double。
    def pending_digits(step: float) -> int:
        d = int(math.ceil(-math.log10(step)))
        return 0 if d < 0 else d

    def probe_digits(step: float) -> int:
        return int(max(0.0, math.ceil(-math.log10(step) - 1e-9)))

    step = math.nextafter(1.0, 0.0)  # 0.9999999999999999

    # Act / Assert: 同一入力・異なる digits
    assert pending_digits(step) == 1
    assert probe_digits(step) == 0


@pytest.mark.parametrize("step", [1e-5, 1e-3, 0.01, 0.1, 0.5, 1.0, 3.0])
def test_digits_formulas_agree_on_common_steps(step):
    # 上の非同値性を過大解釈しないための限定子（射程の明示）: リポジトリ内で実際に使う
    # step 値では両式は一致する。よって差が観測できるのは病的な double に限られる。
    # 「実運用値では同じ」＝「共通化してよい」ではない（step<=0 の分岐が別物であるため）。
    d_pending = int(math.ceil(-math.log10(step)))
    d_pending = 0 if d_pending < 0 else d_pending
    d_probe = int(max(0.0, math.ceil(-math.log10(step) - 1e-9)))

    assert d_pending == d_probe


def test_true_jp225_spec_makes_both_originals_agree_on_one_lot():
    # 限定子: 供給元 JP225 真値（min=1.0 / step=1.0 / max=10000.0）では両者とも 1.0。
    # 差が出るのは step<=0 の経路だけであり、真値経路の是正内容は共通である。
    spec = dict(lot_size=0.1, volume_min=1.0, volume_max=10000.0, volume_step=1.0)

    assert _pending_volume(**spec) == pytest.approx(1.0)
    assert _probe_volume(**spec) == pytest.approx(1.0)
