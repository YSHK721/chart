"""sizing_config_loader（サイジング設定の framework 境界）の単体検定。

固定する不変条件（基本設計書 §12.1・§12.6・CLEAN_ARCH §7）:
    1. **既定 OFF**（§12.1）。設定を一切書かなければ `enabled=False`＝既存挙動と byte 等価。
    2. 未知キーは `extra="forbid"` で**拒否**する（既存 `config_loader.py:39` と同じ規律）。
       silent drop による既定値化は「設定したつもりで効いていない」を生む。
    3. usecase へは**プレーン DTO**（`SizingConfig` dataclass）で返す。pydantic 型を
       usecase 層へ漏らさない（`config_loader.py` の docstring と同じ規約）。
    4. **戦略ごとの指定を持たない**（§12.1「戦略リストのハードコード禁止」）。
       戦略名のキーを与えたら未知キーとして拒否される。
    5. シードは設定項目であり既定は固定値（§12.6 決定性）。
"""
from __future__ import annotations

import dataclasses

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.framework.sizing_config_loader import load_sizing_config
from simulator.usecase.sizing_models import SizingConfig


# --- 1. 既定 OFF -----------------------------------------------------------

def test_設定なしなら既定でOFF() -> None:
    """§12.1: 既定 OFF＝既存挙動と byte 等価。"""
    # Arrange / Act
    got = load_sizing_config({})
    # Assert
    assert got.enabled is False


def test_明示的にONにできる() -> None:
    got = load_sizing_config({"enabled": True})
    assert got.enabled is True


# --- 2. 未知キーの拒否 -----------------------------------------------------

def test_未知キーは拒否する() -> None:
    """silent drop は「設定したつもりで効いていない」を生む（config_loader.py:36 と同じ規律）。"""
    with pytest.raises(ConfigError):
        load_sizing_config({"enabld": True})


def test_戦略名のキーは未知キーとして拒否する() -> None:
    """§12.1: 適用可否は設定のみで決まり、戦略ごとの指定は仕様として存在しない。"""
    with pytest.raises(ConfigError):
        load_sizing_config({"enabled": True, "weekly_vol_band": False})


def test_戦略リストのキーは未知キーとして拒否する() -> None:
    with pytest.raises(ConfigError):
        load_sizing_config({"enabled": True, "strategies": ["MA_Slope_EA"]})


# --- 3. プレーン DTO -------------------------------------------------------

def test_返り値はプレーンなdataclassである() -> None:
    """pydantic 型を usecase 層へ漏らさない。"""
    # Arrange / Act
    got = load_sizing_config({"enabled": True})
    # Assert
    assert isinstance(got, SizingConfig)
    assert dataclasses.is_dataclass(got)
    assert not hasattr(got, "model_dump")   # pydantic BaseModel ではない


def test_dtoは不変である() -> None:
    got = load_sizing_config({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.enabled = True  # type: ignore[misc]


# --- 4. 値の受け渡しと検証 -------------------------------------------------

def test_エッジのパラメータが渡る() -> None:
    # Arrange / Act
    got = load_sizing_config(
        {
            "enabled": True,
            "win_rate": 0.55,
            "payoff_ratio": 1.2,
            "ruin_level": 0.4,
            "alpha": 0.05,
            "horizon": 52,
            "split_count": 10,
            "seed": 7,
            "sims": 500,
            "margin_rate": 0.2,
            "point_value": 2.0,
        }
    )
    # Assert
    assert (got.win_rate, got.payoff_ratio, got.ruin_level) == (0.55, 1.2, 0.4)
    assert (got.alpha, got.horizon, got.split_count) == (0.05, 52, 10)
    assert (got.seed, got.sims) == (7, 500)
    assert (got.margin_rate, got.point_value) == (0.2, 2.0)


def test_シードの既定は固定値() -> None:
    """§12.6: 乱数はシードを設定項目化（既定は固定値）。"""
    assert load_sizing_config({}).seed == 1


def test_EdgeRuinSpecへ変換できる() -> None:
    # Arrange
    config = load_sizing_config({"enabled": True, "win_rate": 0.55, "seed": 7})
    # Act
    spec = config.to_edge_spec()
    # Assert
    assert spec.win_rate == 0.55
    assert spec.seed == 7


@pytest.mark.parametrize(
    "key, value",
    [
        ("win_rate", -0.1),
        ("win_rate", 1.5),
        ("payoff_ratio", 0.0),
        ("ruin_level", 0.0),
        ("ruin_level", 1.5),
        ("alpha", 1.5),
        ("horizon", 0),
        ("split_count", 0),
        ("sims", 0),
        ("margin_rate", 0.0),
        ("margin_rate", 1.0),
        ("point_value", 0.0),
    ],
)
def test_範囲外の値は拒否する(key: str, value: float) -> None:
    """無音で既定値へ倒さない（誤設定に気付けるようにする）。"""
    with pytest.raises(ConfigError):
        load_sizing_config({"enabled": True, key: value})


def test_型違いは拒否する() -> None:
    with pytest.raises(ConfigError):
        load_sizing_config({"enabled": "yes-ish", "win_rate": "たくさん"})


def test_マッピング以外の入力は拒否する() -> None:
    with pytest.raises(ConfigError):
        load_sizing_config(["enabled"])  # type: ignore[arg-type]
