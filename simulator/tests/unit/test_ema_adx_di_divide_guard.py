"""ISSUE-399: ``ema_adx_di`` の除算が 0 除算を踏まないことを固定する（A-7 と同型）。

``np.where(cond, a / b, fallback)`` は **分岐の前に両辺を評価する**ため、``b`` に 0 を
含むと選ばれない側の ``0/0`` を必ず計算する。値は ``where`` が fallback を選ぶため
正しいが、``np.errstate(divide="ignore", invalid="ignore")`` で囲うのは
**除算が走っている事実（症状）の抑制**であって原因の除去ではない。

本件が例外系でない根拠（実測・ISSUE-399）:
    ``compute_adx_with_di`` は warmup として先頭バー i=0 の ``+DM/−DM/TR`` を 0 で
    始める（前足が無いため・``profit_adx_needle.compute_adx`` と同一）。したがって
    ``tr[0] == 0.0`` は **入力によらず常に成立**する。さらに ``sdi[0] == 0.0`` から
    ``pdi[0] == mdi[0] == 0.0``、すなわち ``denom[0] == 0.0`` も常に成立する。
    ゆえに 3 箇所の除算は「フラット系列でのみ」ではなく **全呼び出しで毎回**
    ``0/0`` を実行する。A-7（``metrics_spec``）と同じく正常系の欠陥である。

通過条件は「``errstate`` を外しても警告 0 件」＝除算そのものが実行されないこと。
``errstate`` による抑制は本テストを通せない（``_errstate_neutralized`` が無効化する）。

TDD AAA 構造。F.I.R.S.T。
"""
from __future__ import annotations

import contextlib
import re
import warnings
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from simulator.adapter.indicator import ema_adx_di
from simulator.adapter.indicator.ema_adx_di import _ema, compute_adx_with_di


@contextlib.contextmanager
def _errstate_neutralized():
    """``np.errstate`` を無害化し、被測定コードによる警告抑制を効かなくする。

    症状抑制（``errstate``）と原因除去（``np.divide(..., where=)``）を測定上
    区別するための仕掛け。是正後は production に ``errstate`` が無いため
    本 CM は no-op となり、テストは素の実行を測る。
    """
    with mock.patch.object(np, "errstate", lambda **_kw: contextlib.nullcontext()):
        yield


