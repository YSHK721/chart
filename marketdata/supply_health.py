"""supply_health — 鮮度（段 1）と書き手の在否（段 2）を束ねる（ISSUE-526 段 3）。

どちらか一方では言えないことがある。「先端が進まない」という事実は、書き手が居るか居ないかで
意味が正反対になる:

    在って進まない   → :data:`STUCK`   （固まり。プロセスは生きている。詰まりを探す）
    正規の書き手が無い → :data:`STOPPED` （停止。起こし直す以外に進みようがない）
    在って進んでいる → :data:`HEALTHY`  （健全）

この 2 つの異常を同じ値へ潰すと、運用は毎回どちらなのかを手で調べ直すことになる。**束ねる価値は
区別を出すこと**なので、3 つを別の値にする。分からないときは :data:`UNDECIDABLE` であり、
健全とも異常とも呼ばない（段 1・段 2 と同じ規律で、居ないことと分からないことを混ぜない）。

不在が進みより強い理由:
    在否は 1 回の観測で言えるが、進みは前回観測との差なので 2 回を要する。加えて、正規の
    書き手が居ないのに先端が伸びている状態は「錠を持たない誰かが書いている」であり、
    ISSUE-488（同一 CSV への非原子的な同時書き込みで 1M の 8 月バーと 1D の 3 日ぶんが恒久
    欠落した）の危険そのものである。よって不在は、進んで見えても :data:`STOPPED` と呼ぶ。

暦も時間閾値も持たない: 進みの判定は :mod:`marketdata.supply_freshness` の相対比較へ委ね、
在否は :mod:`common.writer_lock` の読み口へ委ねる。本モジュールは 2 つの答えを突き合わせる
規則だけを持つ。**flock は取らない**（取ると稼働中の書き手に弾かれ、供給を守るための錠が
供給の監視を止める側へ回る）。**1 バイトも書かない**。

依存方向: 中立核（:mod:`common.writer_lock`）と、素材の所有者である marketdata 内の 3 つ
（鮮度 supply_freshness・銘柄とベンダの台帳 dataset_registry・物理基点 paths）のみ。書き手の
実体である tools へは依存しない（上位層への逆流になる）。錠の名前とプログラム名は
:data:`SUPPLIES` に写しを置き、実体との一致は
tools/tests/test_supply_health_writer_ledger.py が機械的に突き合わせる。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.writer_lock import WRITER_ABSENT, WRITER_PRESENT, writer_presence
from marketdata import dataset_registry, supply_freshness
from marketdata.paths import DATA_DIR

#: 束ねた判定の値（健全＝書き手が在り、先端も進んでいる）。
HEALTHY = "healthy"

#: 束ねた判定の値（固まり＝書き手は在るのに先端が進まない）。
STUCK = "stuck"

#: 束ねた判定の値（停止＝正規の書き手が居ない）。
STOPPED = "stopped"

#: 束ねた判定の値（判定不能＝まだ言えない）。健全とも異常とも別の値である。
UNDECIDABLE = "undecidable"

#: 判定の語彙を、**軽い順**に並べたもの。総合判定はこの順で最も重いものを採る。
#:
#: 停止を固まりより重く置く理由: 固まりは詰まりが解ければ自力で再開しうるが、停止は書き手が
#: 居ないので誰かが起こすまで永久に進まない。判定不能を健全より重く置く理由は段 1・段 2 と
#: 同じで、分からないことを黙って合格にしないためである。
VERDICTS = (HEALTHY, UNDECIDABLE, STUCK, STOPPED)

#: 告知の対象にする判定（異常の 2 つだけ）。
#:
#: 判定不能で鳴らしてはならない。観測を始めた直後は必ず判定不能になる（前回観測が無い）ため、
#: 起動のたびに鳴り、鳴りっぱなしの告知は誰も見なくなる。
ALARM_VERDICTS = frozenset({STUCK, STOPPED})


@dataclass(frozen=True)
class Supply:
    """1 本の供給について、呼出側が与える面。

    Attributes:
        lock_filename: その供給の書き手が獲得する錠の名前（``data_dir`` 直下）。
        program: その錠を持つはずのプログラム（コマンド行に現れる語）。PID は使い回される
            ため、在否の照合はこの語で行う。
    """

    lock_filename: str
    program: str


#: 出荷時の供給台帳（供給の名前 → 面）。名前は台帳 dataset_registry のベンダ欄の値であり、
#: どの系列がその供給に属するかはベンダ欄が引く（第 2 の系列一覧を持たない）。
SUPPLIES = {
    "dukascopy": Supply(
        lock_filename="live_tick_watch.lock", program="live_tick_watch.py"),
    "mt5": Supply(
        lock_filename="mt5_tick_watch.lock", program="mt5_tick_watch.py"),
}


def overall(verdicts) -> str:
    """供給ごとの判定から総合判定（最も重いもの）を求める。

    1 本でも異常なら総合は異常である（健全な側に隠れない）。供給が 1 つも無ければ
    :data:`UNDECIDABLE`（言える材料が無い）。
    """
    return max(
        (verdict for verdict in verdicts.values()),
        key=VERDICTS.index,
        default=UNDECIDABLE,
    )


class SupplyHealthWatch:
    """供給ごとの健全性を周期的に観測する。

    進みは前回観測との差なので、**同じインスタンスで繰り返し観測する**こと（1 回目は必ず
    判定不能になる）。前回観測はプロセス内メモリに持つ（新しい永続 state を作らない）。

    Parameters:
        supplies: 供給の名前 → :class:`Supply`。既定は :data:`SUPPLIES`。
        refs: 先端を観測する系列。既定はティック由来の全 ref。**供給ごとに絞らない**のは、
            鮮度の判定が同一銘柄の兄弟系列どうしの相対比較だからである（片方の供給が止まった
            ことは、もう片方が進んだ観測区間でしか言えない）。
        data_dir: 錠と M1 の置き場（読み取りのみ）。
    """

    def __init__(self, supplies=None, refs=None, data_dir=DATA_DIR) -> None:
        self._supplies = dict(supplies if supplies is not None else SUPPLIES)
        self._refs = tuple(
            refs if refs is not None else sorted(dataset_registry.tick_refs()))
        self._data_dir = data_dir
        self._freshness = supply_freshness.M1FreshnessWatch(
            self._refs, data_dir=data_dir)

    def observe(self) -> "dict[str, str]":
        """今この瞬間の、供給ごとの束ねた判定。"""
        freshness = self._freshness.observe()
        return {
            name: _combine(
                writer_presence(
                    self._data_dir,
                    filename=supply.lock_filename,
                    expect_cmdline_contains=supply.program,
                ),
                _freshness_of(name, freshness),
            )
            for name, supply in self._supplies.items()
        }

    def report(self) -> str:
        """プロセス外から読むための平文の報告。

        1 行目が総合判定、2 行目以降が「供給の名前 判定」の 1 行ずつ。読む側が 1 行目だけで
        判断できる形にしてあるのは、起動スクリプトが jq 等の依存なしに読めるようにするため
        （`/__serving_root` と同じ様式）。
        """
        verdicts = self.observe()
        lines = [overall(verdicts)]
        lines += [f"{name} {verdict}" for name, verdict in verdicts.items()]
        return "\n".join(lines) + "\n"


def _combine(presence: str, freshness: str) -> str:
    """在否と進みを突き合わせた 1 つの判定。"""
    if presence == WRITER_ABSENT:
        return STOPPED
    if presence != WRITER_PRESENT:
        return UNDECIDABLE
    if freshness == supply_freshness.STALE:
        return STUCK
    if freshness == supply_freshness.HEALTHY:
        return HEALTHY
    return UNDECIDABLE


def _freshness_of(supply_name: str, freshness: "dict[str, str]") -> str:
    """その供給が書いている系列だけを見た、まとめた鮮度。

    1 系列でも止まっていれば止まっている（同じ書き手が書く系列は一緒に進むため、片方だけ
    止まるのは別の異常である）。全部健全なら健全。それ以外は言えない。
    """
    mine = [
        verdict for ref, verdict in freshness.items()
        if dataset_registry.REGISTRY[ref].vendor == supply_name
    ]
    if supply_freshness.STALE in mine:
        return supply_freshness.STALE
    if mine and set(mine) == {supply_freshness.HEALTHY}:
        return supply_freshness.HEALTHY
    return supply_freshness.UNDECIDABLE


def main() -> int:
    """``python -m marketdata.supply_health`` — 報告を 1 回だけ標準出力へ出す。

    起動スクリプトの告知（tools/supply_health_notice.sh）が読む口である。shell 側へ python の
    コード片を埋め込まないために、実行可能なモジュールとして持つ。
    """
    print(SupplyHealthWatch().report(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
