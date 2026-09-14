"""`JobSubmission.settings`（Phase 8 スライス 3・spec 第 4 ブロック）の単体検定。

固定する不変条件:
    1. `settings` は既定 ``None``。**不在時の観測挙動は現行と完全同一**（OFF 等価）。
       粒度導出（`effective_granularity`）も現行の `config_overrides.tick_model` 規則の
       ままである。
    2. `settings` があるときは `Model`（`.ini` の生トークン）が粒度の権威になる。
       判定規則そのものは**書き写さない**——`Model` を
       `usecase/tester_settings/enums.TICK_MODEL_ENGINE_IDS` で engine id へ引き、
       現行と同じ 1 つの判定へ合流させる（`real_ticks` または `pending_lifecycle` が
       tick 粒度）。
    3. 期待値は列挙から機械導出する（テスト側に "real_ticks" 等のリテラル表を作らない）。

なぜ粒度が settings 経路で要るか（実測された壊れ方の防止）: トレーリングの
`granularity` は run の実効粒度と一致しないと B2/B4 のどちらでも発火せず**無音で
不作動**になる（`submit_job._reject_trailing_granularity_mismatch`）。settings 経路の
`Model` を見ないと、`Model=4`（real ticks）の run に granularity="bar" が素通りする。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.usecase.job_models import JobSubmission
from simulator.usecase.tester_settings import TICK_MODEL_ENGINE_IDS, TickModel

# 期待値の単一ソース: tick 経路になる engine id は `real_ticks` の 1 つ（run_backtest の
# 分岐に忠実）。列挙から引いて、テスト側に文字列を書き写さない。
_REAL_TICKS_ID = TICK_MODEL_ENGINE_IDS[TickModel.REAL_TICKS]


def _settings(model: TickModel) -> dict:
    """`Model` の生トークン（`.ini` の値＝列挙値の 10 進表記）を持つ settings ブロック。"""
    return {"tester": {"Model": str(int(model))}, "inputs": []}


# --- 1. settings 不在＝現行と完全同一 ---------------------------------------

def test_settingsは既定でNoneである() -> None:
    # Arrange / Act
    sub = JobSubmission(backtest={"ea_name": "X"})
    # Assert
    assert sub.settings is None


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "bar"),
        ({"tick_model": "every_tick"}, "bar"),
        ({"tick_model": _REAL_TICKS_ID}, "tick"),
        ({"pending_lifecycle": True}, "tick"),
    ],
)
def test_settings不在の粒度は現行規則のまま(overrides: dict, expected: str) -> None:
    # Arrange
    sub = JobSubmission(backtest={"ea_name": "X", "config_overrides": overrides})
    # Act
    got = sub.effective_granularity
    # Assert
    assert got == expected


# --- 2. settings 有り＝`Model` が粒度の権威 ---------------------------------

@pytest.mark.parametrize("model", list(TickModel))
def test_settingsのModelがengine_idを経て粒度を決める(model: TickModel) -> None:
    """期待値は `TICK_MODEL_ENGINE_IDS` から導く（判定の書き写しを作らない）。"""
    # Arrange
    expected = "tick" if TICK_MODEL_ENGINE_IDS[model] == _REAL_TICKS_ID else "bar"
    sub = JobSubmission(backtest={"ea_name": "X"}, settings=_settings(model))
    # Act
    got = sub.effective_granularity
    # Assert
    assert got == expected


def test_settingsのModelはbacktestのtick_modelより優先する() -> None:
    """`.ini` の `Model` が権威（写像層 `_config_overrides` と同じ優先順位）。"""
    # Arrange: backtest 側は real_ticks（tick）、settings 側は m1 ohlc（bar）
    sub = JobSubmission(
        backtest={"ea_name": "X", "config_overrides": {"tick_model": _REAL_TICKS_ID}},
        settings=_settings(TickModel.ONE_MINUTE_OHLC),
    )
    # Act
    got = sub.effective_granularity
    # Assert
    assert got == "bar"


def test_settings経路でもpending_lifecycleはtick粒度にする() -> None:
    """判定は 1 つ（合流）である。settings 経路だけ別規則にしない。"""
    # Arrange
    sub = JobSubmission(
        backtest={"ea_name": "X", "config_overrides": {"pending_lifecycle": True}},
        settings=_settings(TickModel.ONE_MINUTE_OHLC),
    )
    # Act
    got = sub.effective_granularity
    # Assert
    assert got == "tick"


def test_Modelを持たないsettingsは現行規則へ落ちる() -> None:
    """`Model` は検証層の必須キーであり、受付検証を通れば必ず存在する。

    それでも欠落時に例外にしないのは、本 property が**検証前**にも呼ばれ得る DTO の
    読み取りだからである（規則違反の報告は検証層の rule_id 付き例外が担う）。
    """
    # Arrange
    sub = JobSubmission(
        backtest={"ea_name": "X", "config_overrides": {"tick_model": _REAL_TICKS_ID}},
        settings={"tester": {}, "inputs": []},
    )
    # Act
    got = sub.effective_granularity
    # Assert
    assert got == "tick"
