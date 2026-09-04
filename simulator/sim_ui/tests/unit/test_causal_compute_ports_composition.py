"""3 面を明示合成した CausalComputePort 実装の検定（ISSUE-479 Wave2 3-2・S-5）。

なぜ合成へ変えるのか（現行の穴）:
    現行の記憶 Decorator は「明示していない面も内側へ委譲する」属性の動的フォールバック（catch-all 委譲）を持つ。
    便利に見えるが、**委譲の書き忘れを実行時まで隠す**。面を 1 つ書き落としても
    catch-all 委譲が拾ってしまうため、テストは緑のまま「明示委譲したつもり」の状態が
    残る。分割の意味は「どの面を持つかが読めば分かる」ことなので、拾い先を無くす。

新しい構造:
    MemoizedSourceLoadPort   ロード面だけを実装し、その面だけを記憶する（1 メソッド）
    CausalComputePorts       3 面を受け取り 6 メソッドを明示委譲する（catch-all 委譲なし）

    本番の結線は「ロード面だけ記憶した実体 ＋ 素の実体 2 面」を合成する。記憶が式にも
    因果規約にも触れないことが、型の上で見えるようになる。

旧名の扱い（コーディネータ裁定 2026-09-03）:
    ``MemoizedCausalComputePort`` はクラスのまま **deprecated shim** として残す
    （catch-all 委譲を保持するため既存検定は 1 行も変えずに緑）。記憶規則の実体は
    MemoizedSourceLoadPort ただ 1 つで、shim はそこへ委譲する（第 2 実装を作らない）。
    shim の削除は Wave 末尾の承認保留リスト。

計算量検定（絶対命令 2026-08-28）: 同一鍵 (ref, timeframe, mtime) の 2 回目以降は内側への
    発行が 0（発行 − 実際に必要な読込 = 0）。呼び出し 2 回 / 16 回の 2 点で、発行が
    「異なる鍵の数」だけで決まることを固定する。回数リテラルは焼き込まない。
"""

from __future__ import annotations

import pytest

from simulator.replay_ui.usecase.replay_ports import (
    CausalComputePort,
    IndicatorComputePort,
    SourceLoadPort,
    TimeframeGridPort,
)
from simulator.sim_ui.adapter.causal_compute_ports import (
    CausalComputePorts,
    MemoizedSourceLoadPort,
)
from simulator.sim_ui.adapter.memoized_causal_compute_port import (
    MemoizedCausalComputePort,
)

