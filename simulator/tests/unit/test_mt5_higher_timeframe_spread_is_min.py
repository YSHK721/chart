"""上位足の <SPREAD> は「その足に含まれる入力 spread の最小値」である（ISSUE-511 前提 (b)）。

規則のオラクル検定である。素材は VM の MT5 端末が書いた上位足エクスポート（21 本）で、
受け入れ（バイト列・行数・書式）は test_mt5_bars_fixture_acceptance.py が別に固定する。
素材の所在・ファイル名・行の復号・ラベルの読み方も同ファイルが所有しており、本ファイルは
それを import する（同じ規則を 2 か所に書くと、片方だけ直したときに他方が別の素材を見た
まま緑になる）。

## 束ね方（裁定 TBD-4）

束ねる単位は **MT5 自身が書いた上位足のラベル**であり、我々の resample を通さない。
理由は境界が一致しないことが既知だからである（ローカルの 1 週足は金曜ラベル・MT5 の
週足は日曜ラベル／ローカルの 4 時間足はセッション日起点・MT5 はサーバ日起点）。
我々の畳みを通すと、境界の不一致がそのまま spread の不一致に化け、「畳み方の検定」に
「境界の主張」が紛れ込む。

束ねる範囲は **次のラベルから導出する**（半開区間 [label_i, label_{i+1})）。時間足ごとの
秒長（1 週 = 604800 秒など）を書かないのは、それが規則の 2 通り目の定義になるからである。
ラベルは読む時点で日時へ変換し、範囲の判定も並べ替えもその日時で行う（「書式が固定長
だから辞書順がそのまま時刻順」という前提を持たない。崩れた書式は parse_label が
ValueError で落とす）。秒長を足し引きする時刻演算は持たないので、境界の定義は上の 1 通り
のままである。

## 覆えない足は比較しない

入力が範囲を完全に覆っていない足（先頭・末尾）は除外する。除外の結果 0 件になっていない
ことは各検定が比較件数で確かめ、除外の件数そのものは COMPARABLE_BARS が固定する
（黙って 0 件になる空振りを防ぐ）。素材が無ければ検定は skip ではなく失敗する。

期間 A / B の MN1 だけは比較件数が 0 になる。最初の足は入力の始端より前に開き（切り下げ
の結果）、2 本目には次のラベルが無いためで、どちらも上の規則で覆えない足である。MN1 の
規則は長期の素材が固定する。

## 測定の基準

期間 A / B は **M1 を入力**に上位足 8 本を検証する。長期（2020-05〜2026-09）は M1 が無い
ため **D1 を入力**に W1 / MN1 を検証する。**この 2 組の測定基準は D1 であって M1 ではない。**
「最小値の入れ子は同値（日ごとの最小の最小はその週の最小）だから M1 基準でも同じ」は
未検証の推論である（長期の M1 が無いので確かめていない。日ごとの最小が週の M1 を漏れなく
覆うことも別に要る）。W1 を M1 基準で見た実測は期間 A / B の 3 本・4 本だけで、MN1 に
至っては M1 基準で覆える足が 0 本である（COMPARABLE_BARS）。

## 期間 B と長期を含める理由

最頻値（mode）は一部の組で最小値と完全に一致してしまい、両者を区別できない。規則を mode
へ差し替えた実測では、比較の成立する 16 組のうち 5 組（期間 A の H1 / H4 / D1 / W1 と
期間 B の W1）が緑のまま残った。期間 A で mode を棄却するのは M5 / M15 / M30 の 3 組だけ
であり、H1 / H4 / D1 を棄却するのは期間 B である。どちらの期間でも棄却できない W1 と、
期間 A / B では比較が 0 件の MN1 を棄却するのは、長期の W1 / MN1 だけである。
"""
from __future__ import annotations

import bisect
import functools
from collections import Counter
from datetime import datetime
from typing import Callable, NamedTuple, Sequence

import pytest

from simulator.tests.unit.test_mt5_bars_fixture_acceptance import (
    PERIOD_A,
    PERIOD_B,
    PERIOD_LONG,
    parse_label,
    rows_of,
)

#: 各期間の入力（測定の基準）。長期は M1 が無いので D1 を基準にする。
INPUT_TIMEFRAME = {PERIOD_A: "M1", PERIOD_B: "M1", PERIOD_LONG: "D1"}

