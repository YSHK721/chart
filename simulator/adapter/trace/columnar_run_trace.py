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

`time` 列の単位は epoch **ミリ秒**（§9.0 の是正・実測に基づく）:
    実ティック 1 ヶ月 run（`simulator/tests/confirmation/2026-01_ma-market` の JP225
    2026-01）を trace ON で実走すると評価点は 1,036,394 行あり、`time` が epoch **秒**
    だった時期は **407,745 行（39.3%）が他の行と同じ時刻**（1 秒を最大 14 行が共有）に
    なっていた。tick-store の実 dtype は秒未満を持つ（`datetime64[ms]` /
    `datetime64[us]`）のに、epoch_seconds の `.astype("datetime64[s]")` が切り捨てる
    ためである。「ティック粒度での推移」という要件に対する欠陥であり、
    分析面で同一秒を代表値へ潰すのは**症状の出る条件を避ける形**（対症療法）なので、
    出力単位そのものを直した。ブラウザの `Date` もミリ秒なので front で変換が要らない。

    trades の `entry_time` / `exit_time` は epoch **秒**のままなので、突合は ×1000 で行う。

`time` 列の出所（§6.5.0・**導出点はこのクラスただ 1 つ**）:
    点の epoch 時刻は `point.tick_time`、それが `None` のときは `point.bar.time`。
    `None` は値からの推定対象ではなく、**契約上宣言された不在**（ティックを持たない点＝
    `simulator/usecase/bar_schedule.py` の点・ティック 0 件バーの持ち越し点）である。
    §5.1 で撤回した「合成か実かの判別子」とは別物であり、判別材料の要らない充当である。

    導出した 1 つの値が `time` 列にも `window.contains(...)` にも渡る。窓判定側と列出力側の
    2 箇所で導出すると、「窓が通した点の `time` 列が窓の外」という食い違いが**例外を
    出さずに**起こる。よって `TraceWindow.contains` の引数は評価点
    （`simulator/usecase/evaluation_point.py`）ではなく epoch 秒（int）であり、
    ここでは導出値の**単位換算**（`// 1000`）だけを行う——2 度目の導出ではないので
    食い違う余地が無い（`epoch_millis(v) // 1000 == epoch_seconds(v)` は
    `simulator/tests/unit/test_bar_time_millis.py` が全受理表現で固定する）。

    型の正規化規則は `simulator.domain.bar_time.epoch_millis` が単一ソースであり、
    受理集合（epoch 整数 / numpy.datetime64 / datetime）を本モジュールで列挙し直さない。
    受理集合の定義は epoch_seconds と共有する（`EPOCH_CONVERTERS` ただ 1 つ）。

`observe` に `try` / `except` を置かない（§7.0 の裁定）:
    例外を送出しないことは**実装側の義務**であり、エンジンは握らない。本実装が run 中に
    行うのは列への append だけで（永続化は run 完了後・§6.5）、送出する正当な理由が無い。
    唯一の例外源は「時刻表現が受理集合の外」という契約違反であり、それは隠すべきではない。

依存規律: pandas / pyarrow を import しない（D-5 の隔離）。列は素の `list` で持ち、
`simulator/adapter/trace/parquet_trace_store.py` が受け取って初めて DataFrame 化する。
"""
from __future__ import annotations

from typing import Any

# 秒とミリ秒の関係は domain（時刻表現の単一ソース）から読む。ここで `1000` を書くと
# 同じ関係に 2 人目の所有者ができる（複製は必ず取り残しを生む）。
from simulator.domain.bar_time import MILLIS_PER_SECOND, epoch_millis
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
        millis = epoch_millis(point.bar.time if raw is None else raw)
        # 窓判定を先に置く: 窓の外の点で保有列の走査（下の集計）を発行しない。
        # 窓は epoch **秒**を受ける（§6.4 の JSON 契約）。除算は導出の 2 度目ではなく
        # 同一値の単位換算であり、`epoch_millis(v) // 1000 == epoch_seconds(v)` は
        # `test_bar_time_millis.py` が全受理表現で固定する＝食い違いは構造上起きない。
        if not self._window.contains(millis // MILLIS_PER_SECOND):
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
        columns["time"].append(millis)
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
