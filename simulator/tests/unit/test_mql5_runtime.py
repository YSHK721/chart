"""``simulator.adapter.strategy.mql5_runtime`` の検定（ISSUE-445 段階 3-B 後始末）。

本モジュールは MQL5 の言語・実行環境の意味論のみを所有する。ここで固定するのは
**移送前の 3 戦略（``ma_slope`` / ``ma_slope_pending`` / ``stop_entry_probe``）に
手書き複製されていた private 実装と 1 ビットも違わないこと**である。移送前の実装は
以下（3 ファイルで AST 完全一致・実測）::

    @staticmethod
    def _math_round(x):
        magnitude = math.floor(abs(x))
        if abs(x) - magnitude >= 0.5:
            magnitude += 1.0
        return math.copysign(magnitude, x)

    @classmethod
    def _normalize_double(cls, value, digits):
        scale = 10.0 ** digits
        return cls._math_round(value * scale) / scale

    @staticmethod
    def _spec_value(cfg, key):
        try:
            return float(cfg[key])
        except KeyError:
            return 0.0

境界値の選定根拠（TDD references/test-design-patterns.md §TD.2 境界値分析）:
``math_round`` は「小数部 0.5」が同値クラスの境界であり、Python 組込み ``round``
（銀行家丸め）と食い違う唯一の点である。``normalize_double`` は 2 進で厳密表現できる
入力（1.25 / 2.5）を選び、丸め規則そのものを浮動小数誤差から分離して測る。
"""
from __future__ import annotations

import math

import pytest

from simulator.adapter.strategy.mql5_runtime import (
    math_round,
    normalize_double,
    spec_value,
)


# --- math_round（MQL5 MathRound = 0.5 をゼロから遠ざける丸め）--------------------

@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0.0, 0.0),
        (0.4, 0.0),
        (0.5, 1.0),          # 境界: 0.5 は切り上げ（組込み round は 0 を返す）
        (0.6, 1.0),
        (1.5, 2.0),
        (2.5, 3.0),          # 境界: 組込み round は 2 を返す（銀行家丸め）
        (2.4, 2.0),
        (-0.4, 0.0),
        (-0.5, -1.0),        # 境界: 負側もゼロから遠ざかる
        (-1.5, -2.0),
        (-2.5, -3.0),
        (10.0, 10.0),
        (3.3333333333333335, 3.0),  # 10 / 3 — NormalizeLot の step 丸めで実際に通る値
    ],
)
def test_math_round_is_half_away_from_zero(x, expected):
    # Arrange / Act
    got = math_round(x)

    # Assert: 値と符号（-0.0 と 0.0 を区別しない比較にしない）
    assert got == pytest.approx(expected)


@pytest.mark.parametrize("x", [0.5, 2.5, -0.5, -2.5])
def test_math_round_diverges_from_builtin_round_on_the_half_boundary(x):
    # 「組込み round を使わない」という設計判断を非空虚にする対照。
    # 実測: round(0.5)==0 / round(2.5)==2（銀行家丸め）。
    assert math_round(x) != float(round(x))


def test_math_round_preserves_sign_of_negative_zero_result():
    # 特性化: 原典は math.copysign を使うため -0.4 → -0.0 を返す（値は 0.0 と等しい）。
    got = math_round(-0.4)

    assert got == 0.0
    assert math.copysign(1.0, got) == -1.0


# --- normalize_double（MQL5 NormalizeDouble）------------------------------------

@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (1.25, 1, 1.3),      # 12.5 は 2 進厳密 → 0.5 切り上げ
        (1.35, 1, 1.4),      # 13.5 は 2 進厳密 → 0.5 切り上げ
        (2.5, 0, 3.0),
        (-2.5, 0, -3.0),
        (10.0, 2, 10.0),
        (9.0, 0, 9.0),
        (0.1, 2, 0.1),
        (1.0, 2, 1.0),
    ],
)
def test_normalize_double_rounds_to_digits_half_away_from_zero(value, digits, expected):
    assert normalize_double(value, digits) == pytest.approx(expected)


def test_normalize_double_removes_binary_representation_error():
    # NormalizeLot が呼ぶ本来の目的（step 積で出た誤差の除去）。
    # 実測: 0.1 * 3 == 0.30000000000000004
    assert 0.1 * 3 != 0.3
    assert normalize_double(0.1 * 3, 2) == pytest.approx(0.3)


# --- spec_value（MQL5 SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_*) 相当）-----------

def test_spec_value_returns_supplied_value_as_float():
    assert spec_value({"volume_step": 1}, "volume_step") == 1.0
    assert isinstance(spec_value({"volume_step": 1}, "volume_step"), float)


def test_spec_value_falls_back_to_zero_when_key_is_absent():
    # 後方互換の中核: キー未供給は 0.0＝「制約なし」。原典 NormalizeLot の非正値分岐に載る。
    assert spec_value({}, "volume_step") == 0.0


def test_spec_value_falls_back_to_zero_for_run_config_subscript_key_error():
    # RunConfig.__getitem__ は欠落キーで KeyError を送出する（main/run_config.py:55）。
    # spec_value はそれを 0.0 に翻訳する（非対称。docstring に記録済み）。
    class _RunConfigLike:
        def __getitem__(self, key):
            raise KeyError(key)

    assert spec_value(_RunConfigLike(), "volume_min") == 0.0


def test_spec_value_does_not_swallow_non_key_errors():
    # 射程の限定子: 握り潰すのは KeyError のみ。型不正等は loud に失敗する。
    class _Broken:
        def __getitem__(self, key):
            return object()

    with pytest.raises(TypeError):
        spec_value(_Broken(), "volume_min")
