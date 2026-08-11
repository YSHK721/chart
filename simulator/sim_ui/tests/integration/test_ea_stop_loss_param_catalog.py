"""EaStopLossParamCatalog（§12.8 受付時 SL 検証のカタログ）の結合検定。

固定する不変条件:
    1. 単一ソースは `simulator.main._EA_FACTORIES`。ea_name → パラメータ名の対応表を
       アダプタに**書き写さない**（§12.1「戦略リストのハードコード禁止」）。
    2. `stop_loss_points` で SL を組む EA はその名前を返す。
    3. SL が設定パラメータで決まらない EA（WeeklyVolBand）は**空集合**を返す
       （＝受付では判定せず、実行中の fail-stop に委ねる）。
    4. 未知 ea_name は既定 TC 経路へフォールバックする（`_EA_FACTORIES` と同じ規則）。

実測の出典（本検定が守る事実）:
    tc24051901.py:67 / ma_slope_pending.py:131 / stop_entry_probe.py:92 /
    pro_fit_band.py:118 は `cfg["stop_loss_points"]` を読む。
    weekly_vol_band.py:71 は `band.S`（VolatilityBand）から SL を作り読まない。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.ea_stop_loss_param_catalog import EaStopLossParamCatalog


@pytest.fixture()
def catalog() -> EaStopLossParamCatalog:
    return EaStopLossParamCatalog()


@pytest.mark.parametrize(
    "ea_name",
    [
        "TC24051901",
        "MA_Slope_Pending_EA",
        "StopEntryProbe_EA",
        "PRO_fit_Band_EA",
    ],
)
def test_設定でSLを組むEAはパラメータ名を返す(
    catalog: EaStopLossParamCatalog, ea_name: str
) -> None:
    assert "stop_loss_points" in catalog.stop_loss_params(ea_name)


def test_設定でSLを決めないEAは受付判定の対象外になる(
    catalog: EaStopLossParamCatalog,
) -> None:
    """WeeklyVolBand はビルダ関数生成のため**探索失敗（None）**になる（🟡-3）。

    いずれにせよ「設定の正値検査では SL 保証を証明できない」ため受付では判定しない。
    """
    assert not catalog.stop_loss_params("WeeklyVolBand_EA")


def test_未知のea_nameは既定TC経路へフォールバックする(
    catalog: EaStopLossParamCatalog,
) -> None:
    """`_EA_FACTORIES.get(ea_name, _factory_tc24051901)` と同じ規則。"""
    assert "stop_loss_points" in catalog.stop_loss_params("未登録のEA")


def test_結果はキャッシュされ同一集合を返す(catalog: EaStopLossParamCatalog) -> None:
    """受付は 1 秒以内（§8.1）。毎回ソースを読み直さない。"""
    first = catalog.stop_loss_params("TC24051901")
    assert catalog.stop_loss_params("TC24051901") == first


def test_登録表に載る全EAを判定できる(catalog: EaStopLossParamCatalog) -> None:
    """登録表が増えたときに取り残されないこと（表を写していないことの担保）。"""
    from simulator.main import _EA_FACTORIES

    for ea_name in _EA_FACTORIES:
        # 例外を出さずに 3 値のいずれかを返すこと（探索失敗は None）
        got = catalog.stop_loss_params(ea_name)
        assert got is None or isinstance(got, frozenset)


def test_MA_Slope_EAはSLを持てない実装である() -> None:
    """実測の記録: `ma_slope.py:110` は `sl=None` 固定・:50-55 は正値指定を例外にする。

    したがって MA_Slope_EA × sizing ON は構造的に成立しない。実際には E-3（registry が
    ema のみ）で先に拒否されるが、その前提が変わっても気付けるように事実を固定する。
    """
    import inspect

    from simulator.adapter.strategy.ma_slope import MaSlope

    source = inspect.getsource(MaSlope)
    assert "sl=None" in source
    assert "未サポート" in source


# --- 探索失敗と「SL 設定パラメータ無し」の区別（コードレビュー 🟡-3）------

# 実測に基づく訂正: WeeklyVolBand は `_factory_weekly_vol_band` が
# `make_weekly_vol_band(...)`（**ビルダ関数**）で戦略を組み立てるため、factory の
# ソースからは戦略**クラス**を特定できない。よって従来の空集合は
# 「SL 設定パラメータを持たない」ではなく「**探索できなかった**」が正しい。
# 両者を返り値で区別する（None=探索失敗 / frozenset()=探索できたが該当なし）。

def test_探索できない戦略はNoneを返す(catalog: EaStopLossParamCatalog) -> None:
    """WeeklyVolBand はビルダ関数生成のためクラスを特定できない＝探索失敗。"""
    assert catalog.stop_loss_params("WeeklyVolBand_EA") is None


def test_探索できた戦略は集合を返す(catalog: EaStopLossParamCatalog) -> None:
    got = catalog.stop_loss_params("TC24051901")
    assert isinstance(got, frozenset)
    assert "stop_loss_points" in got


def test_探索失敗と該当なしを返り値で区別できる(
    catalog: EaStopLossParamCatalog,
) -> None:
    """None（探索失敗）と frozenset()（該当なし）は意味が違う。"""
    unresolvable = catalog.stop_loss_params("WeeklyVolBand_EA")
    resolved = catalog.stop_loss_params("TC24051901")
    assert unresolvable is None
    assert resolved is not None


def test_探索失敗のジョブは受付で判定されない() -> None:
    """探索できない＝設定検査で保証を証明できない。受付では通し fail-stop に委ねる。"""
    from simulator.sim_ui.domain.simulation_job import JobStatus
    from simulator.sim_ui.tests.integration._fake_ports import (
        FakeLauncher,
        FakeLedger,
        FakeSeriesCatalog,
        allowed_backtest_keys,
        no_required_backtest_keys,
        required_series,
    )
    from simulator.sim_ui.usecase.job_models import JobSubmission
    from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

    class _Unresolvable:
        @staticmethod
        def stop_loss_params(ea_name: str):
            return None      # 探索失敗

    # Arrange
    interactor = SubmitJobInteractor(
        ledger=FakeLedger(),
        launcher=FakeLauncher(),
        series_catalog=FakeSeriesCatalog({"WeeklyVolBand_EA": frozenset({"open"})}),
        required_series=required_series,
        stop_loss_catalog=_Unresolvable(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )
    submission = JobSubmission(
        backtest={
            "ea_name": "WeeklyVolBand_EA",
            "config_overrides": {"entry_price_basis": "current_open"},
        },
        sizing={"enabled": True},
    )
    # Act
    view = interactor.execute(submission)
    # Assert
    assert view.status == JobStatus.RUNNING.value
