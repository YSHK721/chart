"""ColumnarRunTrace: 何を残すか（点粒度）（adapter 層・RUN_TRACE_BASIC_DESIGN §6.1/§6.5.0）。

アクター（改訂の動機）: **評価点 1 つを何列で残すか**。「どこを残すか」
（`simulator/adapter/trace/trace_window.py`）とも「どう永続化するか」
（`simulator/adapter/trace/parquet_trace_store.py`）とも別の理由で変わる。

記録しないもの（§6.2・複製禁止）:
    * **指標値を毎点コピーしない**。指標は足単位であり、毎点複写は同じ値をティック本数ぶん
      重複させるだけである。指標は `bar_index` を鍵に
      `simulator/adapter/trace/indicator_trace.py` が足単位で残す。
    * **trades / deals を写さない**。既に成果物 report.json と stats.json が持つ。写せば
      同じ事実の 2 経路が静かに食い違う。

`time` 列の出所（§6.5.0・**導出点はこのクラスただ 1 つ**）:
    点の epoch 時刻は `point.tick_time`、それが `None` のときは `point.bar.time`。
    `None` は値からの推定対象ではなく、**契約上宣言された不在**（ティックを持たない点＝
    `simulator/usecase/bar_schedule.py` の点・ティック 0 件バーの持ち越し点）である。
    §5.1 で撤回した「合成か実かの判別子」とは別物であり、判別材料の要らない充当である。

    得た epoch 値は `time` 列にも `window.contains(epoch)` にも**同じものを渡す**。
    窓判定側と列出力側の 2 箇所で導出すると、「窓が通した点の `time` 列が窓の外」という
    食い違いが**例外を出さずに**起こる。よって `TraceWindow.contains` の引数は
    評価点（`simulator/usecase/evaluation_point.py`）ではなく epoch 秒（int）である。

    型の正規化規則は `simulator.domain.bar_time.epoch_seconds` が単一ソースであり、
    受理集合（epoch 整数 / numpy.datetime64 / datetime）を本モジュールで列挙し直さない。

`observe` に `try` / `except` を置かない（§7.0 の裁定）:
    例外を送出しないことは**実装側の義務**であり、エンジンは握らない。本実装が run 中に
    行うのは列への append だけで（永続化は run 完了後・§6.5）、送出する正当な理由が無い。
    唯一の例外源は「時刻表現が受理集合の外」という契約違反であり、それは隠すべきではない。

依存規律: pandas / pyarrow を import しない（D-5 の隔離）。列は素の `list` で持ち、
`simulator/adapter/trace/parquet_trace_store.py` が受け取って初めて DataFrame 化する。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.run_trace_ports import RunTracePort

#: 記録列の**宣言**（§6.1 の 4 群）。成果物 trace_meta.json はこれを読むだけにする
#: （列の意味を writer へ書き写した時点で 2 箇所化する・D-3b）。
COLUMNS: "tuple[str, ...]" = (
    # 時刻・位置
    "time", "bar_index", "tick_ordinal", "granularity", "is_synthetic",
    # クォート
    "eval_bid", "eval_ask",
    # 口座
    "balance", "equity", "floating_pnl", "margin", "margin_level", "swap", "commission",
    # 保有
    "open_count", "open_volume_buy", "open_volume_sell",
    # 状態
    "halted",
)

#: 行の主キー（`(bar_index, tick_ordinal)`）。段階 4 の分析面が読む宣言。
PRIMARY_KEY: "tuple[str, ...]" = ("bar_index", "tick_ordinal")

#: 買い側の呼称（`Position.side` の語彙。`simulator.domain._shared.SIDES` と同じ語）。
_BUY = "buy"


class ColumnarRunTrace(RunTracePort):
    """評価点 1 つ 1 行の列集合を持つ `RunTracePort` 実装。"""

    __slots__ = ("_window", "columns")

    def __init__(self, window: Any) -> None:
        """``window``: 期間ゲート（`simulator/adapter/trace/trace_window.py` の `TraceWindow`）。
        要求する契約は**次の 2 つ**である。

          1. `contains(epoch: int) -> bool` — 記録するかを答える（`observe` が毎点使う）。
          2. `bounds -> (start, end)` — 用いた窓（`window_bounds` が成果物 trace_meta.json
             へ渡す。境界を持たない窓は `(None, None)`）。

        2 を併記するのは、1 だけを契約として書くと、その通りに作った差し替えが
        `window_bounds` で `AttributeError` になるためである（置換可能性は宣言された
        契約の範囲でしか保証されない）。

        既定値を置かない: 窓を渡し忘れた run が黙って全期間を記録する形を作らない
        （組み立ては Composition Root が担う）。
        """
        self._window = window
        self.columns: "dict[str, list]" = {name: [] for name in COLUMNS}

    # --- RunTracePort ----------------------------------------------------

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        """1 評価点を 1 行として記録する（窓の外は 1 行も記録しない）。

        引数は**読むだけ**である（Port 事後条件 1＝非侵襲）。`point` は frozen なので
        写す必要が無く、`account` / `open_trades` からは値だけを取り出して列へ積む
        （参照を持つと次の評価点で中身が変わる＝引数の寿命はこの呼出のあいだだけ）。
        """
        raw = point.tick_time
        # 契約上宣言された不在（ティックを持たない点）へ `bar.time` を充当する（§6.5.0）。
        epoch = epoch_seconds(point.bar.time if raw is None else raw)
        # 窓判定を先に置く: 窓の外の点で保有列の走査（下の集計）を発行しない。
        if not self._window.contains(epoch):
            return

        buy_volume = 0.0
        sell_volume = 0.0
        open_count = 0
        for held in open_trades:
            position = held.position
            open_count += 1
            if position.side == _BUY:
                buy_volume += position.volume
            else:
                sell_volume += position.volume

        columns = self.columns
        columns["time"].append(epoch)
        columns["bar_index"].append(point.bar_index)
        columns["tick_ordinal"].append(point.tick_ordinal)
        columns["granularity"].append(point.granularity)
        columns["is_synthetic"].append(point.is_synthetic_bar_point)
        columns["eval_bid"].append(point.eval_bid)
        columns["eval_ask"].append(point.eval_ask)
        columns["balance"].append(account.balance)
        columns["equity"].append(account.equity)
        columns["floating_pnl"].append(account.floating_pnl)
        columns["margin"].append(account.margin)
        columns["margin_level"].append(account.margin_level())
        columns["swap"].append(account.swap)
        columns["commission"].append(account.commission)
        columns["open_count"].append(open_count)
        columns["open_volume_buy"].append(buy_volume)
        columns["open_volume_sell"].append(sell_volume)
        columns["halted"].append(halted)

    # --- 読み出し（run 完了後に永続化段が使う）---------------------------

    @property
    def rows(self) -> int:
        """記録行数（列はすべて同じ長さである）。"""
        return len(self.columns["time"])

    def bar_indices(self) -> "list[int]":
        """記録した `bar_index` の並び（`simulator/adapter/trace/indicator_trace.py` の鍵の供給元）。

        異なり集合ではなく**並び**を返す: 「異なり数」を作るのは指標トレース側の
        関心であり、ここで畳むと畳み方の規則が 2 箇所に散る。
        """
        return self.columns["bar_index"]

    @property
    def window_bounds(self) -> "tuple[Any, Any]":
        """記録に用いた窓の (start, end)（成果物 trace_meta.json 用）。"""
        return self._window.bounds
