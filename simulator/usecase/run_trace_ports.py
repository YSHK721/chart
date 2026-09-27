"""RunTracePort（エンジンの観測境界・RUN_TRACE_BASIC_DESIGN §4.1）。

何を解くか:
    エンジンから外へ出る辺は run 終了時の結果 DTO
    （`simulator/usecase/models.py` の BacktestResult）1 回だけであり、
    「なぜその時点で建てたのか」「その瞬間に口座がどう見えていたか」は追えない。
    本 Port は評価点 1 つぶんの観測を受け取る唯一の境界である。

なぜ値オブジェクトを組んで渡さないか:
    観測は 1 run で評価点の本数（JP225 2025-01 の実測で 952,832 回）起きる。DTO を
    組めば、記録しない実装（窓の外・記録 OFF）では必ず捨てられるオブジェクトが同数
    生まれる。「作ってから捨てる」形は出力が正しいままなので状態検証では原理的に
    落ちない（ISSUE-450 と同型）。よって引数は素の参照で渡す。

引数注釈が `Any` である理由:
    既存 Port と同じ流儀に統一する（先例 `usecase/marker_ports.py:21-23`・
    `usecase/ports.py:148-150` が口座集約をまさに `Any` で受けている）。

`ports.py` は無改変。観測は実行に必要な境界ではない（既定では注入されない）ため、
新 Port は本ファイルへ分離する（先例 `usecase/marker_ports.py`）。
"""
from __future__ import annotations

import abc
from typing import Any


class RunTracePort(abc.ABC):
    """評価点 1 つぶんの観測を受ける境界。"""

    @abc.abstractmethod
    def observe(
        self, point: Any, account: Any, open_trades: Any, halted: bool
    ) -> None:
        """1 評価点の全副作用が確定した直後に呼ばれる。

        事前条件: `point` の評価が終わっており、`account` が当該点のクォートで
            値洗いされていること（＝呼出点は評価点ループの直後ただ 1 箇所）。

        事後条件（**全実装が守る**。基底が何も約束しないと、差し替えた実装が run の
        結果を変えても「契約違反」と言えず、置換可能性が定義されないままになる）:

          1. **エンジンの状態を変えない**。引数の `account` / `open_trades`、および
             そこから辿れる保有玉は run の**生きた実体**であり、書き換えれば以降の
             評価点が変わる——観測が結果を作ってしまう。読むだけにする。
             検査の射程（正直な限界）: `simulator/tests/unit/test_run_trace_observation.py`
             の非侵襲検定が縛るのは**同ファイル内の Spy 1 個だけ**であり、本 Port を
             継承する任意の実装は一度も通らない。任意実装への強制は段階 3 の非侵襲
             ゲートが担う（現時点の具象は当該 Spy のみ）。
          2. **戻り値を持たない**（`None`）。呼出側は返り値を見ない。見れば観測が
             run の分岐に効くことになり、1 と矛盾する。

        引数の寿命: 引数が有効なのは**この呼出のあいだだけ**である。保有列は次の
            評価点で束ね直され、玉自体は建玉変更が in-place で書き換え、口座は次の点で
            値洗いされる。後で読むために持つなら参照ではなく**値を写す**
            （`point` だけは frozen なので写す必要が無い）。

        何を記録するか・どこを残すか（窓）・どう永続化するかは実装が決める（本契約は
        それらに何も要求しない）。
        """
        raise NotImplementedError

    def observe_start(self, bar_count: int) -> None:
        """評価点ループに入る直前に 1 回だけ呼ばれる（`bar_count` は run の足の総数）。

        なぜ在るか: 実行中の進み具合（処理した足 ÷ 足の総数）を画面へ出す実装は、分母を
            ループの前に知る必要がある（2026-09-27 依頼者指示「結果待ちの終わりが分からない」）。
            足の列はエンジンの組み立ての中で読まれるので、観測器を組む時点では本数が分からない。

        既定は何もしない（評価点・足単位の記録器には要らない）。事後条件は `observe` と同じ
            （エンジンの状態を変えない・戻り値なし）。
        """
        return None

    def observe_final_settlement(self, bar_index: int, bar: Any, account: Any) -> None:
        """期末清算（end_of_test）の直後に 1 回だけ呼ばれる（清算が無い run では呼ばれない）。

        なぜ在るか: 期末清算は評価点ループの**後**で行われ、`observe` は清算後の口座を
            見ない。足ごとの口座を記録する実装は、最終足の値が清算前のまま残り、残高が
            run の結果（balance_curve）と食い違った（2026-09-26 実測: 8775 対 8765）。

        清算は評価点ではない（新しい価格を持たない）。よって `observe` として呼ばない——
            評価点単位の実装へ同じ点を 2 度渡すことになる。既定は何もしない（評価点単位の
            実装には記録するものが無い）。清算後の口座を残す実装だけが上書きする。

        引数: `bar_index` / `bar` は清算した足（最終足）、`account` は清算と値洗いを終えた
            口座。事後条件と引数の寿命は `observe` と同じ（読むだけ・戻り値なし・値を写す）。
        """
        return None


#: 観測器の記録の単位の語彙。"point"＝評価点 1 つにつき 1 行／"bar"＝足 1 本につき 1 行
#: （足の最後の評価点の値）。契約（`RunTracePort`）は単位を要求しない——検定が期待行数を
#: 導けるよう、各実装が 「`OBSERVATION_UNIT`」 として名乗る。
OBSERVATION_UNITS = ("point", "bar")


class FanOutRunTrace(RunTracePort):
    """1 つの観測を複数の観測器へ配る合成（エンジンの観測口は 1 つなので合成で束ねる）。

    Port の合成なので Port と同じ層に置く（具象の記録器ではない）。配る順は渡した順。
    """

    def __init__(self, *tracers: RunTracePort) -> None:
        self._tracers = tracers

    def observe(
        self, point: Any, account: Any, open_trades: Any, halted: bool
    ) -> None:
        for tracer in self._tracers:
            tracer.observe(point, account, open_trades, halted)

    def observe_start(self, bar_count: int) -> None:
        for tracer in self._tracers:
            tracer.observe_start(bar_count)

    def observe_final_settlement(self, bar_index: int, bar: Any, account: Any) -> None:
        for tracer in self._tracers:
            tracer.observe_final_settlement(bar_index, bar, account)
