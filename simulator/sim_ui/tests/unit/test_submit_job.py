"""SubmitJobInteractor（ジョブ投入・F-3 / FR-10）の単体検定。

固定する不変条件:
    1. **並列実行**（§12.7 依頼者裁定）: 投入ごとに独立の子プロセスを**即時起動**する。
       同時 1 本の直列化はしない ＝ 先行ジョブが実行中でも 2 本目の投入が起動される。
    2. 起動できたら状態は 受付 → 実行中。起動に失敗したら 受付 → 失敗（理由つき）。
    3. **E-3**（§12.5）: 成行の建値推定に使える価格系列を指標レジストリが持たない戦略への
       sizing ON は**受付時に明示エラーで拒否**する（無音の誤動作を作らない）。
       拒否したジョブは台帳に作られず、子プロセスも起動しない。
    4. sizing OFF（既定）は E-3 判定の対象外＝どの戦略でも投入できる（既存挙動の保存）。

方式: Port をフェイクへ差し替えた usecase 単体（FS・子プロセスを持たない）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    FakeSeriesCatalog,
    FakeStopLossCatalog,
    allowed_backtest_keys,
    no_required_backtest_keys,
    required_backtest_keys,
    required_series,
    submission,
)
from simulator.sim_ui.usecase.job_models import SizingUnsupportedError
from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

# 実測済みの登録系列（`simulator/main/__init__.py` の registry ビルダ）。
# MA_Slope_EA だけが価格系列を持たない＝E-3 の拒否対象。
_CATALOG = FakeSeriesCatalog(
    {
        "MA_Slope_EA": frozenset({"ema"}),
        "MA_Slope_Pending_EA": frozenset({"ema", "open", "spread"}),
        "StopEntryProbe_EA": frozenset({"ema", "open", "spread"}),
        "WeeklyVolBand_EA": frozenset({"open"}),
        "PRO_fit_Band_EA": frozenset({"ema", "adx", "plus_di", "minus_di", "close"}),
    }
)


# 空カタログ＝「SL は設定パラメータで決まらない」＝受付では判定しない。
_NO_STOP_LOSS_PARAMS = FakeStopLossCatalog()


def _interactor(ledger=None, launcher=None, catalog=_CATALOG) -> SubmitJobInteractor:
    return SubmitJobInteractor(
        ledger=ledger or FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=catalog,
        required_series=required_series,
        # §12.8 の SL 受付検証は本節（E-3）の対象外なので、SL 系設定パラメータを
        # 「持たない」カタログ（＝受付では判定しない）を注入して既存の検証内容を保つ。
        # SL 検証そのものは本ファイル末尾の専用節で固定する。
        stop_loss_catalog=_NO_STOP_LOSS_PARAMS,
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


# --- 1. 正常系 -------------------------------------------------------------

def test_投入するとジョブ識別子が返る() -> None:
    # Arrange
    ledger = FakeLedger(next_ids=["abc123"])
    sut = _interactor(ledger=ledger)
    # Act
    got = sut.execute(submission())
    # Assert
    assert got.job_id == "abc123"


def test_投入すると子プロセスが即時起動する() -> None:
    """§12.7: 受付だけして後で走らせる（キュー滞留）ことはしない。"""
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(ledger=FakeLedger(next_ids=["j1"]), launcher=launcher)
    # Act
    sut.execute(submission())
    # Assert
    assert launcher.launched == ["j1"]


def test_起動後の状態は実行中() -> None:
    # Arrange
    ledger = FakeLedger(next_ids=["j1"])
    sut = _interactor(ledger=ledger)
    # Act
    got = sut.execute(submission())
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert ledger.load("j1").status is JobStatus.RUNNING


def test_投入要求は台帳へ保存される() -> None:
    """子プロセスは台帳に置かれた仕様を読んで走る（引数を argv に載せない）。"""
    # Arrange
    ledger = FakeLedger(next_ids=["j1"])
    sub = submission("WeeklyVolBand_EA")
    # Act
    _interactor(ledger=ledger).execute(sub)
    # Assert
    assert ledger.submissions["j1"] is sub


# --- 2. 並列実行（§12.7）---------------------------------------------------

def test_実行中のジョブがあっても次の投入は直ちに起動される() -> None:
    """§12.7「同時 1 本」案は棄却済み。滞留させたら本検定が落ちる。"""
    # Arrange
    ledger = FakeLedger(next_ids=["j1", "j2", "j3"])
    launcher = FakeLauncher()
    sut = _interactor(ledger=ledger, launcher=launcher)
    # Act（1 本目を完了させずに 3 本投入する）
    ids = [sut.execute(submission()).job_id for _ in range(3)]
    # Assert
    assert ids == ["j1", "j2", "j3"]
    assert launcher.launched == ["j1", "j2", "j3"]
    assert all(ledger.load(i).status is JobStatus.RUNNING for i in ids)


# --- 3. 起動失敗 -----------------------------------------------------------

def test_起動に失敗したら失敗状態になり理由が残る() -> None:
    # Arrange
    ledger = FakeLedger(next_ids=["j1"])
    launcher = FakeLauncher(fail_on_launch="No such file or directory")
    sut = _interactor(ledger=ledger, launcher=launcher)
    # Act
    got = sut.execute(submission())
    # Assert
    assert got.status == JobStatus.FAILED.value
    assert "No such file or directory" in got.failure_reason
    assert ledger.load("j1").status is JobStatus.FAILED


def test_起動に失敗しても例外は呼び出し側へ漏れない() -> None:
    """投入 API は「失敗状態のジョブ」を返す。500 ではなく状態として観測させる。"""
    # Arrange
    sut = _interactor(launcher=FakeLauncher(fail_on_launch="boom"))
    # Act / Assert（例外が出れば pytest が失敗させる）
    assert sut.execute(submission()).failure_reason is not None


# --- 4. E-3（§12.5 価格系列を供給できない戦略の sizing ON 拒否）------------

def test_価格系列を持たない戦略へのsizingONは受付時に拒否される() -> None:
    """§12.5: MA_Slope_EA の registry は ema のみ（実測）。close も open も無い。"""
    # Arrange
    sut = _interactor()
    sub = submission("MA_Slope_EA", sizing={"enabled": True})
    # Act / Assert
    with pytest.raises(SizingUnsupportedError) as exc:
        sut.execute(sub)
    # 何が足りないかがメッセージから読めること（無音の誤動作を作らない）
    message = str(exc.value)
    assert "MA_Slope_EA" in message
    assert "close" in message


def test_拒否された投入は台帳にも子プロセスにも残らない() -> None:
    # Arrange
    ledger = FakeLedger()
    launcher = FakeLauncher()
    sut = _interactor(ledger=ledger, launcher=launcher)
    # Act
    with pytest.raises(SizingUnsupportedError):
        sut.execute(submission("MA_Slope_EA", sizing={"enabled": True}))
    # Assert
    assert ledger.create_calls == 0
    assert launcher.launched == []


def test_約定基準がcurrent_openなら必要系列はopenになる() -> None:
    """§12.2: entry_price_basis="current_open" の建値は bar.open 由来。

    PRO_fit_Band_EA の registry は close を持つが **open を持たない**（実測）ため、
    current_open 指定では拒否される。基準ごとに必要系列が変わることを固定する。
    """
    # Arrange
    sut = _interactor()
    sub = submission(
        "PRO_fit_Band_EA", sizing={"enabled": True}, entry_price_basis="current_open"
    )
    # Act / Assert
    with pytest.raises(SizingUnsupportedError) as exc:
        sut.execute(sub)
    assert "open" in str(exc.value)


@pytest.mark.parametrize(
    "ea_name, basis",
    [
        ("PRO_fit_Band_EA", None),              # close 基準（既定）・close を持つ
        ("WeeklyVolBand_EA", "current_open"),   # open 基準・open を持つ
        ("MA_Slope_Pending_EA", "current_open"),
    ],
)
def test_必要系列を持つ戦略のsizingONは受け付ける(ea_name: str, basis) -> None:
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(
        submission(ea_name, sizing={"enabled": True}, entry_price_basis=basis)
    )
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


@pytest.mark.parametrize("sizing", [None, {"enabled": False}])
def test_sizingOFFは価格系列を持たない戦略でも投入できる(sizing) -> None:
    """既定 OFF は既存挙動と等価（§12.1）。E-3 判定を巻き込まない。"""
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(submission("MA_Slope_EA", sizing=sizing))
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


def test_系列を解決できない戦略へのsizingONは拒否される() -> None:
    """カタログが空集合（＝探索失敗の fail-safe）を返したら拒否する（黙って通さない）。

    なお実物の `EaRegistrySeriesCatalog` は未登録 ea_name を `build_interactor` と同じく
    既定 TC 経路へ倒すため、「未知の名前＝必ず拒否」ではない。ここで固定しているのは
    「カタログが系列を出せないなら通さない」という Interactor 側の規則である。
    """
    # Arrange
    sut = _interactor()
    # Act / Assert
    with pytest.raises(SizingUnsupportedError):
        sut.execute(submission("Unknown_EA", sizing={"enabled": True}))


# --- SL 保証の受付時検証（依頼者裁定 2026-08-11・§12.8）--------------------

# 裁定【主】: sizing ON のジョブは「戦略設定が SL を保証すること」を**投入時の必須項目**
# として検証し、満たさなければ E-3 と同じ受付検証で明示拒否する。
# 理由: ケリー基準は損切り幅が無ければリスク額を定義できず f→ロット変換が成立しない
#       ＝実行前に決着すべき前提条件。
# 判定は**ハードコードの戦略リストではなく** EA 別のカタログから導出する
# （`ea_registry_series_catalog` と同じ流儀）。

def _sl_submission(*, backtest=None, sizing=None, ea_name="TC24051901",
                   entry_price_basis="close", sizing_enabled=True):
    """`JobSubmission` は ea_name / entry_price_basis / sizing_enabled を
    `backtest` と `sizing` から**導出する**（プロパティ）。検定側もその形で組む。"""
    from simulator.sim_ui.usecase.job_models import JobSubmission

    bt = dict(backtest if backtest is not None else {"stop_loss_points": 500})
    bt.setdefault("ea_name", ea_name)
    if entry_price_basis != "close":
        bt.setdefault("config_overrides", {"entry_price_basis": entry_price_basis})
    if sizing_enabled and sizing is None:
        sizing = {"enabled": True}
    return JobSubmission(backtest=bt, sizing=sizing)


def _sl_interactor(catalog_map=None, **over):
    from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

    base = dict(
        ledger=FakeLedger(),
        launcher=FakeLauncher(),
        series_catalog=FakeSeriesCatalog({"TC24051901": frozenset({"close", "madiff"})}),
        required_series=lambda basis: "close",
        stop_loss_catalog=FakeStopLossCatalog(
            catalog_map
            if catalog_map is not None
            else {"TC24051901": frozenset({"stop_loss_points"})}
        ),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )
    base.update(over)
    return SubmitJobInteractor(**base)


# (a) SL 系パラメータ欠落 / 0 → 受付拒否

def test_SL系パラメータが欠落した設定のsizingONは受付拒否() -> None:
    from simulator.sim_ui.usecase.job_models import SizingUnsupportedError

    # Arrange（stop_loss_points 自体が無い）
    interactor = _sl_interactor()
    submission = _sl_submission(backtest={"ea_name": "TC24051901"})
    # Act / Assert
    with pytest.raises(SizingUnsupportedError):
        interactor.execute(submission)


@pytest.mark.parametrize("value", [0, 0.0, -1, -0.5])
def test_SL系パラメータが正でない設定のsizingONは受付拒否(value) -> None:
    from simulator.sim_ui.usecase.job_models import SizingUnsupportedError

    # Arrange
    interactor = _sl_interactor()
    submission = _sl_submission(
        backtest={"ea_name": "TC24051901", "stop_loss_points": value}
    )
    # Act / Assert
    with pytest.raises(SizingUnsupportedError):
        interactor.execute(submission)


def test_SL欠落の拒否理由はパラメータ名と理由を含む() -> None:
    """運用者が「何を直せばよいか」を文言だけで判断できること。"""
    from simulator.sim_ui.usecase.job_models import SizingUnsupportedError

    # Arrange
    interactor = _sl_interactor()
    submission = _sl_submission(backtest={"ea_name": "TC24051901", "stop_loss_points": 0})
    # Act
    with pytest.raises(SizingUnsupportedError) as exc:
        interactor.execute(submission)
    # Assert
    message = str(exc.value)
    assert "stop_loss_points" in message
    assert "SL" in message or "損切" in message


def test_SL欠落で拒否したジョブは台帳にも子プロセスにも残らない() -> None:
    """E-3 と同じ規律（拒否したジョブの残骸を作らない）。"""
    from simulator.sim_ui.usecase.job_models import SizingUnsupportedError

    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    interactor = _sl_interactor(ledger=ledger, launcher=launcher)
    submission = _sl_submission(backtest={"ea_name": "TC24051901", "stop_loss_points": 0})
    # Act
    with pytest.raises(SizingUnsupportedError):
        interactor.execute(submission)
    # Assert
    assert ledger.create_calls == 0
    assert launcher.launched == []


# (b) 受付を通る正常設定 → 受理

def test_SL系パラメータが正なら受理される() -> None:
    from simulator.sim_ui.domain.simulation_job import JobStatus

    # Arrange
    interactor = _sl_interactor()
    # Act
    view = interactor.execute(_sl_submission())
    # Assert
    assert view.status == JobStatus.RUNNING.value


def test_sizingOFFならSL検証は行わない() -> None:
    """§12.1 既定 OFF は既存挙動。SL 無しの戦略でも従来どおり走る。"""
    from simulator.sim_ui.domain.simulation_job import JobStatus

    # Arrange
    interactor = _sl_interactor()
    submission = _sl_submission(
        sizing_enabled=False, sizing=None,
        backtest={"ea_name": "TC24051901", "stop_loss_points": 0},
    )
    # Act
    view = interactor.execute(submission)
    # Assert
    assert view.status == JobStatus.RUNNING.value


def test_SL系パラメータを持たないEAは受付時に判定しない() -> None:
    """WeeklyVolBand のように SL を config 由来で持たない EA は submit では決着しない。

    実測（`simulator/adapter/strategy/weekly_vol_band.py:71`）: SL は `VolatilityBand` の
    `band.S` から決まり、`stop_loss_points` を読まない。設定パラメータの正値検査では
    保証を証明できないため、ここでは通し、実行中の内部不変条件違反（【従】fail-stop）で
    受け止める。カタログが空集合を返すことがその境界を表す。
    """
    from simulator.sim_ui.domain.simulation_job import JobStatus

    # Arrange
    interactor = _sl_interactor(
        catalog_map={},   # 当該 EA は SL 系設定パラメータを持たない
        series_catalog=FakeSeriesCatalog(
            {"WeeklyVolBand_EA": frozenset({"open"})}
        ),
        required_series=lambda basis: "open",
    )
    submission = _sl_submission(
        backtest={"ea_name": "WeeklyVolBand_EA"},
        entry_price_basis="current_open",
    )
    # Act
    view = interactor.execute(submission)
    # Assert
    assert view.status == JobStatus.RUNNING.value


def test_価格系列の拒否はSL検証より先に効く() -> None:
    """E-3 と本検証の順序を固定する（両方満たさない投入で E-3 の文言が出ること）。"""
    from simulator.sim_ui.usecase.job_models import SizingUnsupportedError

    # Arrange（系列も SL も無い）
    interactor = _sl_interactor(
        series_catalog=FakeSeriesCatalog({"TC24051901": frozenset({"ema"})}),
    )
    submission = _sl_submission(backtest={"ea_name": "TC24051901", "stop_loss_points": 0})
    # Act
    with pytest.raises(SizingUnsupportedError) as exc:
        interactor.execute(submission)
    # Assert
    assert "価格系列" in str(exc.value)


# --- backtest キーの許可集合（コードレビュー 🔴-5b）-----------------------

# 実測された壊れ方: `backtest` は sim core が中身を解釈せず子へ素通しし、子は
# `run_backtest(**meta)` へ展開する。未知キーがあると子プロセスで TypeError になり、
# 「投入は 200 で通ったのに実行だけ落ちる」という遅い失敗になる。受付時に弾く。
# 許可集合は `inspect.signature(build_interactor)` から導出する（手書き表禁止）。

def test_許可外のbacktestキーは受付拒否() -> None:
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    # Arrange
    interactor = _sl_interactor()
    submission = _sl_submission(
        backtest={"ea_name": "TC24051901", "stop_loss_points": 500, "bogus_key": 1}
    )
    # Act / Assert（🔵-C: sizing 無関係の検証なので専用型）
    with pytest.raises(JobSubmissionInvalidError) as exc:
        interactor.execute(submission)
    assert "bogus_key" in str(exc.value)


def test_許可集合はbuild_interactorの実シグネチャから導出する() -> None:
    """手書き表だと build_interactor の引数が増えたとき取り残される。"""
    import inspect

    from simulator.main import build_interactor
    from simulator.sim_ui.main.composition_root_jobs import allowed_backtest_keys

    # Arrange / Act
    allowed = allowed_backtest_keys()
    params = set(inspect.signature(build_interactor).parameters)
    # Assert（署名由来であること・注入専用の引数は除く）
    assert allowed <= params
    assert "ea_name" in allowed
    assert "strategy_decorator" not in allowed, (
        "戦略 Decorator は run_job が注入する。JSON から渡させない"
    )


def test_既知キーのみなら受理される() -> None:
    from simulator.sim_ui.domain.simulation_job import JobStatus

    interactor = _sl_interactor()
    view = interactor.execute(
        _sl_submission(
            backtest={
                "ea_name": "TC24051901", "stop_loss_points": 500,
                "symbol": "EURUSD", "period": "M1", "config_overrides": {},
            }
        )
    )
    assert view.status == JobStatus.RUNNING.value


def test_許可外キーで拒否したジョブは台帳に残らない() -> None:
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    ledger, launcher = FakeLedger(), FakeLauncher()
    interactor = _sl_interactor(ledger=ledger, launcher=launcher)
    with pytest.raises(JobSubmissionInvalidError):
        interactor.execute(
            _sl_submission(backtest={"ea_name": "TC24051901", "nope": 1})
        )
    assert ledger.create_calls == 0
    assert launcher.launched == []


def test_sizingOFFでもキー検証は行う() -> None:
    """未知キーは sizing の有無と無関係に子プロセスを落とす（🔵-C: 専用の例外型）。"""
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    interactor = _sl_interactor()
    with pytest.raises(JobSubmissionInvalidError):
        interactor.execute(
            _sl_submission(
                sizing_enabled=False, sizing=None,
                backtest={"ea_name": "TC24051901", "nope": 1},
            )
        )


# --- 必須キー欠落の受付検証（コードレビュー 🟡-A / 🔵-C）------------------

# 実測された壊れ方: 必須引数が欠けた `backtest` は受付を 202 で通り、子プロセスで
# `missing 17 required keyword-only arguments` になる（遅い失敗）。許可集合と同じ
# 単一ソース（`inspect.signature(build_interactor)`）から**必須集合**も導出して
# 受付で弾く。表を二重に持たない。
# 🔵-C: sizing と無関係な検証に `SizingUnsupportedError` を流用しない。

def test_必須キーが欠けた投入は受付拒否() -> None:
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    interactor = _sl_interactor(required_backtest_keys=required_backtest_keys)
    with pytest.raises(JobSubmissionInvalidError) as exc:
        interactor.execute(_sl_submission(backtest={"ea_name": "TC24051901"}))
    # 何が足りないかが読めること
    assert "data_path" in str(exc.value)


def test_必須集合はbuild_interactorのシグネチャから導出する() -> None:
    """既定値のない引数＝必須。許可集合と同一ソース（表を二重化しない）。"""
    import inspect

    from simulator.main import build_interactor
    from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

    required = required_backtest_keys()
    params = inspect.signature(build_interactor).parameters
    expected = {
        name for name, p in params.items() if p.default is inspect.Parameter.empty
    }
    assert required == expected
    assert "data_path" in required
    assert "config_overrides" not in required   # 既定値あり＝任意


def test_必須キーが揃えば受理される() -> None:
    from simulator.sim_ui.domain.simulation_job import JobStatus

    from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

    backtest = {name: 1 for name in required_backtest_keys()}
    backtest["ea_name"] = "TC24051901"
    backtest["stop_loss_points"] = 500
    view = _sl_interactor(
        required_backtest_keys=required_backtest_keys
    ).execute(_sl_submission(backtest=backtest))
    assert view.status == JobStatus.RUNNING.value


def test_未知キーの例外もsizing無関係の型になる() -> None:
    """🔵-C: キー検証は sizing の有無と無関係。専用の例外型を使う。"""
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    interactor = _sl_interactor()
    with pytest.raises(JobSubmissionInvalidError):
        interactor.execute(
            _sl_submission(backtest={"ea_name": "TC24051901", "nope": 1})
        )


def test_受付検証の例外はSizingUnsupportedErrorではない() -> None:
    """型の取り違えで「サイジングの問題」と誤読させない。"""
    from simulator.sim_ui.usecase.job_models import (
        JobSubmissionInvalidError,
        SizingUnsupportedError,
    )

    assert not issubclass(JobSubmissionInvalidError, SizingUnsupportedError)
    assert not issubclass(SizingUnsupportedError, JobSubmissionInvalidError)


def test_必須キー欠落で拒否したジョブは台帳に残らない() -> None:
    from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

    ledger, launcher = FakeLedger(), FakeLauncher()
    interactor = _sl_interactor(
        ledger=ledger, launcher=launcher,
        required_backtest_keys=required_backtest_keys,
    )
    with pytest.raises(JobSubmissionInvalidError):
        interactor.execute(_sl_submission(backtest={"ea_name": "TC24051901"}))
    assert ledger.create_calls == 0
    assert launcher.launched == []
