"""受付（sizing ON）が EA を組み立てる回数（絶対命令 2026-08-28・ISSUE-533 段階 2）。

段階 2 で E-3 の判定入力を「設定値」から「ea_name」へ移した（建値基準の権威は戦略の宣言
ただ 1 つ）。そのため受付段が **EA を 1 本組み立てて宣言を読む**ようになった。ここで入り
込みうる浪費は状態検証では原理的に落ちない:

    組み立てを投入ごとにやり直しても、返す判定は同じである＝出力は 1 ビットも変わらない。
    受付の費用だけが投入数に比例して増える。

したがって測るのは時間ではなく**回数**であり、固定するのは回数そのものではなく
**無駄の不在**である。

固定する不変条件:
    1. 同じ ea_name の 2 度目以降の投入で、EA の組み立てが **1 度も発行されない**
       （発行 − 相異なる ea_name の数 = 0）。
    2. 投入数を増やしても発行が増えない（オーダーの表明）。規模 2 点で等しいことだけを
       表明し、**回数そのものは焼き込まない**。

**未検証（残存リスク）**: 1 つの ea_name の初回投入では、受付が同じ EA を **2 つの探索**
（必要系列の問い・SL 系設定パラメータの問い）でそれぞれ組み立てる。実測 2026-09-25 では
初回の発行が段階 2 前の 2 倍になる。ここは「投入数で増えない」ことだけを固定しており、
初回の重複そのものは別途の裁定（探索を 1 本の共有した組み立てへ束ねる）を要する。
"""
from __future__ import annotations

from typing import Any

import pytest

from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    FakeSeriesCatalog,
    allowed_backtest_keys,
)

#: 既定 TC 経路の EA（comma 形式で組める・登録系列に "close" を持つ）。
_EA = "TC24051901"
#: 規模の 2 点（片方が他方の 4 倍）。値そのものは表明に現れない。
_FEW_SUBMISSIONS = 2
_MANY_SUBMISSIONS = 8


def _no_required_backtest_keys() -> "frozenset[str]":
    return frozenset()


def _submission():
    from simulator.sim_ui.usecase.job_models import JobSubmission

    return JobSubmission(
        backtest={
            "ea_name": _EA,
            "symbol": "JP225",
            "period": "M5",
            "stop_loss_points": 500,
        },
        sizing={"enabled": True},
    )


@pytest.fixture()
def build_spy(monkeypatch):
    """受付が呼ぶ EA 構築関数の発行を数える Test Spy（本物の構築へ委譲する）。"""
    import simulator.sim_ui.main.composition_root_jobs as root

    calls: "list[str]" = []
    original = root._build_ea_strategy

    def spy(**spec: Any) -> Any:
        calls.append(str(spec.get("ea_name")))
        return original(**spec)

    monkeypatch.setattr(root, "_build_ea_strategy", spy)
    # 置き場は本モジュールの外に持ち越される（プロセス内で共有される）。計測のたびに
    # 空にしないと「前の検定が温めた置き場」を測ってしまう（空振り）。
    root._REQUIRED_SERIES_CACHE.clear()
    return calls


def _interactor():
    import simulator.sim_ui.main.composition_root_jobs as root
    from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

    return SubmitJobInteractor(
        ledger=FakeLedger(),
        launcher=FakeLauncher(),
        series_catalog=FakeSeriesCatalog({_EA: frozenset({"close", "madiff"})}),
        required_series=root._required_series,
        stop_loss_catalog=root.build_stop_loss_catalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=_no_required_backtest_keys,
    )


def _measure(build_spy, interactor, submissions: int) -> dict:
    """``interactor`` へ ``submissions`` 件を受け付けさせたときの発行と受理件数。

    受付器は**1 つを使い回す**（本番の形。合成根は `build_sim_jobs_app` で 1 度だけ組む）。
    投入ごとに組み直すと、探索結果の置き場も投入ごとに捨てることになり、測っているのは
    受付の費用ではなく検定の組み立て方になる。
    """
    from simulator.sim_ui.domain.simulation_job import JobStatus

    accepted = 0
    for _ in range(submissions):
        view = interactor.execute(_submission())
        accepted += 1 if view.status == JobStatus.RUNNING.value else 0
    return {"issued": len(build_spy), "accepted": accepted}


def test_the_second_submission_builds_no_ea_again(build_spy) -> None:
    """2 度目以降の投入で組み立てが 1 度も発行されない（発行 − 相異なる ea_name = 0）。"""
    # Arrange
    interactor = _interactor()

    # Act（1 件目で温め、2 件目の発行だけを数える）
    first = _measure(build_spy, interactor, 1)
    build_spy.clear()
    second = _measure(build_spy, interactor, 1)

    # Assert
    assert first["accepted"] == 1              # 空振り防止（現に受理されている）
    assert first["issued"] > 0                 # 空振り防止（現に組み立てている）
    assert second["accepted"] == 1
    assert second["issued"] == 0


def test_the_builds_do_not_grow_with_the_number_of_submissions(build_spy) -> None:
    """投入数を 4 倍にしても組み立ての発行が増えない（オーダーの表明）。

    **回数そのものは焼き込まない**——等しいことだけを表明する。規模 2 点はどちらも
    「冷えた受付器」から始める（温まった置き場を測ると 2 点とも 0 になり、何も表明しない）。
    """
    import simulator.sim_ui.main.composition_root_jobs as root

    # Act
    few = _measure(build_spy, _interactor(), _FEW_SUBMISSIONS)
    build_spy.clear()
    root._REQUIRED_SERIES_CACHE.clear()
    many = _measure(build_spy, _interactor(), _MANY_SUBMISSIONS)

    # Assert
    assert many["accepted"] == few["accepted"] * 4   # 空振り防止（規模が現に違う）
    assert few["issued"] > 0                         # 空振り防止（冷えた状態から測っている）
    assert many["issued"] == few["issued"]
