"""TraceWindow: どこを残すか（adapter 層・RUN_TRACE_BASIC_DESIGN §6.4）。

アクター（改訂の動機）: **運用の要求**（期間指定・記録量の抑制）。「何を残すか」
（`simulator/adapter/trace/columnar_run_trace.py`）とも「どう永続化するか」
（`simulator/adapter/trace/parquet_trace_store.py`）とも別の理由で変わるため、
モジュールを分ける。

責務は 1 つ: 「その epoch 秒を残すか」を答えること。半開 `[start, end)` の**規則そのもの**は
持たない——共有実体 `datawindow.half_open.HalfOpenEpochWindow` へ委譲する
（境界規則の第 2 の実装を作らない。先例 `adapter/execution/tick_model.py:185`）。
本型は `HalfOpenEpochWindow` の薄いラッパに純化する。

引数が epoch 秒（int）であって評価点そのもの
（`simulator/usecase/evaluation_point.py`）でない理由（§6.5.0）:
    点 → epoch の導出点は `simulator/adapter/trace/columnar_run_trace.py` ただ 1 つで
    ある。窓判定側と列出力側の 2 箇所で導出すると、「窓が通した点の time 列が窓の外」
    という食い違いが**例外を出さずに**起こる。よって本型は評価点の型を知らない。

`start > end` を自分で弾く理由:
    `HalfOpenEpochWindow` は空窓として黙って `contains=False` を返す仕様であり
    （`datawindow/half_open.py:76-78` に明記・妥当性検査は呼出側の責務）、そこに頼ると
    「窓を間違えたのに 0 行で成功する」run ができる。誤りは値で表さず例外で表す。

依存規律: pandas / pyarrow を import しない（D-5 の隔離）。列は素の `list` で持ち、
`simulator/adapter/trace/parquet_trace_store.py` が受け取って初めて DataFrame 化する。
"""
from __future__ import annotations

from typing import Any

from datawindow.half_open import HalfOpenEpochWindow
from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import ConfigError


class _Unbounded:
    """窓なし（全区間）を表す判定器。

    `HalfOpenEpochWindow` に ±∞ を入れる形は採らない——同型の `start` / `end` は
    epoch 秒（int）が不変条件であり、そこへ `float("inf")` を入れると型契約が壊れる。
    「境界が無い」は境界の値では表さず、**判定器を差し替えて**表す。
    """

    #: 境界を持たないことを値でも読めるようにする（meta.json の記録に使う）。
    start = None
    end = None

    def contains(self, epoch: int) -> bool:
        return True


class TraceWindow:
    """記録対象の期間ゲート。判定は `contains` ただ 1 つ。"""

    __slots__ = ("half_open",)

    def __init__(self, half_open: Any) -> None:
        """``half_open``: 判定器。要求する契約は**次の 3 つすべて**である。

          1. `contains(epoch: int) -> bool` — `contains` が委譲する述語。
          2. `start` — 境界の epoch 秒、または境界を持たないとき `None`。
          3. `end` — 同上。

        2・3 を併記するのは、`bounds` がそれらを読むためである（`HalfOpenEpochWindow`
        は dataclass のフィールドとして、`_Unbounded` は「境界が無い」を値で読める
        ようにするために持つ）。1 だけを契約として書くと、その通りに作った差し替えが
        `bounds`（成果物 trace_meta.json の窓欄）で `AttributeError` になる——
        置換可能性は宣言された契約の範囲でしか保証されない。

        既定値を置かない: 「窓を渡し忘れた」ときに全区間へ倒れると、期間指定を
        取り落とした run が黙って全期間を記録する。組み立ては `of` が担う。
        """
        self.half_open = half_open

    @classmethod
    def of(cls, start: Any, end: Any) -> "TraceWindow":
        """境界対から窓を組む。両方 `None` は窓なし（全区間）。

        事前条件: ``start`` / ``end`` は `EPOCH_CONVERTERS` が扱える時刻表現、または
            **両方**が `None`。
        事後条件: 境界は `simulator.domain.bar_time.epoch_seconds`（単一ソース）で
            epoch 秒へ正規化される。時刻表現の受理集合を本モジュールで列挙し直さない。
        例外: 未対応の表現・片側だけの指定・``start > end`` は `ConfigError`。

        片側だけの指定を受理しない理由: 欠けた側の境界を発明しない限り解釈できず、
            発明すれば「指定していない期間まで記録された（されなかった）」が静かに
            起きる。§7 の「既定値で黙って埋めない」に従い、受付段で落とす。
        """
        if start is None and end is None:
            return cls(_Unbounded())
        # 片側 `None` は `epoch_seconds` が `ConfigError` にする（§6.4「`epoch_seconds()`
        #   を通してから `HalfOpenEpochWindow` を持つ」の素直な帰結であり、ここに
        #   別の判定を書き足さない）。
        start_epoch = epoch_seconds(start)
        end_epoch = epoch_seconds(end)
        if start_epoch > end_epoch:
            raise ConfigError(
                "トレース期間の開始が終了より後です",
                context={"start": start_epoch, "end": end_epoch},
            )
        return cls(HalfOpenEpochWindow(start_epoch, end_epoch))

    def contains(self, epoch: int) -> bool:
        """``epoch`` を記録するか（半開 `[start, end)` の判定は共有実体が持つ）。"""
        return self.half_open.contains(epoch)

    @property
    def bounds(self) -> "tuple[Any, Any]":
        """(start, end) の epoch 秒。窓なしは `(None, None)`（成果物 trace_meta.json 用）。"""
        return self.half_open.start, self.half_open.end
