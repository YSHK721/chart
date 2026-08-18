"""`simulator.main` の EA 公開アクセサの契約（ISSUE-405）。

固定する仕様:

    1. `build_ea_strategy(**spec) -> StrategyPort`
       `build_ea_indicators` と**同じジョブ仕様・同じ選択規則**で、3 点組のうち戦略を返す。
    2. **`tick_model` を要求しない**（`config_overrides` は任意）。両アクセサの問いは
       「その EA はどの系列／戦略を持つか」であり run の modelling に依存しない。要求すると
       呼出側が値を捏造することになる。
    3. `known_ea_names() -> tuple[str, ...]`
       実行可能な EA 名（登録表のキー＋既定 TC 経路の名前）。決定的順・重複なし。
    4. 2 つのアクセサは**同じ構築**から取られる（別々に factory を選び直さない）。

なぜ公開が要るか（実測・ISSUE-405）:
    これが無いと外側スライス（`sim_ui/adapter`）が私有名（`_EA_FACTORIES` /
    `_factory_tc24051901` / `_EaBuildContext`）を越境 import し、選択規則
    `_EA_FACTORIES.get(ea_name, _factory_tc24051901)` を**書き写す**。実際に 3 ファイルが
    そうなっており、うち 1 件は `getattr(sim_main, "_EA_FACTORIES", {})` の文字列形式で
    AST 検定を素通りしていた。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simulator.main import (
    DEFAULT_EA_NAME,
    build_ea_indicators,
    build_ea_strategy,
    known_ea_names,
)

_ROWS = 12


def _comma_csv(path: Path) -> Path:
    head = "time,open,high,low,close,volume\n"
    rows = "".join(
        f"2024-01-{i + 1:02d}T00:00:00,{100 + i},{101 + i},{99 + i},{100 + i},1\n"
        for i in range(_ROWS)
    )
    path.write_text(head + rows, encoding="utf-8")
    return path


def _mt5_tsv(path: Path) -> Path:
    head = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
    rows = "".join(
        f"2024.01.{i + 1:02d}\t00:00:00\t{100 + i}\t{101 + i}\t{99 + i}\t{100 + i}\t1\t0\t2\n"
        for i in range(_ROWS)
    )
    path.write_text(head + rows, encoding="utf-8")
    return path


@pytest.fixture()
def csv_path(tmp_path: Path) -> Path:
    return _comma_csv(tmp_path / "probe.csv")


def _spec(csv: Path, ea_name: str, **overrides) -> dict:
    """`build_interactor` と同じジョブ仕様のうち、EA 構築に要る最小の部分。"""
    spec = {
        "data_path": csv,
        "ea_name": ea_name,
        "ma_period": 2,
        "ma_method": "sma",
        "adx_period": 2,
    }
    spec.update(overrides)
    return spec


# --- 1. 戦略アクセサ -----------------------------------------------------------


def test_the_strategy_accessor_returns_a_strategy_port(csv_path: Path) -> None:
    """engine が呼ぶ 3 点（StrategyPort の実体）を持つこと。"""
    strategy = build_ea_strategy(**_spec(csv_path, DEFAULT_EA_NAME))
    for hook in ("on_init", "on_new_bar", "on_position_check"):
        assert callable(getattr(strategy, hook, None)), hook


def test_the_strategy_accessor_follows_the_registry(csv_path: Path) -> None:
    """登録 EA ごとに、その EA の戦略実体が返る（表を写さずに到達できる）。"""
    from simulator.adapter.strategy.pro_fit_band import ProFitBand
    from simulator.adapter.strategy.tc24051901 import TC24051901
    from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand

    assert isinstance(build_ea_strategy(**_spec(csv_path, "PRO_fit_Band_EA")), ProFitBand)
    assert isinstance(
        build_ea_strategy(**_spec(csv_path, "WeeklyVolBand_EA")), WeeklyVolBand
    )
    assert isinstance(build_ea_strategy(**_spec(csv_path, DEFAULT_EA_NAME)), TC24051901)


def test_an_unregistered_name_falls_back_to_the_default_tc_path(csv_path: Path) -> None:
    """フォールバック規則は `_select_ea_factory` の 1 箇所にあり、公開側は委譲するだけ。"""
    from simulator.adapter.strategy.tc24051901 import TC24051901

    assert isinstance(build_ea_strategy(**_spec(csv_path, "No_Such_EA")), TC24051901)


def test_the_accessors_do_not_require_a_tick_model(csv_path: Path) -> None:
    """`config_overrides` を渡さなくても呼べる（値を捏造させない）。

    既定は config_loader の既定（``every_tick``＝バー系列を消費する）に落ちるため、
    従来の表引きと同じ factory が選ばれる。
    """
    import inspect

    for accessor in (build_ea_strategy, build_ea_indicators):
        params = inspect.signature(accessor).parameters
        assert "tick_model" not in params
    # 実際に渡さずに構築できる
    assert build_ea_strategy(**_spec(csv_path, "PRO_fit_Band_EA")) is not None


def test_a_data_less_model_selects_the_data_less_construction(csv_path: Path) -> None:
    """バー系列を消費しない modelling では戦略も Null 実装になる（規則は 1 箇所のまま）。"""
    strategy = build_ea_strategy(
        **_spec(
            csv_path,
            "PRO_fit_Band_EA",
            config_overrides={"tick_model": "math_calculations"},
            data_path=None,
        )
    )
    assert strategy.__class__.__name__ == "NullStrategy"


def test_both_accessors_come_from_one_construction(csv_path: Path) -> None:
    """同じ spec なら、戦略と指標は**同じ factory**が作った 3 点組から取られる。

    別々に規則を判定していれば、data-less の指定で片方だけ Null に落ちて食い違う。
    """
    spec = _spec(
        csv_path, "PRO_fit_Band_EA", config_overrides={"tick_model": "math_calculations"},
        data_path=None,
    )
    assert build_ea_strategy(**spec).__class__.__name__ == "NullStrategy"
    assert build_ea_indicators(**spec).__class__.__name__ == "NullIndicatorRegistry"


def test_extra_job_spec_keys_are_tolerated(csv_path: Path) -> None:
    """`**spec` で丸ごと渡せること（`build_interactor` と同じジョブ仕様）。"""
    spec = _spec(csv_path, DEFAULT_EA_NAME)
    spec.update(symbol="JP225", period="M1", lot_size=0.1, initial_deposit=10_000.0)
    assert build_ea_strategy(**spec) is not None


# --- 2. EA 名の列挙 ------------------------------------------------------------


def test_the_default_fallback_name_is_executable() -> None:
    """既定 TC 経路の名前が一覧に載る（表の外側にある唯一の実行可能名）。

    「一覧 ⊇ 登録表のキー」という関係は、表を所有する側の検定
    （`tests/unit/test_unsupported_n01_ea_name_source.py`）が固定する。本ファイルは
    私有名を読まない（越境参照ゲートの対象を増やさない）。
    """
    assert DEFAULT_EA_NAME in known_ea_names()


def test_known_ea_names_is_deterministic() -> None:
    names = known_ea_names()
    assert isinstance(names, tuple)
    assert names != ()
    assert list(names) == sorted(set(names))
    assert known_ea_names() == names


def test_every_known_ea_name_resolves_to_a_strategy() -> None:
    """列挙した名前がすべて**戦略まで到達する**こと（列挙と構築が食い違わない）。

    データ形式（comma / MT5 タブ）の違いは構築規則ではないので、両形式を順に試す
    （`sim_ui` の `EaBuildProbe` と同じ扱い）。1 件も構築できずに空振りしないよう、
    到達件数が一覧の件数と一致することまで測る。
    """
    resolved = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        candidates = (_comma_csv(root / "probe.csv"), _mt5_tsv(root / "probe.mt5.csv"))
        for ea_name in known_ea_names():
            for csv in candidates:
                try:
                    strategy = build_ea_strategy(**_spec(csv, ea_name))
                except Exception:
                    continue
                resolved.append(ea_name)
                assert strategy is not None, ea_name
                break
    assert resolved == list(known_ea_names())