#: 検定する規則。変異（max / first / last / mode）はこの名前を差し替えて実測する。
RULE: "Callable[[Sequence[int]], int]" = min

#: 覆えた足の本数（凍結素材に対する実測値の固定）。
#:
#: 0 の 2 組（期間 A / B の MN1）は「覆える足が無い」ことの明示であって、空振りの黙認では
#: ない。MN1 は素材が 2 本しか無く、半開区間を作れるのは 1 本目だけで、そのラベル
#: （2024.12.01 / 2026.03.01）は入力 M1 の始端（2024.12.30 01:01 / 2026.03.23 01:00）より
#: 前にある（始端を切り下げた結果）。したがって入力はその足を覆えない。
#:
#: 本表が唯一の台帳であり、どの組を検定するかは timeframes_of と COMPARED_PAIRS が
#: ここから導く。表の内容（組ごとの件数・組数・0 件である組の顔ぶれ）は
#: test_the_comparable_bar_count_is_pinned_for_every_declared_pair が測り直して固定する。
#: 何をなぜ固定するかの実測記録は同検定の docstring が持つ（同じ記録をここへ書き写すと、
#: 同じ話が 2 か所で別々に古びる）。
COMPARABLE_BARS = {
    (PERIOD_A, "M5"): 6484, (PERIOD_A, "M15"): 2160, (PERIOD_A, "M30"): 1079,
    (PERIOD_A, "H1"): 539, (PERIOD_A, "H4"): 140, (PERIOD_A, "D1"): 22,
    (PERIOD_A, "W1"): 3, (PERIOD_A, "MN1"): 0,
    (PERIOD_B, "M5"): 7906, (PERIOD_B, "M15"): 2636, (PERIOD_B, "M30"): 1318,
    (PERIOD_B, "H1"): 659, (PERIOD_B, "H4"): 171, (PERIOD_B, "D1"): 27,
    (PERIOD_B, "W1"): 4, (PERIOD_B, "MN1"): 0,
    (PERIOD_LONG, "W1"): 330, (PERIOD_LONG, "MN1"): 75,
}

#: 実際に比較が成立する 16 組（MN1 の期間 A / B を除く）。
COMPARED_PAIRS = tuple(pair for pair, count in COMPARABLE_BARS.items() if count > 0)


def timeframes_of(period_id: str) -> "tuple[str, ...]":
    """その期間で検定する上位足（台帳は COMPARABLE_BARS ただ 1 つ）。

    「どの組を検定するか」の宣言を表の外にもう 1 枚持つと、何も施行しないまま両者が
    食い違う。導出にすれば食い違いようがない。
    """
    return tuple(timeframe for period, timeframe in COMPARABLE_BARS if period == period_id)


class Bar(NamedTuple):
    """1 本の足。label は端末のラベルを日時にしたもの（比較も並べ替えも時刻順）。"""

    label: datetime
    spread: int


@functools.lru_cache(maxsize=None)
def load_bars(period_id: str, timeframe: str) -> "tuple[Bar, ...]":
    """CSV 1 本を読む。不在なら FileNotFoundError で落ちる（skip しない）。"""
    bars = []
    for row in rows_of(period_id, timeframe):
        fields = row.split("\t")
        bars.append(Bar(parse_label(fields[0], fields[1]), int(fields[8])))
    return tuple(bars)


class Span(NamedTuple):
    """1 本の上位足と、それが束ねる半開区間 [start, stop)。"""

    bar: Bar
    start: datetime
    stop: datetime


def spans_of(bars: "Sequence[Bar]") -> "list[Span]":
    """範囲を次のラベルから導出する（末尾の足は次のラベルが無いので作らない）。"""
    return [Span(bars[i], bars[i].label, bars[i + 1].label) for i in range(len(bars) - 1)]


def covered(spans: "Sequence[Span]", first_input: datetime,
            last_input: datetime) -> "list[Span]":
    """入力が完全に覆う範囲だけを残す（先頭・末尾の欠けた足を比較しない）。"""
    return [s for s in spans if s.start >= first_input and s.stop <= last_input]


