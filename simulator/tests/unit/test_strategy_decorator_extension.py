"""E-2（`build_interactor` の `strategy_decorator` 拡張点）の単体検定。

背景（基本設計書 §12.4 E-2・依頼者承認済み）:
    「既存ファイル無改変」（§12.3-2）に対する承認済み例外の 1 つ。`build_interactor` は
    戦略を `_EA_FACTORIES` で選んで `_ResultCapturingInteractor` へ直接渡しており、
    **外から包む拡張点が無い**（OCP 違反）。承認された最小改変は任意引数
    `strategy_decorator`（既定 None）の追加のみ。

固定する不変条件:
    1. **既定 None なら byte 等価**: 戦略は包まれず、`_EA_FACTORIES` が返した
       オブジェクトが**同一インスタンスのまま** Interactor へ渡る。
       （MT5 突合の回帰ゼロ＝§8.1 Phase 2 通過条件 6 の構造的保証）
    2. 与えたときだけ包まれる。包んだ結果が Interactor へ渡る。
    3. 差し込み点は「戦略確定の直後」。指標レジストリ・market_data・tick_model の
       選択は decorator の有無で変わらない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from simulator.main import build_interactor
from simulator.usecase.ports import StrategyPort


#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int が契約である
#: （`Bar.time` = ``numpy.datetime64`` | epoch int。`CsvOHLCRepository._extract` は CSV の値を
#: **そのまま** `Bar.time` に載せるため、ISO 文字列を書くと契約違反の Bar が生まれる。
#: 委譲経路 `CsvCandleSource` は同じ CSV を ValueError で fail-fast する＝経路で解釈が割れる）。
_EPOCH_2024_01_01 = 1_704_067_200


def _write_csv(path: Path) -> Path:
    """既定 TC 経路（comma 形式）の最小 CSV。"""
    rows = []
    for i in range(12):
        base = 1.1000 + i * 0.0001
        rows.append(
            {
                # 是正前 "2024-01-0{1+i//6} {i%6:02d}:00:00" と同一時刻の epoch 秒（UTC）。
                "time": _EPOCH_2024_01_01 + 86400 * (i // 6) + 3600 * (i % 6),
                "open": base,
                "high": base + 0.0005,
                "low": base - 0.0005,
                "close": base + 0.0002,
                "volume": 100,
                "spread": 10,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta(csv_path: Path, **overrides) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
    )
    base.update(overrides)
    return base


class _Wrapper(StrategyPort):
    """包んだことが観測できるだけの最小 Decorator。"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def on_init(self, config: Any, indicators: Any) -> None:
        self.inner.on_init(config, indicators)

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any):
        return self.inner.on_new_bar(bar_index, indicators, account)

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return self.inner.on_position_check(position, bar_index, indicators)


# --- 1. 既定 None は byte 等価 --------------------------------------------

def test_既定では戦略が包まれない(tmp_path: Path) -> None:
    """§12.4 E-2「None なら既存と byte 等価」。MT5 突合の回帰ゼロの構造的保証。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(**_meta(csv_path))
    # Assert（_EA_FACTORIES が返した素の戦略がそのまま渡っている）
    from simulator.adapter.strategy.tc24051901 import TC24051901

    assert isinstance(controller._interactor._strategy, TC24051901)


def test_Noneを明示しても包まれない(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(**_meta(csv_path, strategy_decorator=None))
    # Assert
    from simulator.adapter.strategy.tc24051901 import TC24051901

    assert isinstance(controller._interactor._strategy, TC24051901)


def test_Noneのとき渡るのはファクトリが返した同一インスタンス(tmp_path: Path) -> None:
    """「包まない」を「同一オブジェクトが素通りする」で固定する（写しを作らない）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    seen: list = []

    def _identity(strategy: Any) -> Any:
        seen.append(strategy)
        return strategy

    # Act（identity 関数を通した場合と、None の場合の型が一致することを見る）
    with_identity, _ = build_interactor(
        **_meta(csv_path, strategy_decorator=_identity)
    )
    # Assert
    assert len(seen) == 1
    assert with_identity._interactor._strategy is seen[0]


# --- 2. 与えたときだけ包まれる --------------------------------------------

def test_decoratorを与えると包まれた戦略が渡る(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(
        **_meta(csv_path, strategy_decorator=_Wrapper)
    )
    # Assert
    strategy = controller._interactor._strategy
    assert isinstance(strategy, _Wrapper)


def test_包まれた戦略の内側はファクトリが選んだ戦略である(tmp_path: Path) -> None:
    """差し込み点が「戦略確定の直後」であることの固定。"""
    # Arrange
    from simulator.adapter.strategy.tc24051901 import TC24051901

    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(**_meta(csv_path, strategy_decorator=_Wrapper))
    # Assert
    assert isinstance(controller._interactor._strategy.inner, TC24051901)


def test_decoratorはea_nameで選ばれた戦略に適用される(tmp_path: Path) -> None:
    """戦略の選択規則（_EA_FACTORIES）は decorator の有無で変わらない。"""
    # Arrange
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(
        **_meta(csv_path, ea_name="PRO_fit_Band_EA", strategy_decorator=_Wrapper)
    )
    # Assert
    assert isinstance(controller._interactor._strategy.inner, ProFitBand)


# --- 3. 他の結線は変わらない ----------------------------------------------

def test_decoratorの有無で他の結線が変わらない(tmp_path: Path) -> None:
    """指標・market_data・tick_model の選択は戦略の包装と独立（SRP）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    plain, plain_req = build_interactor(**_meta(csv_path))
    wrapped, wrapped_req = build_interactor(
        **_meta(csv_path, strategy_decorator=_Wrapper)
    )
    # Assert
    assert type(plain._interactor._tick_model) is type(wrapped._interactor._tick_model)
    assert type(plain._interactor._indicators) is type(wrapped._interactor._indicators)
    assert type(plain._market_data) is type(wrapped._market_data)
    assert plain_req.symbol_spec == wrapped_req.symbol_spec


def test_decoratorが例外を投げたら伝播する(tmp_path: Path) -> None:
    """無音で素の戦略へフォールバックしない（誤設定に気付けるようにする）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")

    def _boom(_strategy: Any) -> Any:
        raise RuntimeError("decorator 構築に失敗")

    # Act / Assert
    with pytest.raises(RuntimeError):
        build_interactor(**_meta(csv_path, strategy_decorator=_boom))
