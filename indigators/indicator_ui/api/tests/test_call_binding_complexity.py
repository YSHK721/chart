"""call_binding 経路の**計算量テスト**（CLAUDE.md 絶対命令 2026-08-28・ISSUE-479 Wave2 I-1）。

固定するのは出力の正しさではなく **無駄の不在** である。

    発行した計算 − 出力に使った計算 = 0

「作ってから捨てる」欠陥は出力が正しいままなので、状態検証では原理的に落ちない。
回数そのもの（「N 回呼ばれること」）は期待値に焼き込まない（浪費が仕様へ昇格するため）。
固定するのは次の 3 点だけ:

  C1: src パッケージの exec 発行 − 実際に使う src の種類数 = 0（同じ src を二度 exec しない）。
  C2: 束縛の signature 解析の発行 − 束縛した callable 数 = 0（param 数を増やしても増えない）。
  C3: interval 適応の min/max 発行 − 使った統計量の数 = 0（本数を増やしても増えない）。
  C4: 負の対照 — 二重取りに変異させると C3 の検査が赤になる（検出力の実測）。

構造: Arrange-Act-Assert（AAA）。
"""

from __future__ import annotations

import uuid

import pytest

import common.module_loader as common_loader
from adapter.compute import call_binding
from adapter.compute.bindings import price_range_power as prp


# --------------------------------------------------------------------------- #
# C1: 指標 src の exec 発行 − 使う src の種類数 = 0
# --------------------------------------------------------------------------- #
class _ExecSpy:
    """パッケージ本体の exec（キャッシュ不命中）を数える Test Spy。"""

    def __init__(self, original):
        self._original = original
        self.execs: list[str] = []

    def __call__(self, name, pkg_dir):
        self.execs.append(name)
        return self._original(name, pkg_dir)


def _isolated_src_namespace(monkeypatch) -> None:
    """本テスト専用の一意パッケージ名前空間へ切り替える（他テストのキャッシュ状態に依存しない）。"""
    monkeypatch.setattr(
        call_binding, "_SRC_MODULE_PREFIX", f"_cx{uuid.uuid4().hex[:8]}_", raising=True
    )


@pytest.mark.parametrize("requests", [1, 2, 5])
def test_src_package_exec_issued_never_exceeds_the_kinds_of_src_used(monkeypatch, requests):
    # Arrange: exec 点を spy し、要求のたびに exec されないことを測れるようにする。
    spy = _ExecSpy(common_loader._load_package_locked)
    monkeypatch.setattr(common_loader, "_load_package_locked", spy)
    _isolated_src_namespace(monkeypatch)

    # Act: 同一 src を複数回要求する（invoke は loader と fitter で同じ src を要求しうる）。
    used = set()
    for _ in range(requests):
        call_binding._load_src_package("moving_averages")
        used.add("moving_averages")

    # Assert: 発行した exec − 使った src の種類数 = 0（要求数では増えない＝オーダーの表明）。
    assert len(spy.execs) - len(used) == 0, (
        f"同じ src を繰り返し exec している（要求 {requests} 回で exec {len(spy.execs)} 回）"
    )


def test_btlm_invoke_execs_each_needed_src_once(monkeypatch):
    """invoke（btlm）は loader と fitter で同じ src を要求するが exec は種類数どまり。"""
    from adapter.compute import FakeLineChart

    import numpy as np
    import pandas as pd

    spy = _ExecSpy(common_loader._load_package_locked)
    monkeypatch.setattr(common_loader, "_load_package_locked", spy)
    _isolated_src_namespace(monkeypatch)

    n = 60
    base = 100.0 + np.sin(np.linspace(0.0, 6.0, n))
    df = pd.DataFrame({
        "open": base, "high": base + 1.0, "low": base - 1.0, "close": base + 0.5,
        "volume": np.full(n, 10.0),
        "time": pd.date_range("2024-01-01", periods=n, freq="h"),
    })
    binding = call_binding.CallBinding.resolve("tgp_btlm", "default")
    binding.invoke(FakeLineChart(), df, {"fitter": "ols", "maxbars": 40,
                                         "q_low": 0.05, "q_high": 0.95})

    used = set(spy.execs)
    assert len(spy.execs) - len(used) == 0, (
        f"同一 src が複数回 exec された: {spy.execs}"
    )


