"""保証境界の適用可否を経路が決めないこと（ISSUE-525・合流点への適用）。

何を解くか（実測 2026-09-25・実 UI・稼働サーバ）:
    同じフォームの既定値で、`GET /sim/settings-schema` が取れれば **N-17 で拒否**（exit 2）、
    取れなければ**受理されて 46 秒以上走り続けた**。保証境界を適用する場所が写像層
    （「`effective_to_interactor_kwargs`」）の 1 箇所しか無く、写像層を通らない投入経路
    （`settings` ブロック不在）が宣言の外へ出られたためである。

    是正前の実測（2026-09-25・本作業ツリー・HEAD ed17a928。数え方: `write_marketdata_csv` が書く
    合成 marketdata 6 列（気配幅の列なし）40 本へ、写像層で組んだ引数束の ``ea_name`` だけを
    差し替えて `run_backtest` を呼んだ）:

        MA_Slope_EA      → exit=0 / trades=19 / 約定価格 40000.0（＝足の始値）
        WeeklyVolBand_EA → exit=0 / trades=1  / 約定価格 40000.0（同上）

    どちらも気配幅を供給しないデータで `current_open`（open + spread×point）の約定式を
    使い、spread=0 供給のまま完走して結果を出していた（H-4 が禁じる状態）。

固定する不変条件:
    R1  同じ実行仕様を 2 経路で組むと、**どちらも同じ理由で拒まれる**（N-17）。
    R2  現行経路（`settings` 不在）で N-17 の組合せが拒まれる。
    R3  手書き列挙から漏れていた EA（`WeeklyVolBand_EA`）も両経路で拒まれる。
    R4  設定の語彙を読む規則は現行経路で**落ちない**（迂回ではなく入力の不在である）。
    R5  気配幅を供給する実体では、同じ EA が両経路で走る（境界が過剰発火していない）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.domain.exceptions import BacktestError
from simulator.main import build_interactor, run_backtest
from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.main.tester_settings.unsupported import RULES
from simulator.tests.route_parity_fixtures import (
    MA_SLOPE_EA,
    NO_SL_TP,
    WEEKLY_VOL_BAND_EA,
    run_kwargs_for,
    weekly_params,
    write_marketdata_csv,
)
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_NAME,
    engine_binding,
    runnable_settings,
)

#: 判定の瞬間を ``current_open`` と名乗る EA と、その EA を走らせる追加引数。
_CURRENT_OPEN_EAS = {
    MA_SLOPE_EA: NO_SL_TP,
    WEEKLY_VOL_BAND_EA: weekly_params(),
}


@pytest.fixture()
def spreadless(tmp_path) -> Path:
    return write_marketdata_csv(tmp_path / "spreadless.csv", with_spread=False)


@pytest.fixture()
def with_spread(tmp_path) -> Path:
    return write_marketdata_csv(tmp_path / "with_spread.csv", with_spread=True, spread=5)


def _refusal_via_the_current_route(data_path: Path, ea_name: str):
    """現行経路（`settings` 不在）の合流点で走らせ、送出された例外を返す。

    観測点を `build_interactor` に置くのは、`run_backtest` が `BacktestError` を終了コードへ
    翻訳して送出しないためである（理由の 「`context`」 はそこで失われる）。終了コードの側は
    `_exit_code_via_the_current_route` が別に測る——両方を測らないと、「翻訳が変わっただけ」
    と「境界が効いた」を区別できない。
    """
    kwargs = run_kwargs_for(data_path, ea_name=ea_name, **_CURRENT_OPEN_EAS[ea_name])
    with pytest.raises(BacktestError) as caught:
        build_interactor(**kwargs)
    return caught.value


def _exit_code_via_the_current_route(tmp_path: Path, data_path: Path, ea_name: str) -> int:
    """現行経路で 1 run 実行し、終了コードを返す（是正前はここが 0 で完走した）。"""
    kwargs = run_kwargs_for(data_path, ea_name=ea_name, **_CURRENT_OPEN_EAS[ea_name])
    exit_code, _result = run_backtest(
        output_dir=tmp_path / f"direct_{ea_name}", **kwargs
    )
    return exit_code


def _refusal_via_the_settings_route(data_path: Path, ea_name: str):
    """settings 経路（`.ini` → 写像層 → 合流点）で走らせ、送出された例外を返す。"""
    settings = runnable_settings(Dates="0", Expert=f"{ea_name}.ex5")
    binding = engine_binding(
        data_path=str(data_path),
        config_overrides=None,
        ea_params={"ma_period": 2, "ma_method": "sma", "lot_size": 1.0, **NO_SL_TP},
    )
    from simulator.main.tester_settings.run_from_settings import run_effective_settings

    # 戦略の構築に要る注入物（「`weekly_forecast`」 等）は渡さない——保証境界は戦略を組む
    # **前**に効くため、拒否の観測には要らない。渡せる口が無いことも理由である
    # （`run_effective_settings` は拡張点を受けない）。
    with pytest.raises(BacktestError) as caught:
        run_effective_settings(settings.effective(), binding)
    return caught.value


class TestTheRouteDoesNotDecideWhetherTheBoundaryApplies:
    """R1 / R2 / R3。"""

    @pytest.mark.parametrize("ea_name", sorted(_CURRENT_OPEN_EAS))
    def test_both_routes_refuse_for_the_same_reason(self, spreadless, ea_name):
        # Arrange / Act
        direct = _refusal_via_the_current_route(spreadless, ea_name)
        settings = _refusal_via_the_settings_route(spreadless, ea_name)

        # Assert: 同じ宣言（N-17）で止まっている。
        assert direct.context["unsupported_id"] == "N-17"
        assert settings.context["unsupported_id"] == "N-17"
        assert direct.context["reason"] == RULES["N-17"].reason
        assert settings.context["reason"] == RULES["N-17"].reason
        # 空振り防止: 拒まれた値が当該 EA の名前であること（別の理由の一致ではない）。
        assert direct.context["value"] == ea_name
        assert settings.context["value"] == ea_name

    def test_the_current_route_refuses_a_spread_dependent_ea(self, tmp_path, spreadless):
        # R2: 是正前はここが exit=0 / trades=19 で完走していた（上記 docstring の実測）。
        error = _refusal_via_the_current_route(spreadless, MA_SLOPE_EA)
        assert error.context["unsupported_id"] == "N-17"
        assert _exit_code_via_the_current_route(tmp_path, spreadless, MA_SLOPE_EA) == 2

    def test_the_current_route_refuses_the_ea_missing_from_the_hand_written_list(
        self, tmp_path, spreadless
    ):
        # R3: `WeeklyVolBand_EA` は宣言が ``current_open`` なのに手書き列挙に無かった。
        error = _refusal_via_the_current_route(spreadless, WEEKLY_VOL_BAND_EA)
        assert error.context["unsupported_id"] == "N-17"
        assert (
            _exit_code_via_the_current_route(tmp_path, spreadless, WEEKLY_VOL_BAND_EA) == 2
        )


class TestTheSettingsScopeRulesDoNotFireOnTheCurrentRoute:
    """R4: 設定の語彙を読む規則は現行経路で落ちない（入力が存在しない）。"""

    def test_the_default_ea_runs_on_the_current_route(self, tmp_path, spreadless):
        kwargs = run_kwargs_for(spreadless, ea_name=DEFAULT_EA_NAME)
        exit_code, result = run_backtest(output_dir=tmp_path / "default", **kwargs)
        assert exit_code == 0
        assert result is not None and result.trades

    def test_the_settings_scope_rules_have_no_input_on_the_current_route(self):
        """現行経路が運ぶ判定入力の中に、設定の語彙を読む規則の入力が 1 つも無い。"""
        from simulator.main.tester_settings.unsupported import (
            RUN_SCOPE_INPUTS,
            SETTINGS_SCOPE_RULES,
        )

        assert SETTINGS_SCOPE_RULES, "設定の語彙を読む規則が 0 件（前提の崩れ）"
        for rule in SETTINGS_SCOPE_RULES:
            assert not set(rule.reads) & set(RUN_SCOPE_INPUTS), rule.unsupported_id


class TestTheBoundaryDoesNotOverFire:
    """R5: 気配幅を供給する実体では両経路とも走る。"""

    @pytest.mark.parametrize("ea_name", sorted(_CURRENT_OPEN_EAS))
    def test_the_current_route_runs_when_the_entity_supplies_spread(
        self, tmp_path, with_spread, ea_name
    ):
        kwargs = run_kwargs_for(
            with_spread, ea_name=ea_name, **_CURRENT_OPEN_EAS[ea_name]
        )
        exit_code, result = run_backtest(output_dir=tmp_path / f"ok_{ea_name}", **kwargs)
        assert exit_code == 0
        assert result is not None and result.trades

    def test_the_settings_route_runs_when_the_entity_supplies_spread(self, with_spread):
        exit_code, result, _metadata = run_from_settings(
            runnable_settings(Dates="0", Expert=f"{MA_SLOPE_EA}.ex5"),
            engine_binding(
                data_path=str(with_spread),
                config_overrides=None,
                ea_params={
                    "ma_period": 2, "ma_method": "sma", "lot_size": 1.0, **NO_SL_TP
                },
            ),
        )
        assert exit_code == 0
        assert result is not None and result.trades
