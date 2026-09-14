"""計算源ロードの記憶と、3 面の明示合成（adapter 層・ISSUE-479 Wave2 3-2 / S-5）。

本ファイルは 2 つの実装を持つ。どちらも規則を持たず、**役割の境界**だけを表現する。

``MemoizedSourceLoadPort``
    SourceLoadPort（ロード面）**だけ**の実装。``load_source(ref, timeframe)`` を
    ``(ref, timeframe, csv_mtime)`` を鍵に記憶する。式（compute）にも因果規約（truncate）にも
    触れない——触れないことが、実装している面が 1 つであることによって型に現れる。

``CausalComputePorts``
    3 面（ロード面 / 時間足グリッド面 / 指標計算面）を受け取り、CausalComputePort の
    6 メソッドを**明示委譲**する合成。拾い先（属性の動的フォールバック）を**持たない**。

なぜ属性の動的フォールバックを置かないか（ISSUE-479 S-5 の核心）:
    「明示していない面も内側へ委譲する」フォールバックは、委譲の書き忘れを実行時まで隠す。
    面を 1 つ書き落としても拾われてしまうため、検定は緑のまま「明示委譲したつもり」の状態が
    残る。分割の目的は「どの面を持つかが読めば分かる」ことなので、拾い先を無くす。
    実測（対照実験・2026-09-03）: 両者へ同一の変異（``period_start`` の明示委譲を落とす）を
    入れて比べた。
      旧 shim（動的フォールバックあり）: ``period_start('1D', 100)`` は **99 を返して成功**した。
        委譲の欠落が振る舞いに現れず、面の委譲を確かめる既存検定も**緑のまま**通った
        （気づいたのは runtime Protocol の isinstance 検定だけで、それも「型として通らない」
        と言うだけであり、どの面が落ちたかは呼び出すまで分からない）。
      本合成（フォールバックなし）: 呼び出しそのものが ``AttributeError`` で落ち、
        「どの面の委譲が無いか」が失敗地点として直接出る。

記憶の効果（なぜ要るか・実測 2026-08-11）:
    案 i は 1 バーにつき 1 回 causal_compute を呼ぶため、記憶が無いと「毎回同じ CSV を
    DataFrame から plain dict へ実体化し直す」費用を全バーぶん払う（案 i 0.25 秒/バーのうち
    ``load_source`` が 242ms＝97%）。減らしているのは**捨てられる計算**であり、検定の範囲でも
    精度でもない。

キャッシュ汚染が起きない根拠（実測ではなくコードの構造）:
    causal_compute は受け取ったバー列を reveal_clock.truncate に通す。truncate は
    ``[dict(b) for b in bars ...]``（reveal_clock.py:20-22）＝**毎回新しい dict の新しい
    list** を返す。以降の加工はその複製に対して行われるため、記憶した列が書き換わることはない。

寿命の限定（裁定 B）: 検定 CLI プロセスと 1 ジョブ子プロセスに限る。sim core の常駐へは
注入しない（常駐に載せると CSV 更新の検知が mtime 1 点に集約され、更新の見落としが
そのまま古い値の供給になる）。

CLEAN_ARCH §6: FS（mtime 取得）は本ファイルに閉じる。
"""
from __future__ import annotations

from typing import Any, Callable


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


class MemoizedSourceLoadPort:
    """ロード面だけの SourceLoadPort 実装（``load_source`` を鍵つきで記憶する）。

    ``inner``: 委譲先（ロード面を持つ実体。本番は `CausalComputeGateway`）。
    ``mtime_of``: ``(ref) -> float | None``。源 CSV の更新時刻。既定はデータセット
      ホワイトリストから解決する（取得できないときは ``None``＝鍵の一部として
      「不明」を保持する。不明のまま更新されると記憶が古くなるため、寿命を
      短命プロセスに限る前提とセットで成り立つ）。
    """

    def __init__(
        self, *, inner: Any, mtime_of: "Callable[[str], float | None] | None" = None
    ) -> None:
        self._inner = inner
        self._mtime_of = mtime_of if mtime_of is not None else _dataset_mtime
        self._cache: "dict[tuple[str, str | None, float | None], list[dict]]" = {}
        #: 記憶が効いたか（実測の証拠として検定・CLI が読む）。
        self.hits = 0
        self.misses = 0

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


class CausalComputePorts:
    """3 面を明示委譲で合成した CausalComputePort 実装（動的フォールバックを持たない）。

    ``source_load``: ロード面（SourceLoadPort）。本番は :class:`MemoizedSourceLoadPort`。
    ``timeframe_grid``: 時間足グリッド面（TimeframeGridPort）。
    ``indicator_compute``: 指標計算面（IndicatorComputePort）。

    3 つに同じ実体を渡してもよい（記憶を挟まない構成）。本番の結線は「ロード面だけ記憶した
    実体 ＋ 素の実体 2 面」で、記憶が式に触れないことが結線の形として見える。
    """

    def __init__(
        self, *, source_load: Any, timeframe_grid: Any, indicator_compute: Any
    ) -> None:
        self._source_load = source_load
        self._timeframe_grid = timeframe_grid
        self._indicator_compute = indicator_compute

    # --- ロード面 ---------------------------------------------------------

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        return self._source_load.load_source(ref, timeframe)

    # --- 時間足グリッド面 -------------------------------------------------

    def bar_time(self, timeframe: str, unix_sec: int) -> int:
        return self._timeframe_grid.bar_time(timeframe, unix_sec)

    def period_start(self, timeframe: str, unix_sec: int) -> int:
        return self._timeframe_grid.period_start(timeframe, unix_sec)

    # --- 指標計算面 -------------------------------------------------------

    def causal_series(
        self, indicator: str, variant: str, chart_bars: "list[dict]",
        source_bars: "list[dict]", compute_tf: str, window_bars: "list[dict]", params: dict,
    ) -> "list[dict]":
        return self._indicator_compute.causal_series(
            indicator, variant, chart_bars, source_bars, compute_tf, window_bars, params
        )

    def compute(
        self, indicator: str, variant: str, mode: str, bars: "list[dict]", params: dict
    ) -> "list[dict]":
        return self._indicator_compute.compute(indicator, variant, mode, bars, params)

    def compute_latest_seq(
        self, indicator: str, variant: str, prefix_bars: "list[dict]",
        tails: "list[list[dict]]", params: dict,
    ) -> "list[list[dict]]":
        return self._indicator_compute.compute_latest_seq(
            indicator, variant, prefix_bars, tails, params
        )


def memoized_causal_compute_ports(
    *, inner: Any, mtime_of: "Callable[[str], float | None] | None" = None
) -> CausalComputePorts:
    """本番の結線: ロード面だけを記憶し、残る 2 面は素の実体へ委ねる合成を返す。

    束縛点を関数にしてあるのは、検定 CLI と将来の子プロセスが同じ組み立てを書き写さない
    ようにするためである（組み立ての規則を 2 箇所に持たない）。
    """
    return CausalComputePorts(
        source_load=MemoizedSourceLoadPort(inner=inner, mtime_of=mtime_of),
        timeframe_grid=inner,
        indicator_compute=inner,
    )
