"""`MATH_CALCULATIONS` の実行経路（内部設計 §8.2 D-09・基本設計 §4.5.2・T-03）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.main.tester_settings.math_calculations` と Null 実装 3 種は未実装のため、
現時点では**収集エラー（ImportError）** になる（アサート失敗ではない）。

固定する仕様（基本設計 §4.5.2「`Math calculations` の正常終了定義」）:
    ティックを 1 件も生成せず、メインループを 0 回回し、例外なしで終了コード 0 を返す。
    その結果として `trades` / `deals` / `equity_curve` / `balance_curve` はいずれも
    長さ 0、`profit_factor` は `math.inf`（METRICS §1.1: GrossLoss==0 のとき ∞）、
    `expected_payoff` は `0.0`（n==0）。

    加えて規則 S（基本設計 §4.5.5）——「実行要求時、`data`（バー系列）の有無が
    `tick_model` と整合する（`MATH_CALCULATIONS` は data is None）」——に反する要求は
    E-03（`SettingsActivationError`）で Fail-Stop する。

    経路の前提（規則 A）: 参照するのは `EffectiveSettings` であり、inert 11 フィールドは
    `None` である。口座計算に使える値が型として存在しないことを先に固定する。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.tester_settings_exceptions import SettingsActivationError
from simulator.main.tester_settings.math_calculations import run_math_calculations
from simulator.tests.tester_settings_engine_fixtures import engine_binding, runnable_settings
from simulator.usecase.tester_settings.enums import TickModel
from simulator.usecase.tester_settings.models import INERT_FIELDS

#: `Model=3`（`MATH_CALCULATIONS`。corpus 未出現・消去法＝TBD-01）。
_MATH_MODEL = "3"


def _math_effective():
    """`MATH_CALCULATIONS` の `EffectiveSettings`（規則 A 適用済みの実行時ビュー）。"""
    return runnable_settings(Model=_MATH_MODEL).effective()


def _math_binding(**overrides):
    """規則 S を満たす束縛（バー系列を供給しない＝`data_path is None`）。"""
    return engine_binding(data_path=None, **overrides)


class TestEffectivePrecondition:
    """規則 A: inert 11 フィールドが `None` である経路であることを先に固定する。"""

    def test_tick_model_is_math_calculations(self):
        effective = _math_effective()
        assert effective.tick_model is TickModel.MATH_CALCULATIONS
        assert effective.is_math_calculations is True

    def test_all_inert_fields_are_none(self):
        effective = _math_effective()
        # 「非活性でも値は保持する」のは `TesterSettings` 側。実行時ビューは値を持たない。
        assert {name: getattr(effective, name) for name in INERT_FIELDS} == {
            name: None for name in INERT_FIELDS
        }

    def test_inert_fields_are_the_eleven_declared_ones(self):
        assert _math_effective().inert_fields == INERT_FIELDS
        assert len(INERT_FIELDS) == 11


class TestNormalTermination:
    """基本設計 §4.5.2 の正常終了定義（8 項目）を 1 実行で測る。"""

    @pytest.fixture()
    def run(self):
        # Arrange / Act: 例外なしで完走すること自体が §4.5.2 の 1 項目
        return run_math_calculations(_math_effective(), _math_binding())

    def test_exit_code_is_zero(self, run):
        exit_code = run[0]
        assert exit_code == 0

    def test_no_trades_and_no_deals_are_produced(self, run):
        result = run[1]
        assert len(result.trades) == 0
        assert len(result.deals) == 0
        assert result.stats.trades == 0

    def test_curves_are_empty(self, run):
        result = run[1]
        # 追記経路はメインループ内にのみ存在する（ループ 0 回＝長さ 0）
        assert len(result.equity_curve) == 0
        assert len(result.balance_curve) == 0

    def test_profit_factor_is_infinite(self, run):
        # METRICS §1.1: GrossLoss == 0 のとき ∞（0 除算にしない）
        assert run[1].stats.profit_factor == math.inf

    def test_recovery_factor_is_infinite(self, run):
        # 実測: 最大ドローダウン 0 のとき ∞。`profit_factor` と同じ「分母 0 → ∞」規約。
        assert run[1].stats.recovery_factor == math.inf

    def test_exactly_two_statistics_are_non_finite(self, run):
        """非有限値が **2 つ**であることを明示する（呼出側が JSON 化で詰まる箇所）。

        `math.inf` は `json.dumps` の既定で `Infinity` になり、厳格な JSON パーサでは
        読めない。どのフィールドが非有限なのかを一覧で固定しておかないと、3 つ目が
        増えたときに出力側で初めて気付くことになる。
        """
        stats = run[1].stats
        non_finite = sorted(
            name
            for name in vars(stats)
            if isinstance(getattr(stats, name), float)
            and not math.isfinite(getattr(stats, name))
        )
        assert non_finite == ["profit_factor", "recovery_factor"]

    def test_expected_payoff_is_zero(self, run):
        # METRICS: n == 0 のとき 0.0
        assert run[1].stats.expected_payoff == 0.0

    def test_metadata_records_the_settings_vocabulary(self, run):
        # §8.5: エンジンへは既定 `every_tick` が渡るが、Settings 層の語彙を隠さない
        metadata = run[2]
        assert metadata.tick_model == "math_calculations"
        assert tuple(metadata.inert_fields) == INERT_FIELDS
        assert metadata.execution_delay is None  # inert（規則 A）


class TestRuleS:
    """規則 S: `MATH_CALCULATIONS` にバー系列を与えた要求は E-03。"""

    def test_supplying_data_path_raises_activation_error(self):
        with pytest.raises(SettingsActivationError) as excinfo:
            run_math_calculations(
                _math_effective(),
                engine_binding(data_path="/nonexistent/synthetic/jp225.csv"),
            )
        context = excinfo.value.context
        assert context["error_id"] == "E-03"
        assert context["rule_id"] == "S"

    def test_activation_error_is_catchable_as_config_error(self):
        # T-13: 既存 CLI の `except ConfigError` → 終了コード 2 に載る
        from simulator.domain.exceptions import ConfigError

        with pytest.raises(ConfigError):
            run_math_calculations(
                _math_effective(),
                engine_binding(data_path="/nonexistent/synthetic/jp225.csv"),
            )


class TestNullPortsSubstitutability:
    """Null 実装は Port ABC の実装であること（LSP・置換可能性）。"""

    def test_null_ports_implement_their_port_abcs(self):
        from simulator.adapter.execution.null_tick_model import NullTickModel
        from simulator.adapter.indicator.null_registry import NullIndicatorRegistry
        from simulator.adapter.strategy.null_strategy import NullStrategy
        from simulator.usecase.ports import IndicatorPort, StrategyPort, TickModelPort

        assert issubclass(NullStrategy, StrategyPort)
        assert issubclass(NullIndicatorRegistry, IndicatorPort)
        assert issubclass(NullTickModel, TickModelPort)

    def test_null_tick_model_generates_no_ticks(self):
        from simulator.adapter.execution.null_tick_model import NullTickModel
        from simulator.domain.bar import Bar

        bar = Bar(time=0, open=100.0, high=100.5, low=99.5, close=100.2, volume=1.0, spread=0)
        # TickModelPort の事後条件は「空列を許容する」。Null 実装は常に空列。
        assert tuple(NullTickModel().ticks_of(bar, 100.0)) == ()
