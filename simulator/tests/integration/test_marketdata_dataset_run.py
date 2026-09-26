"""全期間 JP225 実データ（marketdata 形式・2012〜）の実行結線の統合検定（2026-09-06）。

依頼者承認の結線（データセット = 全期間 JP225 実データ・`_FULL_CSV`）が**実データ・実エンジン**で
成立することを固定する。全歴史ランは実測で数十分級のため、ここでは期間窓つきの run で
「読める・窓が効く・完走する」を測る（e2e スタックはデータ実体を fixture へ差し替えており、
本検定が全期間実体を実際に読む唯一の自動検定である）。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from simulator.domain.tester_settings_exceptions import UnsupportedSettingError
from simulator.main import build_interactor, run_backtest
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.tests.tester_settings_engine_fixtures import (
    custom_range_settings,
    engine_binding,
)

_ROOT = Path(__file__).resolve().parents[3]
_FULL_CSV = _ROOT / "data" / "marketdata" / "jp225_m1.csv"

pytestmark = pytest.mark.skipif(
    not _FULL_CSV.is_file(), reason="全期間データ実体（data/marketdata/jp225_m1.csv）が無い環境"
)


def _kwargs(**settings_overrides):
    settings = custom_range_settings(
        date(2024, 1, 8), date(2024, 1, 12), Period="M1", Deposit="100000000",
        **settings_overrides,
    )
    return to_interactor_kwargs(
        settings,
        engine_binding(
            data_path=str(_FULL_CSV), period="M1",
            known_ea_names=("TC24051901", "MA_Slope_EA"),
        ),
    )


def test_a_windowed_run_on_the_full_dataset_completes(tmp_path):
    """期間指定（1 週間）の run が全期間実体の上で完走する（読める＋窓が効く）。"""
    # Act
    code, result = run_backtest(output_dir=tmp_path, **_kwargs())
    # Assert
    assert code == 0, f"exit={code}"
    assert result is not None
    # 窓が効いている（全期間 4,604,080 本を回していない）ことは所要時間でなく本文で固定
    # できないため、bars 数で測る（1 週間の M1 は高々 7*24*60 本）。
    assert 0 < len(result.equity_curve) <= 7 * 24 * 60 + 1


def test_a_spread_dependent_ea_is_fail_stopped_before_running(tmp_path):
    """MA_Slope 系（spread 依存）× 本データは N-17 で実行前に止まる（沈黙縮退なし）。

    観測点が 2 つあるのは ISSUE-525 の是正によるものである。N-17 の適用点は合流点
    （`build_interactor`）へ移ったので、`run_backtest` から見ると例外は**終了コードへ
    翻訳されて**返る（送出されない）。理由の 「`context`」 は合流点で観測する。
    """
    # Act / Assert: 理由（合流点）
    with pytest.raises(UnsupportedSettingError) as excinfo:
        build_interactor(**_kwargs(Expert="MA_Slope_EA.ex5"))
    assert excinfo.value.context["unsupported_id"] == "N-17"
    # Act / Assert: 終了コード（1 run 実行しても始まらない）
    code, result = run_backtest(output_dir=tmp_path, **_kwargs(Expert="MA_Slope_EA.ex5"))
    assert code == 2 and result is None
