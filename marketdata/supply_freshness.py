"""supply_freshness — 兄弟系列の相対的な進みで、片方だけ止まった供給を見つける（ISSUE-526 段 1）。

欠測は出力に現れない（止まった時点までの CSV は正しいままである）ため、状態検証では原理的に
落ちない。気づくには「最後に取り込んだ時刻」を能動的に測るしかない。

暦を持たない理由:
    「最終ティックが N 分より古い」を閾値にすると、休場では必ず超えるので鳴りっぱなしになる。
    本モジュールは**同一銘柄の兄弟系列どうしの相対比較**だけで判定する。基準となる系列が
    進んだ観測区間でのみ対象を見るため、休場では基準も進まず、判定そのものが起きない。

依存方向（厳守）: pandas と marketdata 内の 4 つ（物理基点 paths・末尾読み tail_reader・
銘柄の台帳 dataset_registry・M1 の置き場の権威 tick_m1）のみに依存する。simulator /
indigators へは依存しない（暦の権威はそちらに在るが、ここへ引き込むと marketdata から
上位層への逆流になる）。この宣言は
marketdata/tests/test_module_dependency_declarations.py の許可表が AST で施行する。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from marketdata import dataset_registry, tail_reader, tick_m1
from marketdata.paths import DATA_DIR


#: 判定の値（健全）。
HEALTHY = "healthy"

#: 判定の値（異常＝基準が進んだ観測区間で 1 分も進まなかった）。
STALE = "stale"

#: 判定の値（判定不能＝比べる基準が 1 本も無い）。健全と別の値であることが要点であり、
#: 基準が無いことを黙って合格にしない。
UNDECIDABLE = "undecidable"

#: 判定の値（先端が読めない＝M1 の末尾が torn）。健全・異常・判定不能のいずれとも別の値であり、
#: :func:`m1_tip` の戻りもこの値を兼ねる（同じ事実に 2 つの呼び名を作らない）。
#:
#: 末尾は非原子的な追記の途中で壊れうる（NUL の混入・列数の崩れ）。そこで例外が素通りすると、
#: **供給の停止を検知するはずのものが、供給の停止と同じ理由で死ぬ**（ISSUE-529 と同型）。
#: 壊れていることを黙って健全にもせず、落ちもせず、名前を付けて返す。
UNREADABLE = "unreadable"

#: 基準系列がこれだけ進んだ観測区間でのみ判定する（分）。
#:
#: 1 分より大きくなければならない。兄弟系列の書き手は互いに独立した常駐であり、1 つの観測の
#: 内側で片方だけが 1 分ぶん追記することが普通に起きる（読む順の差）。1 分では、その競合を
#: そのまま異常と呼ぶ。
MIN_REFERENCE_ADVANCE_MINUTES = 3


def m1_tip(ref: str, data_dir=DATA_DIR) -> "Optional[pd.Timestamp | str]":
    """系列 ref の M1 CSV の**末尾 1 行**の date（naive UTC）。無い・空なら None。

    全読みしない。末尾からの逆シーク（:func:`marketdata.tail_reader.read_tail`）で 1 行だけ読む。
    M1 の置き場の組み立ては :func:`marketdata.tick_m1.m1_csv_path` が唯一源であり、ここでは
    その答えを受け取るだけである（置き場の規則を手書きで複製しない）。

    末尾が壊れていたら :data:`UNREADABLE` を返す（**落ちない・黙って健全にしない**）。壊れ方は
    1 つではない: 列数が崩れれば末尾読みが Fail-Stop し（ISSUE-455 の防護）、日付として
    読めなければ日付解析が失敗し、NUL が混ざれば例外なしに欠損値が出る。どれも「先端が
    読めない」という 1 つの事実なので、例外の有無ではなく事実の名前で返す。
    """
    path = tick_m1.m1_csv_path(ref, data_dir)
    if not path.exists():
        return None
    try:
        tail = tail_reader.read_tail(path, 1)
        if tail.empty:
            return None
        tip = pd.Timestamp(tail.index[-1])
    except Exception:
        return UNREADABLE
    return UNREADABLE if pd.isna(tip) else tip


class M1FreshnessWatch:
    """兄弟系列の先端を周期的に観測し、片方だけ止まっていないかを判定する。

    前回観測は**プロセス内メモリ**に持つ（新しい永続 state を作らない）。
    """

    def __init__(
        self,
        refs,
        data_dir=DATA_DIR,
        min_reference_advance_minutes: int = MIN_REFERENCE_ADVANCE_MINUTES,
    ) -> None:
        self._refs = tuple(refs)
        self._data_dir = data_dir
        self._min_reference_advance = min_reference_advance_minutes
        self._previous: "dict[str, pd.Timestamp]" = {}

    def observe(self) -> "dict[str, str]":
        """今この瞬間の先端を読み、前回観測との差から ref ごとの判定を返す。"""
        tips = {ref: m1_tip(ref, self._data_dir) for ref in self._refs}
        advances = {
            ref: _advance_minutes(self._previous.get(ref), tip)
            for ref, tip in tips.items()
        }
        # 先端が読めなかった観測は前回観測として残さない（壊れた値を次の差分へ持ち込まない）。
        self._previous = {
            ref: tip for ref, tip in tips.items() if isinstance(tip, pd.Timestamp)
        }
        return {
            ref: UNREADABLE if tips[ref] is UNREADABLE
            else _verdict(ref, _siblings(ref, advances), self._min_reference_advance)
            for ref in self._refs
        }


def _advance_minutes(previous, tip) -> "int | None":
    """前回観測から何分ぶん進んだか。前回が無い・先端が無いなら None（観測できていない）。

    先端が時刻でない（無い・読めない）ものは差分を取らない。壊れた先端で差を作ると、
    基準としても対象としても偽の進みが入り込む。
    """
    if not isinstance(tip, pd.Timestamp) or previous is None:
        return None
    return int((tip - previous).total_seconds() // 60)


def _siblings(ref: str, advances: "dict[str, int | None]") -> "dict[str, int | None]":
    """ref と**同じ銘柄**の系列だけを残す（銘柄は台帳 dataset_registry が引く）。

    銘柄は新しい台帳欄ではなく、既にある記述子の銘柄欄をそのまま読む。
    """
    symbol = dataset_registry.REGISTRY[ref].symbol
    return {
        other: advance
        for other, advance in advances.items()
        if dataset_registry.REGISTRY[other].symbol == symbol
    }


def _verdict(
    ref: str, advances: "dict[str, int | None]", min_reference_advance: int
) -> str:
    """ref の判定（基準＝同じ観測にいる**同じ銘柄**の兄弟系列の進み）。"""
    if advances[ref] is None:
        return UNDECIDABLE
    references = [a for other, a in advances.items() if other != ref and a is not None]
    if not references:
        return UNDECIDABLE
    if max(references) >= min_reference_advance and advances[ref] == 0:
        return STALE
    return HEALTHY
