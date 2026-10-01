"""合流点の保証境界の計算量（絶対命令 2026-08-28・ISSUE-525）。

何を解くか:
    保証境界を合流点へ移すと、**読み手が増える危険**がある。N-17 はデータ実体のヘッダを
    読む（「`supplies_spread`」）。適用点を足しただけなら 1 run で 2 回読むことになり——出力は
    1 ビットも変わらないので状態検証では**原理的に落ちない**（規約が禁じる「作ってから
    捨てる」型）。

固定する不変条件:
    CX-1  1 run あたりの気配幅の問い合わせは「発行 − 使用 = 0」である。使用は「気配幅の
          供給を問う必要があった判定の数」を宣言から導いて数える（回数そのものを期待値へ
          焼き込まない）。
    CX-2  規模を変えても発行は増えない（足の本数 2 点・ファイルの行数で測る）。
    CX-3  「`settings`」 経路と現行経路で発行数が等しい（適用点を 2 つ持っていない）。
    CX-4  気配幅を読まない EA では発行が 0 である（問う必要が無い判定は問わない）。

数え方:
    継ぎ目は `simulator.adapter.repository.ohlc_marketdata_csv.supplies_spread`（保証境界が
    気配幅の供給を問う唯一の口）である。**形式判定（「`detect_ohlc_form`」）の読取は数えない**
    ——それは別の問い（どのリーダで読むか）であり、別の消費者（EA 束縛のデータ供給）が
    持つ。両者を畳んで数えると、保証境界が増やした読取と元からある読取が区別できない。
    その口が 1 回の呼出でヘッダを 2 度読まないことは
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` が持つ。

    **時間は測らない**——閾値はマシン負荷で揺れ、緩んで浪費を通す。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.domain.tester_settings_exceptions import UnsupportedSettingError
from simulator.main import build_interactor
from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.main.unsupported_run_scope import RUN_SCOPE_RULES
from simulator.tests.route_parity_fixtures import (
    MA_SLOPE_EA,
    NO_SL_TP,
    run_kwargs_for,
    write_marketdata_csv,
)
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_NAME,
    engine_binding,
    runnable_settings,
)

#: 規模 2 点（足の本数）。読取がヘッダ 1 行なら本数に依存しない。
_SCALES = (40, 400)


def _asked_for(calls, data_path) -> "list[str]":
    """当該実体について「気配幅を供給するか」を問った発行だけを取り出す。"""
    return [str(args[0]) for args in calls if str(args[0]) == str(data_path)]


def _spreadless(tmp_path, bars: int):
    return write_marketdata_csv(
        tmp_path / f"spreadless_{bars}.csv", with_spread=False, bars=bars
    )


def _issues_when_refused(monkeypatch, tmp_path, *, bars: int, ea_name: str):
    """気配幅を読む EA × 気配幅なしの実体で合流点を 1 回通し、発行を数える。

    拒否は**期待する結果**なので型と ID を明示して捕まえる。例外を握り潰すと、測定対象
    （保証境界）が一度も動かなくても発行 0 のまま緑になる——計算量検定がいちばん危険な形
    （恒真化）であり、ISSUE-532 欠陥 1 で実測済みの型である。
    """
    data_path = _spreadless(tmp_path, bars)
    kwargs = run_kwargs_for(data_path, ea_name=ea_name, **NO_SL_TP)
    calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
    with pytest.raises(UnsupportedSettingError) as caught:
        build_interactor(**kwargs)
    assert caught.value.context["unsupported_id"] == "N-17"
    return _asked_for(calls, data_path)


def _issues_when_admitted(monkeypatch, tmp_path, *, bars: int, ea_name: str):
    """気配幅を読まない EA で合流点を 1 回通し、発行を数える（例外は出ない）。

    例外を捕まえない——出たら落ちる。捕まえると「合流点まで届かなかったので 0 件」を
    「問わなかったので 0 件」と読み違える。
    """
    data_path = _spreadless(tmp_path, bars)
    kwargs = run_kwargs_for(data_path, ea_name=ea_name, **NO_SL_TP)
    calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
    controller, request = build_interactor(**kwargs)
    assert controller is not None and request is not None
    return _asked_for(calls, data_path)


def _uses_of_the_spread_supply(ea_name: str) -> int:
    """「気配幅の供給を問う必要があった判定」の数（＝使用）。

    宣言の並びを読んで数える——`RUN_SCOPE_RULES` のうち、当該 EA について気配幅の供給が
    答えに要る判定の件数である。**回数を焼き込まない**ため、宣言から導く。
    """
    from simulator.main import spread_dependent_ea_names

    if ea_name not in spread_dependent_ea_names():
        return 0
    return sum(
        1 for rule in RUN_SCOPE_RULES if "spread_dependent_ea_names" in rule.reads
    )


class TestIssuedMinusUsedIsZero:
    """CX-1 / CX-4。"""

    def test_a_spread_dependent_ea_reads_the_header_exactly_as_many_times_as_used(
        self, monkeypatch, tmp_path
    ):
        used = _uses_of_the_spread_supply(MA_SLOPE_EA)
        assert used > 0, "気配幅の供給を問う判定が 0 件（前提の崩れ）"
        issued = _issues_when_refused(
            monkeypatch, tmp_path, bars=_SCALES[0], ea_name=MA_SLOPE_EA
        )
        assert len(issued) - used == 0

    def test_an_ea_that_does_not_read_spread_issues_no_header_read(
        self, monkeypatch, tmp_path
    ):
        # CX-4: 問う必要が無い判定は問わない（判定の中で先に打ち切る）。
        assert _uses_of_the_spread_supply(DEFAULT_EA_NAME) == 0
        issued = _issues_when_admitted(
            monkeypatch, tmp_path, bars=_SCALES[0], ea_name=DEFAULT_EA_NAME
        )
        assert issued == []


class TestTheIssueCountDoesNotGrowWithScale:
    """CX-2: 規模 2 点で発行が等しい（オーダーの表明）。"""

    def test_the_issues_do_not_grow_with_the_number_of_bars(
        self, monkeypatch, tmp_path
    ):
        measured = {}
        for bars in _SCALES:
            measured[bars] = len(
                _issues_when_refused(
                    monkeypatch, tmp_path, bars=bars, ea_name=MA_SLOPE_EA
                )
            )
            monkeypatch.undo()
        assert len(set(measured.values())) == 1, measured
        # 空振り防止: 規模が本当に違うこと（同じファイルを 2 回測っていない）。
        small = (tmp_path / f"spreadless_{_SCALES[0]}.csv").read_text(encoding="utf-8")
        large = (tmp_path / f"spreadless_{_SCALES[1]}.csv").read_text(encoding="utf-8")
        assert len(large.splitlines()) > len(small.splitlines())


class TestTheTwoRoutesIssueTheSameNumberOfReads:
    """CX-3: 適用点を 2 つ持っていない。"""

    def test_the_settings_route_issues_the_same_number_of_reads(
        self, monkeypatch, tmp_path
    ):
        # Arrange: 気配幅を供給する実体（拒否されずに合流点を**通り抜ける**ので、
        #   「通った run で 2 回読む」退化を捕まえられる）。
        data_path = write_marketdata_csv(
            tmp_path / "with_spread.csv", with_spread=True, bars=_SCALES[0], spread=5
        )

        # Act: settings 経路
        calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
        exit_code, result, _metadata = run_from_settings(
            runnable_settings(Dates="0", Expert=f"{MA_SLOPE_EA}.ex5"),
            engine_binding(
                data_path=str(data_path),
                config_overrides=None,
                ea_params={
                    "ma_period": 2, "ma_method": "sma", "lot_size": 1.0, **NO_SL_TP
                },
            ),
        )
        assert exit_code == 0 and result is not None, "run が始まっていない（空振り）"
        via_settings = _asked_for(calls, data_path)
        monkeypatch.undo()

        # Act: 現行経路（同じ実体）
        kwargs = run_kwargs_for(data_path, ea_name=MA_SLOPE_EA, **NO_SL_TP)
        calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
        build_interactor(**kwargs)
        via_current = _asked_for(calls, data_path)

        # Assert
        assert len(via_settings) == len(via_current)
        assert len(via_settings) - _uses_of_the_spread_supply(MA_SLOPE_EA) == 0
