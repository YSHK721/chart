"""MemoizedCausalComputePort — 計算源ロードの記憶（adapter 層・Phase 3 F-5・裁定 B）。

`CausalComputePort`（リプレイ core の Port）の**全メソッド委譲 Decorator**。差は 1 点だけ:

    load_source(ref, timeframe) を (ref, timeframe, csv_mtime) 鍵で記憶する

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


class MemoizedCausalComputePort:
    """`CausalComputePort` の委譲 Decorator（``load_source`` のみ記憶する）。

    ``inner``: 委譲先の `CausalComputePort`（実体は `CausalComputeGateway`）。
    ``mtime_of``: ``(ref) -> float | None``。源 CSV の更新時刻。既定はデータセット
      ホワイトリストから解決する（取得できないときは ``None``＝鍵の一部として
      「不明」を保持する。不明のまま更新されると記憶が古くなるため、寿命を
      短命プロセスに限る前提とセットで成り立つ）。
    """

    def __init__(self, *, inner: Any, mtime_of: "Callable[[str], float | None] | None" = None) -> None:
        self._inner = inner
        self._mtime_of = mtime_of if mtime_of is not None else _dataset_mtime
        self._cache: "dict[tuple[str, str | None, float | None], list[dict]]" = {}
        #: 記憶が効いたか（実測の証拠として検定・CLI が読む）。
        self.hits = 0
        self.misses = 0

    # --- 記憶するメソッド -------------------------------------------------

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        key = (ref, timeframe, self._mtime_of(ref))
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        bars = self._inner.load_source(ref, timeframe)
        self._cache[key] = bars
        return bars

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


def _dataset_mtime(ref: str) -> "float | None":
    """源 CSV の更新時刻（解決できないときは ``None``）。

    データセット → ファイルの対応はライブ側 `marketdata.dataset` のホワイトリストが
    唯一源であり、ここに写さない。
    """
    try:
        from marketdata.dataset import DATASET_WHITELIST

        return DATASET_WHITELIST[ref].stat().st_mtime
    except Exception:  # noqa: BLE001 — 未知 ref・不在ファイルは「不明」として扱う
        return None