# --------------------------------------------------------------------------- #
# C2: signature 解析の発行 − 束縛した callable 数 = 0（param 数に依らない）
# --------------------------------------------------------------------------- #
class _SignatureSpy:
    def __init__(self, original):
        self._original = original
        self.calls: list[object] = []

    def __call__(self, callable_):
        self.calls.append(callable_)
        return self._original(callable_)


def _free_form(chart, df, **kwargs):  # pragma: no cover - 束縛先のダミー
    return None


@pytest.mark.parametrize("n_params", [5, 20])
def test_kwarg_binding_signature_lookups_do_not_grow_with_param_count(monkeypatch, n_params):
    # Arrange
    spy = _SignatureSpy(call_binding.accepted_param_names)
    monkeypatch.setattr(call_binding, "accepted_param_names", spy)
    params = {f"p{i}": i for i in range(n_params)}

    # Act
    bound = call_binding._bind_kwargs(_free_form, params)

    # Assert: 発行した signature 解析 − 束縛した callable の種類数 = 0。
    assert len(spy.calls) - len(set(map(id, spy.calls))) == 0
    assert len(spy.calls) - 1 == 0, "param 数を増やすと signature 解析が増えている"
    assert bound == params


# --------------------------------------------------------------------------- #
# C3 / C4: interval 適応の min/max 発行 − 使った統計量の数 = 0
# --------------------------------------------------------------------------- #
class _StatSpy:
    """``df[col].min()/.max()`` の発行を数える最小の DataFrame 代役。"""

    def __init__(self, values: dict[str, list[float]]):
        self._values = values
        self.issued: list[str] = []

    @property
    def columns(self):
        return list(self._values)

    def __getitem__(self, col):
        outer = self

        class _Series:
            def min(self):
                outer.issued.append(f"{col}.min")
                return min(outer._values[col])

            def max(self):
                outer.issued.append(f"{col}.max")
                return max(outer._values[col])

        return _Series()


def _spy_df(bars: int) -> _StatSpy:
    lows = [40000.0 + i for i in range(bars)]
    highs = [46000.0 + i for i in range(bars)]
    return _StatSpy({"open": lows, "high": highs, "low": lows, "close": highs})


@pytest.mark.parametrize("bars", [80, 640])
def test_interval_adaptation_issues_one_min_and_one_max_regardless_of_bar_count(bars):
    # Arrange
    df = _spy_df(bars)

    # Act
    out = prp.adapt_interval(df, {"interval": 0.1})

    # Assert: 使った統計量（下限 1・上限 1）と発行が一致し、本数では増えない。
    used = {"low.min", "high.max"}
    assert set(df.issued) == used
    assert len(df.issued) - len(used) == 0, f"統計量を取り直している: {df.issued}"
    assert out > 0.1  # 適応が効いた（＝統計量が実際に出力へ使われた）


def test_interval_adaptation_issues_no_statistics_when_range_is_given():
    """range_from/range_to 指定時は df 統計量を発行しない（使わない計算を作らない）。"""
    df = _spy_df(80)
    prp.adapt_interval(df, {"interval": 0.1, "range_from": 40000.0, "range_to": 46000.0})
    assert df.issued == []


def test_complexity_gate_detects_a_double_fetch_mutation():
    """負の対照: min/max を二重取りする変異は C3 の検査で赤になる（検出力の実測）。"""

    def mutated_adapt_interval(df, kw):
        cols = {str(c).lower(): c for c in df.columns}
        _warmup_lo = float(df[cols["low"]].min())  # 捨てられる発行（浪費の再現）
        _warmup_hi = float(df[cols["high"]].max())
        return prp.adapt_interval(df, kw)

    df = _spy_df(80)
    mutated_adapt_interval(df, {"interval": 0.1})
    used = {"low.min", "high.max"}
    assert len(df.issued) - len(used) != 0, "変異を検出できていない（検査が空振り）"
