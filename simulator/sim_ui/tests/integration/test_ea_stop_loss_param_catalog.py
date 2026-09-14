"""EaStopLossParamCatalog（§12.8 受付時 SL 検証のカタログ）の結合検定。

固定する不変条件:
    1. 単一ソースは `simulator.main` の公開アクセサ（`build_ea_strategy`）。ea_name →
       パラメータ名の対応表をアダプタに**書き写さない**（§12.1「戦略リストのハードコード禁止」）。
       束縛は Composition Root が持つ（ISSUE-405・R-4 と同型）。
    2. `stop_loss_points` で SL を組む EA はその名前を返す。
    3. SL が設定パラメータで決まらない EA（WeeklyVolBand）は**空集合**を返す
       （＝受付では判定せず、実行中の fail-stop に委ねる）。
    4. 未知 ea_name は既定 TC 経路へフォールバックする（選択規則は `_select_ea_factory`
       の 1 箇所であり、本カタログはそこへ委譲した結果を見るだけ）。
    5. 構築そのものができないときだけ ``None``（探索失敗）を返す。

実測の出典（本検定が守る事実）:
    tc24051901.py:67 / ma_slope_pending.py:131 / stop_entry_probe.py:92 /
    pro_fit_band.py:118 は `cfg["stop_loss_points"]` を読む。
    weekly_vol_band.py:71 は `band.S`（VolatilityBand）から SL を作り読まない。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.adapter.ea_stop_loss_param_catalog import EaStopLossParamCatalog
from simulator.sim_ui.main.composition_root_jobs import build_stop_loss_catalog


@pytest.fixture()
def catalog() -> EaStopLossParamCatalog:
    # 束縛は Composition Root から取る（テストが束縛先を書き写さない）。
    return build_stop_loss_catalog()


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
    """WeeklyVolBand は SL が設定パラメータで決まらない（＝受付では判定しない）。"""
    assert not catalog.stop_loss_params("WeeklyVolBand_EA")


def test_未知のea_nameは既定TC経路へフォールバックする(
    catalog: EaStopLossParamCatalog,
) -> None:
    """フォールバック規則は `simulator.main._select_ea_factory` の 1 箇所にある。

    本カタログはその規則を写さず、公開アクセサ経由の構築結果を見るだけである
    （ISSUE-405 以前は `_EA_FACTORIES.get(ea_name, _factory_tc24051901)` を写していた）。
    """
    assert "stop_loss_points" in catalog.stop_loss_params("未登録のEA")


def test_結果はキャッシュされ同一集合を返す(catalog: EaStopLossParamCatalog) -> None:
    """受付は 1 秒以内（§8.1）。毎回組み立て直さない。"""
    first = catalog.stop_loss_params("TC24051901")
    assert catalog.stop_loss_params("TC24051901") == first


def test_登録表に載る全EAを判定できる(catalog: EaStopLossParamCatalog) -> None:
    """登録表が増えたときに取り残されないこと（表を写していないことの担保）。"""
    from simulator.main import known_ea_names

    for ea_name in known_ea_names():
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
#
# ISSUE-405 で意味論が変わった点（実測）:
#   旧実装は factory 関数の**ソース文字列**から戦略クラス名を推測していたため、
#   `_factory_weekly_vol_band` が `make_weekly_vol_band(...)`（ビルダ関数）を呼ぶ
#   WeeklyVolBand ではクラスを特定できず ``None``（探索失敗）に落ちていた。
#   公開アクセサ `build_ea_strategy` で**実際に組み立てる**現行実装は
#   ``type(strategy) is WeeklyVolBand`` まで到達し、その実装ソースが
#   ``stop_loss_points`` を参照しないため ``frozenset()`` を返す。
#   すなわち「調べられなかった」→「調べた上で該当なし」へ**強くなった**。
#   受付側の挙動は不変（`submit_job` は `if not params: return`）。


def test_WeeklyVolBandは構築でき該当なしと判明する(
    catalog: EaStopLossParamCatalog,
) -> None:
    got = catalog.stop_loss_params("WeeklyVolBand_EA")
    assert got == frozenset()
    assert got is not None  # 「探索失敗」ではない


def test_WeeklyVolBandの戦略クラスに到達している() -> None:
    """空集合の根拠が「戦略クラスまで届いた上で該当なし」であることを実測で固定する。

    これが無いと、構築が壊れて別のクラスに落ちても空集合が返り、区別できない。
    """
    from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand
    from simulator.sim_ui.main.composition_root_jobs import _build_ea_strategy

    strategy = EaBuildProbe(_build_ea_strategy).for_ea("WeeklyVolBand_EA")
    assert isinstance(strategy, WeeklyVolBand)


def test_探索できた戦略は集合を返す(catalog: EaStopLossParamCatalog) -> None:
    got = catalog.stop_loss_params("TC24051901")
    assert isinstance(got, frozenset)
    assert "stop_loss_points" in got


def test_構築できないときはNoneを返す() -> None:
    """``None``（探索失敗）が到達可能な値であり続けること。

    注入で構築を失敗させる（束縛の差し替え点が実在することの確認でもある）。
    """

    def _cannot_build(**_spec):
        raise RuntimeError("構築できない")

    catalog = EaStopLossParamCatalog(probe=EaBuildProbe(_cannot_build))
    assert catalog.stop_loss_params("TC24051901") is None


def test_探索失敗と該当なしを返り値で区別できる() -> None:
    """None（探索失敗）と frozenset()（該当なし）は意味が違う。"""

    def _cannot_build(**_spec):
        raise RuntimeError("構築できない")

    unresolvable = EaStopLossParamCatalog(
        probe=EaBuildProbe(_cannot_build)
    ).stop_loss_params("TC24051901")
    resolved = build_stop_loss_catalog().stop_loss_params("TC24051901")
    assert unresolvable is None
    assert resolved is not None


def test_既定束縛を持たない() -> None:
    """R-4 と同型: 構築の束縛は必須引数（adapter → main の外向き依存を作らない）。"""
    import inspect

    parameter = inspect.signature(EaStopLossParamCatalog.__init__).parameters["probe"]
    assert parameter.default is inspect.Parameter.empty


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
