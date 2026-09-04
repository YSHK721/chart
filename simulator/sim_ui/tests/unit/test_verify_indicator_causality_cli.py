"""検定 CLI（引数解析・三段実行・台帳生成の形）の単体検定。

**実データ（marketdata）を使う検定実行そのものは pytest に入れない**。データ不在の環境で
無音スキップになり、「テストは緑だが検定は一度も走っていない」を作る。ここで固定するのは
CLI の骨格だけである:

    1. 引数解析（既定値が測定条件の規約どおりか）
    2. 三段実行の形（段 0 供給コスト → 段 1 スクリーニング → 段 2 供給窓で確定）
    3. cost-gating（供給予算超過は案 i を走らせない・reason=supply_cost_exceeded）
    4. 台帳生成の形（schema・測定条件・系列単位・reason 3 値・supply_seconds）
    5. 1 系列の異常で検定全体を止めない（fail-safe な記録）

方式: 全 Port をフェイクへ差し替える（`FakeCausalSeriesProbe` / `FakeCatalogSource` /
`FakeCausalityLedger`）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.main.verify_indicator_causality_cli import main, parse_args, run
from simulator.sim_ui.tests.integration._fake_indicator_ports import (
    FakeCatalogSource,
    FakeCausalSeriesProbe,
    FakeCausalityLedger,
)
from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_SUPPLY_COST_EXCEEDED,
    REASON_VERIFICATION_INCOMPLETE,
    IndicatorSpec,
)

_MA = IndicatorSpec("moving_averages", "default", {"length": 20})
_CVFE = IndicatorSpec("cvfe", "default", {"n_har": 500})
_BARS = [100, 160, 220, 280]


# --- 1. 引数解析 -----------------------------------------------------------

def test_既定の測定条件は裁定どおり() -> None:
    """limit=None 固定・tolerance 0.0・probe_mode full・verify-bars 1000。"""
    # Act
    options = parse_args(["--ref", "jp225_tick", "--timeframe", "5m"])
    # Assert
    assert options.ref == "jp225_tick"
    assert options.timeframe == "5m"
    assert options.supply_bars == 10_000
    assert options.verify_bars == 1_000
    assert options.verify_timeout is None
    assert options.supply_budget == 1.0
    assert options.tolerance == 0.0
    assert options.probe_mode == "full"
    assert options.limit is None


def test_窓と予算を指定できる() -> None:
    # Act
    options = parse_args([
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "200", "--verify-bars", "50",
        "--verify-timeout", "30", "--supply-budget", "0.5", "--tolerance", "1e-9",
    ])
    # Assert
    assert (options.supply_bars, options.verify_bars) == (200, 50)
    assert options.verify_timeout == 30.0
    assert options.supply_budget == 0.5
    assert options.tolerance == 1e-9


def test_対象指標を絞れる() -> None:
    # Act
    options = parse_args([
        "--ref", "jp225_tick", "--indicator", "moving_averages", "--indicator", "cvfe",
    ])
    # Assert
    assert options.indicators == ("moving_averages", "cvfe")


def test_refは必須() -> None:
    """測定条件を欠いたまま台帳を作らせない。"""
    with pytest.raises(SystemExit):
        parse_args(["--timeframe", "5m"])


# --- 2. 三段実行と台帳生成 ------------------------------------------------

def _probe(*, mismatch: bool = False, excluded=None) -> FakeCausalSeriesProbe:
    full = {"MA": [(t, float(t)) for t in _BARS], "MID": [(t, float(t)) for t in _BARS]}
    upto = {
        "MA": {t: float(t) for t in _BARS},
        "MID": {t: float(t) for t in _BARS},
    }
    if mismatch:
        upto["MID"][220] = 999.0
    return FakeCausalSeriesProbe(
        full=full, upto=upto, bars=list(_BARS), excluded=excluded
    )


def _run(*, probe=None, argv=None, specs=None):
    options = parse_args(argv or [
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "4", "--verify-bars", "2",
    ])
    probe = probe if probe is not None else _probe()
    ledger = FakeCausalityLedger()
    snapshot = run(
        options=options, probe=probe,
        catalog=FakeCatalogSource(specs if specs is not None else [_MA, _CVFE]),
        ledger=ledger,
    )
    return snapshot, ledger, probe


def _by_key(snapshot) -> dict:
    return {(f.spec.indicator, f.series_name): f for f in snapshot.findings}


def test_台帳が機械生成される() -> None:
    # Act
    snapshot, ledger, _p = _run()
    # Assert
    assert ledger.writes == [snapshot]
    assert snapshot.schema == 1
    assert snapshot.measured_at


def test_台帳の測定条件は供給窓と検定窓を持つ() -> None:
    # Act
    snapshot, _l, _p = _run()
    # Assert
    conditions = snapshot.conditions
    assert conditions.ref == "jp225_tick"
    assert conditions.timeframe == "5m"
    assert conditions.supply_bars == 4
    assert conditions.verify_bars == 2
    assert conditions.verify_coverage == 1.0
    assert conditions.supply_budget == 1.0
    assert conditions.limit is None
    assert conditions.tolerance == 0.0
    assert conditions.probe_mode == "full"


def test_一致した系列は選択可能になる() -> None:
    # Act
    snapshot, _l, _p = _run()
    # Assert
    assert all(f.selectable for f in snapshot.findings)
    assert {(f.spec.indicator, f.series_name) for f in snapshot.findings} == {
        ("moving_averages", "MA"), ("moving_averages", "MID"),
        ("cvfe", "MA"), ("cvfe", "MID"),
    }


def test_不一致の系列だけが理由つきで選択不可になる() -> None:
    """無音で消さない・使える系列を巻き添えにしない。"""
    # Act
    snapshot, _l, _p = _run(probe=_probe(mismatch=True))
    # Assert
    findings = _by_key(snapshot)
    assert findings[("moving_averages", "MA")].selectable is True
    assert findings[("moving_averages", "MID")].selectable is False
    assert findings[("moving_averages", "MID")].reason == REASON_MISMATCH
    assert findings[("moving_averages", "MID")].first_mismatch_time == 220


def test_段1は少バーで段2は供給窓で検定する() -> None:
    """裁定 C。段 1 の費用は本数の 2 乗で減るため、落ちる系列に段 2 を払わない。"""
    # Act
    _snapshot, _l, probe = _run()
    # Assert（段 1 は先頭 2 本・段 2 は先頭 4 本。指標 2 件ぶん）
    assert probe.upto_calls == [100, 160] + _BARS + [100, 160] + _BARS


def test_段1で全系列が落ちれば段2を走らせない() -> None:
    # Arrange（段 1 の窓（先頭 2 本）の中で不一致にする）
    probe = _probe()
    probe.upto["MA"][160] = 999.0
    probe.upto["MID"][160] = 999.0
    # Act
    snapshot, _l, probe = _run(probe=probe, specs=[_MA])
    # Assert
    assert probe.upto_calls == [100, 160]     # 段 2（4 本）は走っていない
    assert all(f.reason == REASON_MISMATCH for f in snapshot.findings)


def test_段2が確定しなければ段1で一致した事実を残す() -> None:
    """測れたことを台帳から消さない（次の再検定でどこから測り直すかが判らなくなる）。"""
    # Act（段 1 は 1 本＝最後まで測り切る・段 2 は 4 本で予算 0 秒＝途中で打ち切り）
    snapshot, _l, _p = _run(argv=[
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "4", "--verify-bars", "1", "--verify-timeout", "0",
    ], specs=[_MA])
    # Assert
    findings = _by_key(snapshot)
    assert findings[("moving_averages", "MA")].reason == REASON_VERIFICATION_INCOMPLETE
    assert "段 1" in findings[("moving_averages", "MA")].detail


def test_供給所要秒が記録される() -> None:
    """通過条件 3（供給窓での供給時間）の証拠を台帳へ残す。"""
    # Act
    snapshot, _l, _p = _run()
    # Assert
    assert all(f.supply_seconds is not None for f in snapshot.findings)
    assert all(f.supply_seconds >= 0.0 for f in snapshot.findings)


def test_並び順はindicatorとvariantと系列名の昇順() -> None:
    # Act
    snapshot, _l, _p = _run()
    # Assert
    assert [(f.spec.indicator, f.series_name) for f in snapshot.findings] == [
        ("cvfe", "MA"), ("cvfe", "MID"),
        ("moving_averages", "MA"), ("moving_averages", "MID"),
    ]


def test_対象指標を絞ると母集合が狭まる() -> None:
    # Act
    snapshot, _l, _p = _run(argv=[
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "4", "--verify-bars", "2", "--indicator", "cvfe",
    ])
    # Assert
    assert {f.spec.indicator for f in snapshot.findings} == {"cvfe"}


# --- 3. cost-gating（裁定 C 段 0）------------------------------------------

def test_供給予算を超えた指標は案iを走らせない() -> None:
    """落ちると分かっているものに案 i の実費を払わない。"""
    # Act（予算 0 秒＝必ず超過）
    snapshot, _l, probe = _run(argv=[
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "4", "--verify-bars", "2", "--supply-budget", "0",
    ])
    # Assert
    assert probe.upto_calls == []   # 案 i は 1 回も呼ばれない
    assert all(f.reason == REASON_SUPPLY_COST_EXCEEDED for f in snapshot.findings)
    assert all(not f.selectable for f in snapshot.findings)


def test_供給予算超過の詳細に実測秒が残る() -> None:
    # Act
    snapshot, _l, _p = _run(argv=[
        "--ref", "jp225_tick", "--timeframe", "5m",
        "--supply-bars", "4", "--verify-bars", "2", "--supply-budget", "0",
    ])
    # Assert
    finding = snapshot.findings[0]
    assert "予算" in finding.detail
    assert finding.supply_seconds is not None


def test_供給対象外の系列も理由つきで台帳に残る() -> None:
    """裁定 A: 除外系列名を記録する（「指標にその系列が無い」と区別する）。"""
    # Act
    snapshot, _l, _p = _run(probe=_probe(excluded=["LEVEL"]), specs=[_MA])
    # Assert
    excluded = _by_key(snapshot)[("moving_averages", "LEVEL")]
    assert excluded.selectable is False
    assert excluded.reason == REASON_VERIFICATION_INCOMPLETE
    assert "kind" in excluded.detail


# --- 4. 1 系列の異常で全体を止めない --------------------------------------

def test_整列できない系列は理由つきで選択不可になる() -> None:
    # Arrange（供給窓のグリッドに無い時刻の点を混ぜる）
    probe = _probe()
    probe.full["MID"] = [(100, 1.0), (130, 1.5), (160, 2.0), (220, 3.0), (280, 4.0)]
    probe.upto["MID"] = {t: float(t) for t in _BARS}
    # Act
    snapshot, _l, _p = _run(probe=probe, specs=[_MA])
    # Assert
    findings = _by_key(snapshot)
    assert findings[("moving_averages", "MID")].reason == REASON_VERIFICATION_INCOMPLETE
    assert "整列" in findings[("moving_averages", "MID")].detail
    assert findings[("moving_averages", "MA")].selectable is True


def test_比較不能は検定未完了として記録される() -> None:
    """値がずれた（mismatch）と混ぜない。原因の違う 2 事象を同じ顔にしない。"""
    # Arrange（案 i にしか無い系列がある＝同じものを比べていない）
    probe = FakeCausalSeriesProbe(
        full={"MA": [(t, float(t)) for t in _BARS]},
        upto={"MA": {t: float(t) for t in _BARS},
              "GHOST": {t: float(t) for t in _BARS}},
        bars=list(_BARS),
        upto_names=["MA", "GHOST"],
    )
    # Act
    snapshot, _l, _p = _run(probe=probe, specs=[_MA])
    # Assert
    assert all(f.reason == REASON_VERIFICATION_INCOMPLETE for f in snapshot.findings)
    assert all("比較不能" in f.detail for f in snapshot.findings)


def test_供給できない指標も台帳に残る() -> None:
    """境界値: 束の計算そのものが失敗（無音で消さない）。"""
    # Arrange
    class _Broken(FakeCausalSeriesProbe):
        def series_full(self, *args, **kwargs):
            raise RuntimeError("compute が落ちました")

    probe = _Broken(full={}, upto={}, bars=list(_BARS))
    # Act
    snapshot, _l, _p = _run(probe=probe, specs=[_MA])
    # Assert
    finding = snapshot.findings[0]
    assert finding.selectable is False
    assert finding.reason == REASON_VERIFICATION_INCOMPLETE
    assert "compute が落ちました" in finding.detail


# --- 5. エントリポイント ---------------------------------------------------

def test_mainは正常終了で0を返す() -> None:
    # Arrange
    ledger = FakeCausalityLedger()
    # Act
    code = main(
        ["--ref", "jp225_tick", "--timeframe", "5m",
         "--supply-bars", "4", "--verify-bars", "2"],
        probe=_probe(), catalog=FakeCatalogSource([_MA]), ledger=ledger,
    )
    # Assert
    assert code == 0
    assert len(ledger.writes) == 1