def _runtime_warnings(fn):
    """``fn()`` を errstate 抑制なしで実行し、RuntimeWarning のみを返す（値も併せて返す）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # numpy は 'warn' のときにのみ Python warning を出す。errstate を無害化している間は
        # CM で設定できないため seterr を使うが、グローバル状態のため必ず元に戻す
        # （復元を怠ると under='warn' 等が他テストへ漏れて偽陽性を生む）。
        previous = np.seterr(all="warn")
        try:
            with _errstate_neutralized():
                value = fn()
        finally:
            np.seterr(**previous)
    return value, [
        f"{w.category.__name__}: {w.message} ({w.filename}:{w.lineno})"
        for w in caught
        if issubclass(w.category, RuntimeWarning)
    ]


#: 是正の等価性・警告 0 件を測る入力ケース（ISSUE-399 完了条件 4）。
_CASES: dict[str, tuple[list[float], list[float], list[float]]] = {
    # 通常系列（既存 test_ema_adx_di.py と同一の 8 本）。
    "normal": (
        [10.0, 11.0, 12.0, 11.5, 13.0, 12.0, 14.0, 13.5],
        [9.0, 9.5, 10.0, 10.5, 11.0, 11.0, 12.0, 12.5],
        [9.5, 10.5, 11.5, 11.0, 12.5, 11.5, 13.5, 13.0],
    ),
    # フラット系列（全バー TR=0 ＝ 全要素で 0 除算が走る）。
    "flat": ([100.0] * 6, [100.0] * 6, [100.0] * 6),
    # NaN 混在系列（NaN 伝播が是正前後で不変であること）。
    "nan": (
        [10.0, float("nan"), 12.0, 11.5, 13.0],
        [9.0, 9.5, float("nan"), 10.5, 11.0],
        [9.5, 10.5, 11.5, float("nan"), 12.5],
    ),
    # 長さ 1（差分配列が空・TR=[0.0] のみ）。
    "len1": ([10.0], [9.0], [9.5]),
}


def _series(case: str):
    high, low, close = _CASES[case]
    return pd.Series(high), pd.Series(low), pd.Series(close)


def _legacy_where_formula(high, low, close, period: int):
    """是正前の式（``np.where`` 内で除算する形）を再現する等価性オラクル。

    差分は除算の書き方のみに限定する。EMA は production の ``_ema`` を用いる
    （是正対象外の部分を手書き複製しないため）。
    """
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = h.size
    pdm = np.zeros(n, dtype=np.float64)
    mdm = np.zeros(n, dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)

    up = h[1:] - h[:-1]
    dn = lo[:-1] - lo[1:]
    p = np.where(up < 0.0, 0.0, up)
    m = np.where(dn < 0.0, 0.0, dn)
    eq = p == m
    p_lt = p < m
    pdm[1:] = np.where(eq, 0.0, np.where(p_lt, 0.0, p))
    mdm[1:] = np.where(eq, 0.0, np.where(p_lt, m, 0.0))

    hl = np.abs(h[1:] - lo[1:])
    hc = np.abs(h[1:] - c[:-1])
    lc = np.abs(lo[1:] - c[:-1])
    tr[1:] = np.maximum(np.maximum(hl, hc), lc)

    with np.errstate(divide="ignore", invalid="ignore"):
        sdi_plus = np.where(tr > 0.0, 100.0 * pdm / tr, 0.0)
        sdi_minus = np.where(tr > 0.0, 100.0 * mdm / tr, 0.0)

    pdi = _ema(sdi_plus, period)
    mdi = _ema(sdi_minus, period)

    denom = pdi + mdi
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(denom != 0.0, 100.0 * np.abs(pdi - mdi) / denom, 0.0)
    adx = _ema(dx, period)
    return adx, pdi, mdi


# --- 除算そのものが実行されないこと（errstate による抑制では通らない）-----------

class TestDivisionIsNotExecuted:
    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_no_runtime_warning_without_errstate(self, case):
        # Arrange
        high, low, close = _series(case)

        # Act
        _v, caught = _runtime_warnings(
            lambda: compute_adx_with_di(high, low, close, period=4)
        )

        # Assert: errstate を無効化しても警告 0 件＝除算が実行されていない
        assert caught == [], f"[{case}] 0 除算が実行された: {caught}"

    def test_warmup_bar_makes_zero_denominator_unconditional(self):
        """本件が例外系でない実証: TR[0]・denom[0] は入力によらず 0。"""
        # Arrange / Act
        high, low, close = _series("normal")
        _adx, pdi, mdi = compute_adx_with_di(high, low, close, period=4)

        # Assert: 先頭バーは warmup で DM/TR=0 → SDI=0 → +DI=−DI=0 → denom=0
        assert pdi.iloc[0] == 0.0
        assert mdi.iloc[0] == 0.0
        assert pdi.iloc[0] + mdi.iloc[0] == 0.0

    def test_empty_input_still_raises_value_error(self):
        """長さ 0 は除算に到達せず ValueError（是正で契約を変えない）。"""
        empty = pd.Series([], dtype=float)
        with pytest.raises(ValueError):
            compute_adx_with_di(empty, empty, empty, period=4)


# --- 指標値が是正前と bit 一致であること（絶対制約）-----------------------------

class TestBitIdenticalToLegacyFormula:
    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_outputs_are_bit_identical(self, case):
        # Arrange
        high, low, close = _CASES[case]
        exp_adx, exp_pdi, exp_mdi = _legacy_where_formula(high, low, close, period=4)

        # Act
        adx, pdi, mdi = compute_adx_with_di(
            pd.Series(high), pd.Series(low), pd.Series(close), period=4
        )

        # Assert: bit パターン一致（NaN も含めて完全同一）＋ array_equal（NaN 許容）
        for name, got, expected in (
            ("adx", adx, exp_adx),
            ("plus_di", pdi, exp_pdi),
            ("minus_di", mdi, exp_mdi),
        ):
            got_arr = np.asarray(got, dtype=np.float64)
            assert got_arr.tobytes() == expected.tobytes(), f"[{case}] {name} が bit 不一致"
            assert np.array_equal(got_arr, expected, equal_nan=True), f"[{case}] {name} 不一致"

    @pytest.mark.parametrize("period", [1, 2, 4, 8, 14])
    def test_bit_identical_across_periods(self, period):
        """SPEC §3.5 の ADX_Period=8 を含む複数 period で bit 一致。"""
        high, low, close = _CASES["normal"]
        exp = _legacy_where_formula(high, low, close, period)
        got = compute_adx_with_di(
            pd.Series(high), pd.Series(low), pd.Series(close), period=period
        )
        for got_s, expected in zip(got, exp):
            assert np.asarray(got_s, dtype=np.float64).tobytes() == expected.tobytes()


# --- 同型の欠陥（np.where 内の除算 / errstate による抑制）が残っていないこと -------

class TestNoSuppressionRemains:
    def test_module_source_has_no_where_wrapped_division(self):
        import inspect

        offenders = []
        for line in inspect.getsource(ema_adx_di).splitlines():
            code = re.sub(r"#.*$", "", line)  # コメントは対象外（説明文を誤検出しない）
            if "np.where(" in code and "/" in code.split("np.where(", 1)[1]:
                offenders.append(line.strip())
        assert offenders == [], f"np.where 内の除算が残存: {offenders}"

    def test_module_source_has_no_errstate_suppression(self):
        """``errstate`` は症状抑制。原因除去後は不要であることを固定する。"""
        import inspect

        source = inspect.getsource(ema_adx_di)
        offenders = [
            line.strip()
            for line in source.splitlines()
            if "np.errstate(" in re.sub(r"#.*$", "", line)
        ]
        assert offenders == [], f"errstate による抑制が残存: {offenders}"
