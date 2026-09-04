"""MemoizedCausalComputePort — **deprecated shim**（adapter 層・Phase 3 F-5・裁定 B）。

**deprecated（ISSUE-479 Wave2 3-2・コーディネータ裁定 2026-09-03）**:
移行先は `simulator/sim_ui/adapter/causal_compute_ports.py` の CausalComputePorts
（3 面を明示委譲で合成し、拾い先となる ``__getattr__`` を持たない）。本番の結線は
そちらへ移した。本クラスは ``__getattr__`` の後方互換のためだけに残っている
（削除は Wave 末尾の承認事項であり、本 Wave では行わない）。

`CausalComputePort`（リプレイ core の Port）の**全メソッド委譲 Decorator**。差は 1 点だけ:

    load_source(ref, timeframe) を (ref, timeframe, csv_mtime) 鍵で記憶する

記憶規則そのものは持たない: 鍵の作り方と記憶の実体は MemoizedSourceLoadPort ただ 1 つで、
本クラスはそこへ委譲する。2 箇所に書くと片方だけが源の更新検知（mtime）を失う。

**式（compute）にも因果規約（truncate）にも触れない**。案 i は 1 バーにつき 1 回
`causal_compute` を呼ぶため、記憶が無いと「毎回同じ CSV を DataFrame から plain dict へ
実体化し直す」費用を全バーぶん払う（実測 2026-08-11: 案 i 0.25 秒/バーのうち load_source が
242ms＝97%）。減らしているのは**捨てられる計算**であり、検定の範囲でも精度でもない。

キャッシュ汚染が起きない根拠（実測ではなくコードの構造）:
    `causal_compute` は受け取ったバー列を `reveal_clock.truncate` に通す。truncate は
    ``[dict(b) for b in bars ...]``（reveal_clock.py:20-22）＝**毎回新しい dict の新しい
    list** を返す。以降の加工（tail・apply_forming・DataFrame 化）はその複製に対して
    行われるため、記憶した列が書き換わることはない。この性質は
    tests/unit/test_memoized_causal_compute_port.py で固定する。

寿命の限定（裁定 B）: 検定 CLI プロセスと 1 ジョブ子プロセスに限る。sim core の常駐へは
注入しない（常駐に載せると CSV 更新の検知が mtime 1 点に集約され、更新の見落としが
そのまま古い値の供給になる）。

LSP: `CausalComputePort` として差し替え可能。呼び出し側は記憶の有無を知らない。
CLEAN_ARCH §6: FS（mtime 取得）は本ファイルに閉じる。
"""
from __future__ import annotations

from typing import Any, Callable

from simulator.sim_ui.adapter.causal_compute_ports import MemoizedSourceLoadPort


class MemoizedCausalComputePort:
    """**deprecated**: `CausalComputePort` の委譲 Decorator（``load_source`` のみ記憶する）。

    移行先は CausalComputePorts（`simulator/sim_ui/adapter/causal_compute_ports.py`）。

    ``inner``: 委譲先の `CausalComputePort`（実体は `CausalComputeGateway`）。
    ``mtime_of``: ``(ref) -> float | None``。源 CSV の更新時刻。既定はデータセット
      ホワイトリストから解決する（取得できないときは ``None``＝鍵の一部として
      「不明」を保持する。不明のまま更新されると記憶が古くなるため、寿命を
      短命プロセスに限る前提とセットで成り立つ）。
    """

    def __init__(self, *, inner: Any, mtime_of: "Callable[[str], float | None] | None" = None) -> None:
        self._inner = inner
        #: 記憶規則の唯一の実体（鍵の作り方をここへ書き写さない）。
        self._loader = MemoizedSourceLoadPort(inner=inner, mtime_of=mtime_of)

    # --- 記憶するメソッド（規則は _loader が持つ）--------------------------

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        return self._loader.load_source(ref, timeframe)

    @property
    def hits(self) -> int:
        """記憶が効いた回数（実測の証拠として検定・CLI が読む）。"""
        return self._loader.hits

    @property
    def misses(self) -> int:
        """内側へ読みに行った回数。"""
        return self._loader.misses

    @property
    def _cache(self) -> "dict[tuple[str, str | None, float | None], list[dict]]":
        """記憶表（後方互換の読み口。実体は _loader が持つ）。"""
        return self._loader._cache

    # --- 委譲するメソッド（`CausalComputePort` の面を欠かさない）-----------

    def bar_time(self, timeframe: str, unix_sec: int) -> int:
        return self._inner.bar_time(timeframe, unix_sec)

    def period_start(self, timeframe: str, unix_sec: int) -> int:
        return self._inner.period_start(timeframe, unix_sec)

    def causal_series(
        self, indicator: str, variant: str, chart_bars: "list[dict]",
        source_bars: "list[dict]", compute_tf: str, window_bars: "list[dict]", params: dict,
    ) -> "list[dict]":
        return self._inner.causal_series(
            indicator, variant, chart_bars, source_bars, compute_tf, window_bars, params
        )

    def compute(
        self, indicator: str, variant: str, mode: str, bars: "list[dict]", params: dict
    ) -> "list[dict]":
        return self._inner.compute(indicator, variant, mode, bars, params)

    def compute_latest_seq(
        self, indicator: str, variant: str, prefix_bars: "list[dict]",
        tails: "list[list[dict]]", params: dict,
    ) -> "list[list[dict]]":
        return self._inner.compute_latest_seq(
            indicator, variant, prefix_bars, tails, params
        )

    def __getattr__(self, name: str) -> Any:
        """明示していない面も内側へ委譲する（Port が増えても穴を空けない）。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