class Folded(NamedTuple):
    """1 組（期間 × 上位足）の畳みの結果。"""

    compared: int
    mismatches: "list[tuple[datetime, int, int]]"
    folded_rows: int
    empty_ranges: int


def fold_against(source: "Sequence[Bar]", bars: "Sequence[Bar]",
                 *, rule: "Callable[[Sequence[int]], int]") -> Folded:
    """入力 source を上位足 bars のラベルで束ね、規則 rule の値を足の spread と比べる。

    畳むのは比較する範囲の入力だけである（捨てる分を畳まない）。
    """
    labels = [b.label for b in source]
    spreads = [b.spread for b in source]
    compared_spans = covered(spans_of(bars), labels[0], labels[-1])
    mismatches: "list[tuple[datetime, int, int]]" = []
    folded_rows = 0
    empty_ranges = 0
    for span in compared_spans:
        left = bisect.bisect_left(labels, span.start)
        right = bisect.bisect_left(labels, span.stop)
        chunk = spreads[left:right]
        folded_rows += len(chunk)
        if not chunk:
            empty_ranges += 1
            continue
        aggregated = rule(chunk)
        if aggregated != span.bar.spread:
            mismatches.append((span.bar.label, aggregated, span.bar.spread))
    return Folded(
        compared=len(compared_spans),
        mismatches=mismatches,
        folded_rows=folded_rows,
        empty_ranges=empty_ranges,
    )


def fold_period(period_id: str, timeframes: "Sequence[str]", *,
                rule: "Callable[[Sequence[int]], int]",
                read: "Callable[[str, str], Sequence[Bar]]") -> "dict[str, Folded]":
    """1 期間ぶんを畳む。入力も各上位足も読むのは 1 回ずつである。"""
    source = read(period_id, INPUT_TIMEFRAME[period_id])
    return {tf: fold_against(source, read(period_id, tf), rule=rule) for tf in timeframes}


def rows_inside_compared_range(period_id: str, timeframe: str) -> int:
    """比較した範囲に入る入力の行数を、範囲の両端 1 組だけから数える（畳みを通さない）。

    覆えた範囲が途切れ無く連続すること（test_the_compared_ranges_form_one_unbroken_run
    が固定する）を使い、端から端までを 1 回数えるだけで求める。畳み側とは別の道筋で
    出すので、両者の一致は恒真ではない。
    """
    source = load_bars(period_id, INPUT_TIMEFRAME[period_id])
    labels = [b.label for b in source]
    spans = covered(spans_of(load_bars(period_id, timeframe)), labels[0], labels[-1])
    return bisect.bisect_left(labels, spans[-1].stop) - bisect.bisect_left(labels, spans[0].start)


class ReadSpy:
    """読み取りの発行を数える Test Spy（同じ CSV を 2 度読めば calls に 2 回現れる）。"""

    def __init__(self) -> None:
        self.calls: "list[tuple[str, str]]" = []

    def __call__(self, period_id: str, timeframe: str) -> "Sequence[Bar]":
        self.calls.append((period_id, timeframe))
        return load_bars(period_id, timeframe)


def pair_id(pair: "tuple[str, str]") -> str:
    return f"{pair[0]}-{pair[1]}"


# =====================================================================
# 規則そのもの
# =====================================================================

@pytest.mark.parametrize("pair", COMPARED_PAIRS, ids=[pair_id(p) for p in COMPARED_PAIRS])
def test_every_higher_timeframe_spread_equals_the_minimum_of_the_input_spreads(
        pair: "tuple[str, str]"):
    """上位足の <SPREAD> が、その足が束ねる入力 spread の最小値と一致する。

    期間 A / B の基準は M1、長期（PERIOD_LONG）の基準は D1 である。
    """
    period_id, timeframe = pair
    folded = fold_period(period_id, [timeframe], rule=RULE, read=load_bars)[timeframe]

    assert folded.mismatches == []
    assert folded.compared == COMPARABLE_BARS[pair]
    assert folded.compared > 0
    assert folded.empty_ranges == 0


