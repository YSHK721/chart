"""RunFeatures: 1 run の config 由来スイッチの読み取り点（ISSUE-479 Wave2 4-3・O-2）。

固定する仕様:
    1. run 中に効く config 由来のスイッチは、**1 度だけ・1 箇所で**読まれる。
    2. config が当該属性を持たないときに入る既定値は、`BacktestConfig` の宣言から
       導出される（既定値の単一ソースは models.py であり、読み取り側ではない）。

なぜ既定値の単一ソースが要るか:
    移設前は `getattr(config, "floating_pnl_basis", "close")` の形の既定値リテラルが
    2 つのエンジンに散っていた。`models.py` の宣言を変えても読み取り側のリテラルは
    追随せず、しかも**両方のリテラルを同時に直さない限り 2 つのエンジンが違う既定で
    走る**。この食い違いは、両経路が同じ fixture を通らない限り数値に現れない。
    既定を宣言から導出すれば、宣言を変えた瞬間に両エンジンへ同時に効く。
"""
from __future__ import annotations

from dataclasses import MISSING, fields

import pytest

from simulator.usecase.models import BacktestConfig
from simulator.usecase.run_features import RunFeatures


class _ConfigWithout:
    """指定した属性だけを「持たない」config（既定値の入り方を測る）。"""

    def __init__(self, base, missing_names):
        self._base = base
        self._missing = set(missing_names)

    def __getattr__(self, name):
        if name in self._missing:
            raise AttributeError(name)
        return getattr(self._base, name)


def _config():
    return BacktestConfig(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=5,
        legacy_quirks=False,
        return_basis="equity",
    )


#: `BacktestConfig` の宣言から独立に引いた既定値（検定側の第 2 の導出）。
#: 実装と検定が同じ 1 つの宣言を見ていることを、両者を突き合わせて確かめる。
_DECLARED = {
    f.name: (None if f.default is MISSING else f.default)
    for f in fields(BacktestConfig)
}


class TestRunFeaturesReadsTheConfigOnce:
    """run のスイッチが 1 つの値として在ること。"""

    def test_every_feature_takes_the_value_the_config_carries(self):
        # Arrange
        config = _config()
        # Act
        features = RunFeatures.of(config)
        # Assert: 宣言に在る属性はそのまま写される（読み替えをしない）。
        for name in RunFeatures.feature_names():
            assert getattr(features, name) == getattr(config, name), name

    def test_a_feature_the_config_lacks_falls_back_to_the_declared_default(self):
        # Arrange: すべてのスイッチを「持たない」config。
        names = RunFeatures.feature_names()
        config = _ConfigWithout(_config(), names)
        # Act
        features = RunFeatures.of(config)
        # Assert: 入る値は BacktestConfig の宣言が定めた既定である。
        for name in names:
            assert getattr(features, name) == _DECLARED[name], name

    def test_a_config_that_is_absent_entirely_still_yields_the_declared_defaults(self):
        # Arrange / Act: config を持たない run（窓検証等の経路が None を積む）。
        features = RunFeatures.of(None)
        # Assert: 例外を出さず、宣言どおりの既定で埋まる。
        for name in RunFeatures.feature_names():
            assert getattr(features, name) == _DECLARED[name], name


class TestTheDefaultsComeFromTheDeclaration:
    """既定値の単一ソースが models.py の宣言であること。"""

    def test_the_default_of_each_feature_is_derived_from_backtest_config(self):
        # Assert: 実装が持つ既定表と、宣言から独立に引いた表が一致する
        #   （実装側に手書きの既定リテラルが在れば、宣言を変えた瞬間に食い違う）。
        derived = {name: _DECLARED[name] for name in RunFeatures.feature_names()}
        assert RunFeatures.declared_defaults() == derived

    def test_every_feature_names_a_field_that_backtest_config_declares(self):
        declared_fields = {f.name for f in fields(BacktestConfig)}
        assert set(RunFeatures.feature_names()) <= declared_fields

    def test_a_feature_without_a_declared_default_is_treated_as_unset(self):
        """宣言が必須と言う項目（既定を持たない項目）は「未設定」として扱われる。"""
        # Arrange: tick_model は BacktestConfig で既定を持たない必須項目である。
        assert next(f for f in fields(BacktestConfig) if f.name == "tick_model").default is MISSING
        # Act
        features = RunFeatures.of(_ConfigWithout(_config(), {"tick_model"}))
        # Assert
        assert features.tick_model is None


class TestRunFeaturesDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_each_feature_is_read_from_the_config_exactly_once(self):
        # Arrange: 属性アクセスを記録する config。
        reads: "list[str]" = []
        base = _config()

        class _Counting:
            def __getattr__(self, name):
                reads.append(name)
                return getattr(base, name)

        # Act
        features = RunFeatures.of(_Counting())
        # Assert: 発行（config から読んだ回数）− 使用（スイッチの数）= 0。
        used = len(RunFeatures.feature_names())
        assert len(reads) - used == 0
        assert len(set(reads)) - len(reads) == 0  # 同じ項目を二度読まない
        assert features is not None

    def test_the_read_count_is_determined_by_the_feature_count_alone(self):
        """スイッチ 1 つあたり読み取り 1 回であること（オーダーの表明）。

        入力（config の中身）を変えても、読み取り数はスイッチの数だけで決まる。
        """
        measured = {}
        for label, config in (("full", _config()), ("bare", _ConfigWithout(_config(), RunFeatures.feature_names()))):
            reads: "list[str]" = []
            target = config

            class _Counting:
                def __getattr__(self, name):
                    reads.append(name)
                    return getattr(target, name)

            RunFeatures.of(_Counting())
            measured[label] = len(reads)
        assert measured["full"] - measured["bare"] == 0, measured
        assert measured["full"] - len(RunFeatures.feature_names()) == 0, measured

    def test_the_declared_default_table_is_built_once_not_per_call(self):
        """既定表は run ごとに組み直されない（同じ実体が返る）。"""
        assert RunFeatures.declared_defaults() is RunFeatures.declared_defaults()


class TestRunFeaturesIsAValue:
    """スイッチ束は run の途中で書き換わらない値であること。"""

    def test_the_feature_set_cannot_be_mutated_after_it_is_read(self):
        features = RunFeatures.of(_config())
        with pytest.raises(Exception):
            features.pending_lifecycle = True
