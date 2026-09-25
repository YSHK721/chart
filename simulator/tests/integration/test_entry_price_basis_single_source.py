"""建値基準の出所を戦略ただ 1 つにする（ISSUE-533 段階 2・ISSUE-525 の構造的解消）。

段階 1（`ab7f3226`）は判定の瞬間の**権威**を戦略の宣言へ移したが、設定からの供給経路は
受け口として残っていた。残った供給は 2 つの害を生む:

    1. 経路（settings の有無）とデータ実体（気配幅を供給するか）が建値基準を決めうる
       ——ISSUE-525 が「同じデータで経路により約定式が変わる」と記した構図そのもの。
    2. 気配幅を供給する実体には ``current_open`` が載るため、**終値で判定する EA を
       その実体へ投げると run が始まらない**（宣言と設定の食い違いで Fail-Stop）。

本ファイルが固定するのは「**経路もデータ実体も建値基準を決めない**」ことである。

固定する不変条件:
    T1  同じ足・同じ戦略なら、経路（settings あり／なし）とデータ実体（気配幅を供給する
        ／しない）の 4 通りで**約定価格が一致する**。
    T2  設定に建値基準を書いた投入は**明示的に拒まれる**（黙って効く経路を残さない）。
    T3  気配幅を供給する実体へ終値で判定する EA を投げても run が始まる。
    T4  サイジングの推定建値系列は**戦略の宣言**から決まる（宣言の違う 2 戦略で 2 点）。

計算量（絶対命令 2026-08-28）は
`simulator/tests/unit/test_entry_price_basis_supply_complexity.py` が持つ。

数え方（空振り防止）: どの検定も「両方が同じ理由で失敗したので一致した」を通さないため、
約定価格が**足の終値そのもの**であることを併せて表明する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.main import build_interactor, run_backtest
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.sim_ui.adapter import symbol_spec_catalog
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.ohlc_header_fixtures import MD6, MD9
from simulator.tests.sizing_declaration_fixtures import (
    DeclaringStrategy,
    VolumeConstraints,
    ask_series,
)
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_NAME,
    engine_binding,
    runnable_settings,
)

#: 2024-01-01T00:00:00Z。marketdata 形式の 「`date`」 列は naive 文字列で時刻系は UTC である。
_EPOCH_2024_01_01 = 1_704_067_200
#: 合成する足の本数（実測 2026-09-25: 20 本で 9 件・40 本で 19 件の取引が出る）。
_BARS = 40
#: 始値を固定して終値だけを動かす（**どちらで約定したかが約定価格 1 つで判別できる**）。
_OPEN = 40000.0
#: 終値の並び。既定 EA（MADiff ゼロクロス）の判定は
#: ``MA(close) − MA(open)`` の符号反転で起きるため、上げ 2 本・下げ 2 本を繰り返し、
#: **上げ幅と下げ幅を違える**（同じ幅にすると移動平均がちょうど 0 を通り、
#: 「prev < 0 かつ curr > 0」の狭義不等式が 1 度も成立しない）。
_CLOSE_PATTERN = (_OPEN + 30.0, _OPEN + 30.0, _OPEN - 50.0, _OPEN - 50.0)


def _rows(count: int) -> "list[tuple]":
    """始値を固定し終値だけを動かした合成足。気配幅は 0（実体差を作らない）。

    気配幅を 0 に固定するのは、2 つの実体（気配幅の列を持つ／持たない）が**同じ足**に
    なるようにするためである。列の有無だけが違う 2 実体で約定価格が一致することが、
    「実体が建値基準を決めない」の表明になる。
    """
    rows = []
    for index in range(count):
        open_ = _OPEN
        close = _CLOSE_PATTERN[index % len(_CLOSE_PATTERN)]
        rows.append(
            (
                _EPOCH_2024_01_01 + 60 * index,
                open_,
                max(open_, close) + 1.0,
                min(open_, close) - 1.0,
                close,
                1.0,
                0,
            )
        )
    return rows


def _write(path: Path, *, with_spread: bool) -> Path:
    """気配幅の列を持つ／持たない 2 実体を**同じ足**から書き出す。

    2 実体の形式は本番と同じ marketdata 形式（6 列 / 9 列）である。列の有無以外は
    1 バイトも変えない——測りたいのは「実体が建値基準を決めないこと」であって、
    形式差ではない。
    """
    from datetime import datetime, timezone

    header = MD9 if with_spread else MD6
    lines = [header]
    for time, open_, high, low, close, volume, spread in _rows(_BARS):
        stamp = datetime.fromtimestamp(time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        values = [stamp, f"{open_}", f"{high}", f"{low}", f"{close}", f"{volume}"]
        if with_spread:
            values.extend(["0", "0", str(spread)])
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def entities(tmp_path, monkeypatch) -> "dict[str, Path]":
    """カタログの 2 実体を合成データへ差し替える（既定のデータ木は 1 バイトも読まない）。"""
    spreadless = _write(tmp_path / "spreadless.csv", with_spread=False)
    with_spread = _write(tmp_path / "with_spread.csv", with_spread=True)
    monkeypatch.setattr(symbol_spec_catalog, "_JP225_DATA_CSV", spreadless)
    monkeypatch.setattr(symbol_spec_catalog, "_JP225_SPREAD_DATA_CSV", with_spread)
    return {"spreadless": spreadless, "with_spread": with_spread}


def _profile_overrides(data_path: Path) -> "dict | None":
    """カタログが当該実体について供給する決定論設定（front はこれを素通しする）。"""
    for profile in build_run_options_port().datasets():
        if Path(profile.data_path) == data_path:
            return profile.config_overrides
    raise AssertionError(f"カタログが {data_path} を提供していない（差し替えの空振り）")


def _settings():
    """保証境界の内側にある設定（期間は全区間＝`Dates=0`）。"""
    return runnable_settings(Dates="0", Expert=f"{DEFAULT_EA_NAME}.ex5")


def _binding(data_path: Path, overrides: "dict | None"):
    return engine_binding(data_path=str(data_path), config_overrides=overrides)


def _entry_prices_via_settings(data_path: Path, overrides: "dict | None") -> "list[float]":
    """settings 経路（`.ini` → 写像層 → エンジン）で実行し、約定価格を並べて返す。"""
    exit_code, result, _meta = run_from_settings(_settings(), _binding(data_path, overrides))
    assert exit_code == 0, f"settings 経路が exit={exit_code} で始まらなかった"
    assert result is not None
    return [trade.entry_price for trade in result.trades]


def _entry_prices_direct(
    tmp_path: Path, data_path: Path, overrides: "dict | None"
) -> "list[float]":
    """現行経路（settings 不在＝ 「`backtest`」 ブロックを素通し）で実行する。

    投入引数は settings 経路と**同一のものを使う**（銘柄仕様・期間・EA 入力を手で写すと、
    2 経路の差ではなく写し間違いを測ることになる）。差し替えるのは ``config_overrides``
    だけであり、現行経路が実際に渡すもの——カタログの供給そのまま——に置く。

    「``tick_model``」 と 「``stop_out_action``」 は settings 経路が権威として供給すると宣言
    している 2 項目（「`kwargs_mapper._config_overrides`」）であり、本検定の対象ではない。
    揃えておかないと modelling と証拠金の分岐が経路ごとに変わり、建値基準以外の理由で
    約定価格が動く。**建値基準はここで揃えない**——それが測りたい唯一の量である。
    """
    kwargs = dict(to_interactor_kwargs(_settings(), _binding(data_path, overrides)))
    supplied = kwargs["config_overrides"]
    kwargs["config_overrides"] = {
        **(overrides or {}),
        "tick_model": supplied["tick_model"],
        "stop_out_action": supplied["stop_out_action"],
    }
    exit_code, result = run_backtest(output_dir=tmp_path / "direct", **kwargs)
    assert exit_code == 0, f"現行経路が exit={exit_code} で始まらなかった"
    assert result is not None
    return [trade.entry_price for trade in result.trades]


# --- T1 / T3: 経路もデータ実体も建値基準を決めない --------------------------------


def test_the_route_and_the_entity_do_not_decide_the_entry_price(tmp_path, entities):
    """settings あり／なし × 気配幅あり／なしの 4 通りで約定価格が一致する。

    既定 EA（「`TC24051901`」）は**当該足の終値**で判定する（`entry_price_basis = "close"`）。
    したがって 4 通りすべてで約定価格は足の終値と一致していなければならない。

    是正前（段階 1 の残存）: 気配幅を供給する実体にはカタログが ``current_open`` を載せる
    ため、宣言（終値）と食い違って `build_interactor` が Fail-Stop する＝**run が始まらない**
    （T3 が固定する残存リスクそのもの）。
    """
    # Arrange
    closes = {close for *_, close, _volume, _spread in _rows(_BARS)}

    # Act
    measured = {}
    for name, path in entities.items():
        overrides = _profile_overrides(path)
        measured[(name, "settings")] = _entry_prices_via_settings(path, overrides)
        measured[(name, "direct")] = _entry_prices_direct(
            tmp_path / name, path, overrides
        )

    # Assert
    reference = measured[("spreadless", "settings")]
    assert reference, "取引が 1 件も出ておらず比較にならない（素材の空振り）"
    for key, prices in measured.items():
        assert prices == reference, f"{key} だけ約定価格が違う"
    # 空振り防止: 同じ理由で失敗して一致したのではなく、宣言（終値）どおりに約定している。
    assert set(reference) <= closes


def test_a_close_deciding_ea_runs_on_the_entity_that_supplies_spread(tmp_path, entities):
    """気配幅を供給する実体へ終値で判定する EA を投げても run が始まる（残存リスク）。

    是正前は `build_interactor` が 「`EntryPriceBasisConflictError`」 を送出し、投入は
    exit=2 で終わっていた（カタログが ``current_open`` を載せるため）。
    """
    # Arrange
    path = entities["with_spread"]
    overrides = _profile_overrides(path)

    # Act
    exit_code, result, _meta = run_from_settings(_settings(), _binding(path, overrides))

    # Assert
    assert exit_code == 0
    assert result is not None and result.trades


# --- T2: 設定からの供給は明示的に拒まれる ----------------------------------------


def test_an_explicit_entry_price_basis_in_the_config_is_refused(tmp_path, entities):
    """設定に建値基準を書いた投入は受け付けない（黙って効く経路を残さない）。

    「無視する」ではなく「拒む」を選ぶ理由: 無視すると、書いた人は自分の指定が効いたと
    思ったまま結果を読む。効かない指定を黙って受けるのは沈黙であり、沈黙は検出できない。
    """
    # Arrange
    kwargs = dict(
        to_interactor_kwargs(
            _settings(), _binding(entities["spreadless"], {"entry_price_basis": "close"})
        )
    )

    # Act / Assert
    with pytest.raises(ConfigError):
        build_interactor(**kwargs)


# --- T4: サイジングの推定建値系列は戦略の宣言から決まる ----------------------------


def test_the_estimated_entry_series_follows_the_strategy_declaration():
    """宣言の違う 2 戦略を同じ工場で包むと、要求される系列が宣言どおりに分かれる。

    是正前は工場の引数（run の設定由来）が系列を決めていたため、**同じ工場で包んだ
    2 戦略は必ず同じ系列**になった（実測 2026-09-25・本作業ツリー: 工場へ "close" を
    渡すと 2 戦略とも "close"、"current_open" を渡すと 2 戦略とも "open"）。宣言が
    権威なら、工場が 1 つでも宣言の数だけ分かれる。
    """
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    # Arrange
    factory = build_sizing_decorator(
        SizingConfig(enabled=True, sims=8, seed=1), symbol_spec=VolumeConstraints()
    )

    # Act
    asked = (
        ask_series(factory(DeclaringStrategy("close"))),
        ask_series(factory(DeclaringStrategy("current_open"))),
    )

    # Assert
    assert asked == ("close", "open")