def test_the_comparable_bar_count_is_pinned_for_every_declared_pair():
    """表の 18 組すべてを測り直し、組数と 0 件の組の顔ぶれも固定する。

    件数の等式だけでは 0 件の組を守れない。measured は COMPARABLE_BARS を読んで作るので、
    表から行ごと消えた組はそもそも測られず、0 を足していた合計も動かない。0 件の組は上の
    規則検定にも現れない（parametrize も本表から導くため）ので、行が消えれば誰も測らなく
    なる。だから行そのものの存在を、件数とは別に組数と顔ぶれで表明する。

    実測（本表から 0 件の (期間 A, MN1) の行を削る）: 落ちるのは本検定だけで、本ファイルの
    他の検定はすべて緑のままだった。組数と顔ぶれの 2 つの表明を足す前は、本検定すら落ちな
    かった。
    """
    measured = {
        (period_id, tf): fold_period(period_id, [tf], rule=RULE, read=load_bars)[tf].compared
        for period_id, tf in COMPARABLE_BARS
    }

    assert measured == COMPARABLE_BARS
    assert sum(measured.values()) == 23553
    assert len(COMPARABLE_BARS) == 18
    assert sorted(pair for pair, count in COMPARABLE_BARS.items() if count == 0) == [
        (PERIOD_A, "MN1"), (PERIOD_B, "MN1"),
    ]


def test_the_compared_ranges_form_one_unbroken_run():
    """覆えた範囲は端から端まで途切れない（比較に使った行数を端だけから数える根拠）。"""
    broken = []
    for period_id, timeframe in COMPARED_PAIRS:
        source = load_bars(period_id, INPUT_TIMEFRAME[period_id])
        spans = covered(spans_of(load_bars(period_id, timeframe)),
                        source[0].label, source[-1].label)
        gaps = [(period_id, timeframe, a.stop, b.start)
                for a, b in zip(spans, spans[1:]) if a.stop != b.start]
        broken.extend(gaps)

    assert broken == []


# =====================================================================
# 計算量（Test Spy・回数を焼き込まない）
# =====================================================================

@pytest.mark.parametrize("period_id", [PERIOD_A, PERIOD_B, PERIOD_LONG])
def test_no_input_row_is_folded_and_then_discarded(period_id: str):
    """畳んだ入力の行数 − 比較に使った行数 = 0（捨てる分を畳まない）。

    固定するのは回数ではなく**無駄の不在**である。素材には覆えない足（先頭・末尾）が
    あり、そこに入る入力行は比較に使われない。「全部畳んでから捨てる」実装は出力が
    正しいままなので、規則の検定では原理的に落ちない。

    長期（PERIOD_LONG）も測る。MN1 の規則を裏づける組は長期にしかなく（期間 A / B の
    MN1 は比較 0 件）、ここから外すと MN1 の畳みだけ浪費の検査が無いまま残る。
    """
    wasted = [
        (timeframe,
         fold_period(period_id, [timeframe], rule=RULE, read=load_bars)[timeframe].folded_rows
         - rows_inside_compared_range(period_id, timeframe))
        for period_id_, timeframe in COMPARED_PAIRS if period_id_ == period_id
    ]

    assert [w for w in wasted if w[1] != 0] == []
    assert len(wasted) > 0
    assert sum(rows_inside_compared_range(period_id, tf) for tf, _ in wasted) > 0


@pytest.mark.parametrize("timeframes",
                         [timeframes_of(PERIOD_A)[:2], timeframes_of(PERIOD_A)])
def test_each_csv_is_read_once_however_many_timeframes_are_folded(timeframes: "tuple[str, ...]"):
    """読み取りの発行が「入力 1 本 + 上位足 1 本ずつ」ちょうどで、規模で増えない。

    時間足 2 本と 8 本の 2 点で測る。同じ CSV を 2 度読む実装（上位足ごとに入力を
    読み直す）は、出力が正しいままこの検定だけが落ちる。1 本あたりの発行数を固定する
    ので、本数そのものは期待値に焼き込まない。
    """
    spy = ReadSpy()

    fold_period(PERIOD_A, timeframes, rule=RULE, read=spy)

    issued = Counter(spy.calls)
    needed = {(PERIOD_A, INPUT_TIMEFRAME[PERIOD_A])} | {(PERIOD_A, tf) for tf in timeframes}
    assert max(issued.values()) == 1
    assert set(issued) == needed
    assert len(spy.calls) - len(needed) == 0