_BARS = [
    {"time": 100, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
    {"time": 160, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0},
]


class _FakeInner:
    """内側の実体（呼ばれ方だけを記録する）。"""

    def __init__(self) -> None:
        self.load_calls: "list[tuple[str, str | None]]" = []
        self.calls: "list[str]" = []

    def load_source(self, ref, timeframe):
        self.load_calls.append((ref, timeframe))
        return [dict(b) for b in _BARS]

    def bar_time(self, timeframe, unix_sec):
        self.calls.append("bar_time")
        return int(unix_sec)

    def period_start(self, timeframe, unix_sec):
        self.calls.append("period_start")
        return int(unix_sec) - 1

    def causal_series(self, indicator, variant, chart_bars, source_bars, compute_tf,
                      window_bars, params):
        self.calls.append("causal_series")
        return [{"name": indicator}]

    def compute(self, indicator, variant, mode, bars, params):
        self.calls.append("compute")
        return [{"name": indicator, "mode": mode, "bars": len(bars)}]

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        self.calls.append("compute_latest_seq")
        return [[{"name": indicator}] for _ in tails]

    def extra_face(self):
        self.calls.append("extra_face")
        return "ok"


def _loader(inner=None, mtime=1.0) -> MemoizedSourceLoadPort:
    return MemoizedSourceLoadPort(inner=inner or _FakeInner(), mtime_of=lambda _ref: mtime)


def _ports(inner=None, mtime=1.0) -> CausalComputePorts:
    inner = inner or _FakeInner()
    return CausalComputePorts(
        source_load=_loader(inner, mtime),
        timeframe_grid=inner,
        indicator_compute=inner,
    )


# --------------------------------------------------------------------------------------
# 1. ロード面だけの実装（ISP）
# --------------------------------------------------------------------------------------
def test_the_memoized_loader_implements_only_the_load_face() -> None:
    """記憶は式にも因果規約にも触れない——それを型の上で見えるようにする。"""
    loader = _loader()
    assert isinstance(loader, SourceLoadPort)
    assert not isinstance(loader, TimeframeGridPort)
    assert not isinstance(loader, IndicatorComputePort)
    assert not isinstance(loader, CausalComputePort)


def test_the_memoized_loader_reads_once_per_key() -> None:
    inner = _FakeInner()
    loader = _loader(inner)
    first = loader.load_source("jp225", "5m")
    second = loader.load_source("jp225", "5m")
    assert inner.load_calls == [("jp225", "5m")]
    assert second is first


def test_the_memoized_loader_rereads_when_the_source_changed() -> None:
    """源 CSV が更新されたのに古い列を配ると、供給が黙って過去の値になる。"""
    inner = _FakeInner()
    clock = {"mtime": 1.0}
    loader = MemoizedSourceLoadPort(inner=inner, mtime_of=lambda _ref: clock["mtime"])
    loader.load_source("jp225", "5m")
    clock["mtime"] = 2.0
    loader.load_source("jp225", "5m")
    assert len(inner.load_calls) == 2


# --------------------------------------------------------------------------------------
# 2. 合成（明示委譲・拾い先を持たない）
# --------------------------------------------------------------------------------------
def test_the_composition_declares_every_port_method_explicitly() -> None:
    """合併 Protocol の全面が、クラス本体に**書かれている**こと（継承や動的解決に頼らない）。"""
    declared = {name for name in vars(CausalComputePorts) if not name.startswith("__")}
    missing = sorted(set(CausalComputePort.__protocol_attrs__) - declared)
    assert missing == []


def test_the_composition_has_no_catch_all_delegation() -> None:
    """catch-all 委譲を持たない（委譲の書き忘れを実行時まで隠さない）。"""
    assert "__getattr__" not in vars(CausalComputePorts)


def test_an_undeclared_face_is_not_silently_forwarded() -> None:
    """対照: 現行 shim は拾うが、合成は拾わずその場で落ちる。"""
    inner = _FakeInner()
    assert MemoizedCausalComputePort(inner=inner, mtime_of=lambda _r: 1.0).extra_face() == "ok"
    with pytest.raises(AttributeError):
        _ports(inner).extra_face()


def test_the_composition_passes_as_the_merged_port() -> None:
    """LSP: 呼び出し側は合成であることを知らない。"""
    assert isinstance(_ports(), CausalComputePort)


def test_every_face_is_routed_to_its_own_collaborator() -> None:
    """6 メソッドがそれぞれの面へ届く（合成の結線そのもの）。"""
    inner = _FakeInner()
    ports = _ports(inner)
    ports.load_source("jp225", "5m")
    ports.bar_time("1D", 100)
    ports.period_start("1D", 100)
    ports.causal_series("ma", "default", [], [], "1D", [], {})
    ports.compute("ma", "default", "full", _BARS, {})
    ports.compute_latest_seq("ma", "default", [], [[]], {})
    assert inner.load_calls == [("jp225", "5m")]
    assert inner.calls == [
        "bar_time", "period_start", "causal_series", "compute", "compute_latest_seq"
    ]


# --------------------------------------------------------------------------------------
# 3. 記憶規則の単一ソース（旧 shim は委譲するだけ）
# --------------------------------------------------------------------------------------
def test_the_deprecated_shim_delegates_the_memo_rule_instead_of_copying_it() -> None:
    """鍵の作り方を 2 箇所に書くと、片方だけが源の更新検知を失う。"""
    shim = MemoizedCausalComputePort(inner=_FakeInner(), mtime_of=lambda _r: 1.0)
    assert isinstance(shim._loader, MemoizedSourceLoadPort)


def test_the_deprecated_shim_is_marked_as_deprecated() -> None:
    """移行先が docstring から辿れること（消し方を知らないまま残らない）。"""
    doc = MemoizedCausalComputePort.__doc__ or ""
    assert "deprecated" in doc.lower()
    assert "CausalComputePorts" in doc


# --------------------------------------------------------------------------------------
# 4. 本番結線（合成を使う）
# --------------------------------------------------------------------------------------
def test_the_cli_composition_root_wires_the_explicit_composition() -> None:
    """検定 CLI は拾い先の無い合成を使う（shim は互換のためだけに残す）。"""
    from simulator.sim_ui.main import verify_indicator_causality_cli as cli

    probe = cli._default_probe()
    assert isinstance(probe._compute_port, CausalComputePorts)


# --------------------------------------------------------------------------------------
# 5. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("calls_requested", [2, 16], ids=["call_2", "call_16"])
def test_the_loader_issues_one_read_per_distinct_key(calls_requested: int) -> None:
    """呼び出し 2 回 / 16 回の 2 点で「内側への発行 − 異なる鍵の数 = 0」。

    回数を焼き込まず、「同じ鍵を何度引いても読み直しが増えない」ことだけを固定する。
    """
    # Arrange
    inner = _FakeInner()
    loader = _loader(inner)
    keys = [("jp225", "5m"), ("jp225", "1m")]
    # Act
    for i in range(calls_requested):
        loader.load_source(*keys[i % len(keys)])
    # Assert
    assert len(inner.load_calls) - len(keys) == 0, inner.load_calls


def test_the_read_count_does_not_grow_with_repeated_calls() -> None:
    """オーダーの表明: 呼び出し回数を増やしても発行は異なる鍵の数で頭打ちになる。"""
    inner = _FakeInner()
    loader = _loader(inner)
    measured = {}
    for calls in (2, 16):
        inner.load_calls.clear()
        loader._cache.clear()
        for _ in range(calls):
            loader.load_source("jp225", "5m")
        measured[calls] = len(inner.load_calls)
    assert measured[2] == measured[16], measured


def test_dropping_the_mtime_from_the_key_would_miss_a_source_update() -> None:
    """鍵から mtime を落とす変異の検出力（更新の見落としが古い値の供給になる）。"""
    inner = _FakeInner()
    clock = {"mtime": 1.0}
    loader = MemoizedSourceLoadPort(inner=inner, mtime_of=lambda _ref: clock["mtime"])
    loader.load_source("jp225", "5m")
    clock["mtime"] = 2.0
    loader.load_source("jp225", "5m")
    keys_seen = {("jp225", "5m", 1.0), ("jp225", "5m", 2.0)}
    assert set(loader._cache) == keys_seen
