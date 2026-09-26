"""ティック実体と M1 の突合・修復（ISSUE-534 根治・一度書いた分を見直す経路）。

用語（初出定義）:
    ティック実体
        ＝ 日別ティック parquet（``<data_dir>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet``）。
          取得の成果物であり、M1 の**素材の権威**である。
    不完全な足
        ＝ その分のティックが出揃う前に書かれた確定 M1 バー。ティック実体から作り直すと
          ティック数（volume）が増え、多くは OHLC も変わる。出力は連続して見えるため
          **状態検証では原理的に落ちない**（ISSUE-534 の実測: 直近 12 日に 178 分）。
    窓（修復の窓）
        ＝ 突合・書き直しの対象とする UTC 日の並び。呼出側が渡す（周期の持ち主が決める）。
          窓より前の日は 1 バイトも触らない（追記のみの規律を壊さない）。
    先端より前の穴
        ＝ M1 の途中に在る欠測分。鮮度監視（ISSUE-526）は先端しか見ないため検出できない。
    発行 / 使用
        発行 ＝ ティック parquet の読込回数・唯一の畳み点へ渡った回数・既存 M1 から読んだバイト数。
        使用 ＝ 窓に実在する日別 parquet の数（出力に使う素材の数）。
        規約の形は「発行 − 使用 = 0」（絶対命令 2026-08-28）。

なぜこの検定が要るか（実測された損害・ISSUE-534）:
    常駐は「一度書いた分を二度と見直さない」（``tools/live_tick_watch.py`` が自ら宣言している）。
    既存の自己修復 :func:`marketdata.rollup.heal_tail_gaps` は**ロールアップと M1 のずれ**だけを見る。
    その 1 段下（ティック実体と M1）を見る経路が無かったため、2026-09-14〜09-25 の 178 分が
    不完全なまま残り、129 分が欠測したまま残った。猶予秒は原因ではない（欠けたティックは分が
    終わる 11.1 秒前〜ちょうどに発生し、猶予 12 秒を 1 件も超えていない＝ティックは間に合って
    いたのに、書いた時点の手元に無かった）。

本検定が固定するもの:
  H-1 不完全な足が直る（修復後の CSV が、同じティック実体から全構築した CSV と **byte 一致**）。
  H-2 先端より前の穴（丸ごと欠けた分）が埋まる。
  H-3 冪等: すでに一致している窓を修復に掛けても **byte 一致**・書込の発行が 0。
  H-4 有界: 窓より前の日は、ティック実体と食い違っていても**書き換えない**。
  H-5 組の**全系列**に効く（2 系列以上・列の上位集合と、その列を持たない側の両方）。
  H-6 素材に無い分は消さない（減る方向の食い違いは修復せず、警告を残して止まる）。
  CX-1 修復の費用が履歴の長さで増えない（素材側）: parquet 読込・畳みの発行が窓の日数で決まり、
       履歴 2 点（短い / 長い）と系列 1 → 2 で増えない。発行 − 使用 = 0。
       **回数そのものは期待値に焼き込まない**（期待値は窓に実在する parquet の数から導く）。
  CX-2 修復の費用が履歴の長さで増えない（既存 M1 側）: 逆シークで読んだバイト数が履歴 2 点で
       等しい（全件読みへ戻す変異はここに現れる）。

本検定が固定しないもの（射程の明示）:
  - 周期（いつ回すか）・ログの文面・ロールアップへの伝播。それは
    ``tools/tests/test_live_tick_watch_m1_heal.py`` が持つ。
  - 畳みと射影そのもの。それは ``marketdata/tests/test_tick_m1_series_plan.py``。
  - ロールアップ末尾の整合。それは ``marketdata/tests/test_rollup_tail_heal.py``。

書込はすべて ``tmp_path``（``data/marketdata/**`` は 1 バイトも触らない）。合成系列の組み立ては
``marketdata/tests/spread_series_fixture.py`` が唯一源。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd
import pytest

from marketdata import dataset_registry, tail_reader, tick_m1
from marketdata.dataset_registry import REGISTRY
from spread_series_fixture import (
    TICK_TREE,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day,
    put_day_of_spread_points,
    spy,
)

#: Dukascopy 配信のティックであることを示すベンダ欄の値（台帳の語彙）。
_DUKASCOPY = "dukascopy"


def _series_set() -> "tuple[str, ...]":
    """同じ Dukascopy のティック木を読む系列の組（並びは台帳の宣言順・綴りを書き写さない）。"""
    tokens = [d.tick_token for d in REGISTRY.values() if d.tick and d.vendor == _DUKASCOPY]
    return dataset_registry.refs_of_tick_token(tokens[0])


def _spread_ref() -> str:
    """組のうち spread 列を宣言する系列（列の上位集合）。"""
    return [
        ref for ref in _series_set()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ][0]


def _widths(n_minutes: int, n_ticks: int) -> "tuple[tuple[int, ...], ...]":
    """``n_minutes`` 分 × ``n_ticks`` 本の気配幅（points・分内で変わる）。

    ティック本数を減らすと、その分の close / high / volume が変わる（＝不完全な足の素材）。
    分数を減らすと、その分が丸ごと無くなる（＝欠測の素材）。
    """
    return tuple(tuple(60 + m + k for k in range(n_ticks)) for m in range(n_minutes))


#: 修復の窓に置く日数（当日と前日＝周期の持ち主が渡す形・:func:`_scene` が作る窓の広さ）。
_WINDOW_DAYS = 2

#: 完全な 1 日（分数 × ティック本数）と、不完全に書かれた 1 日。
_FULL = _widths(6, 3)
_PARTIAL_TICKS = _widths(6, 2)   # 全分が在るがティックが 1 本足りない（不完全な足）。
_PARTIAL_MINUTES = _widths(4, 3)  # 末尾 2 分が丸ごと無い（欠測）。


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(data_dir: Path, days: "list[pd.Timestamp]", refs) -> None:
    """``days`` の全域から組の M1 CSV を全構築する（既存の口を通す＝期待値を手で組まない）。"""
    tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=refs, symbol=TICK_TREE, data_dir=data_dir
    )


def _heal(data_dir: Path, window: "list[pd.Timestamp]", refs) -> "dict[str, int]":
    return tick_m1.heal_m1_days_for_series(
        window, refs=refs, symbol=TICK_TREE, data_dir=data_dir
    )


class _Scene(NamedTuple):
    """突合の素材が揃った 2 つの置き場（修復対象と、同じ素材から全構築した参照）。"""

    broken: Path
    reference: Path
    window: "list[pd.Timestamp]"


def _scene(
    tmp_path: Path, refs, *, history_days: int, written: "tuple[tuple[int, ...], ...]"
) -> _Scene:
    """当日を ``written`` で書いた置き場と、全域を完全な素材で全構築した参照を作る。

    どちらの置き場も最終状態のティック実体は同一（``_FULL``）である。違うのは、修復対象の側では
    **当日の M1 が出揃う前の素材で書かれた**ことだけ——これが ISSUE-534 の形である。
    """
    broken, reference = tmp_path / "broken", tmp_path / "reference"
    days = [day(k) for k in range(history_days + 1)]
    for when in days[:-1]:                      # 履歴（窓の外）は両者とも完全。
        put_day_of_spread_points(broken, when, _FULL)
        put_day_of_spread_points(reference, when, _FULL)
    put_day_of_spread_points(broken, days[-1], written)   # 出揃う前の素材で…
    _build(broken, days, refs)                            # …M1 を書いてしまう。
    put_day_of_spread_points(broken, days[-1], _FULL)     # その後ティックが出揃う。
    put_day_of_spread_points(reference, days[-1], _FULL)
    _build(reference, days, refs)
    return _Scene(broken=broken, reference=reference, window=[days[-2], days[-1]])


# =====================================================================
# H-1. 不完全な足が直る
# =====================================================================
def test_a_bar_written_before_its_ticks_arrived_is_rewritten_from_the_material(tmp_path):
    """H-1: ティックの一部だけで書いた分が、修復後はティック実体と一致する（byte 一致）。"""
    # Arrange
    refs = _series_set()
    scene = _scene(tmp_path, refs, history_days=2, written=_PARTIAL_TICKS)
    before = {ref: _m1_path(ref, scene.broken).read_bytes() for ref in refs}

    # Act
    healed = _heal(scene.broken, scene.window, refs)

    # Assert
    assert {ref: _m1_path(ref, scene.broken).read_bytes() for ref in refs} == {
        ref: _m1_path(ref, scene.reference).read_bytes() for ref in refs
    }
    assert all(v > 0 for v in healed.values()), f"直した分が 0 と報告された: {healed}"
    assert any(before[ref] != _m1_path(ref, scene.broken).read_bytes() for ref in refs), (
        "素材が食い違っていたのに 1 バイトも変わっていない（この検定が空振りしている）"
    )


# =====================================================================
# H-2. 先端より前の穴が埋まる
# =====================================================================
def test_minutes_missing_from_the_middle_are_filled_from_the_material(tmp_path):
    """H-2: 丸ごと欠けた分が、修復後は素材どおりに埋まる（行数も byte も参照と一致）。"""
    # Arrange
    refs = _series_set()
    scene = _scene(tmp_path, refs, history_days=2, written=_PARTIAL_MINUTES)
    missing = len(_FULL) - len(_PARTIAL_MINUTES)

    # Act
    healed = _heal(scene.broken, scene.window, refs)

    # Assert
    assert {ref: _m1_path(ref, scene.broken).read_bytes() for ref in refs} == {
        ref: _m1_path(ref, scene.reference).read_bytes() for ref in refs
    }
    assert healed == {ref: missing for ref in refs}, (
        f"埋めた分が {healed}（欠けていたのは 1 系列あたり {missing} 分）"
    )


# =====================================================================
# H-3. 冪等（すでに一致している窓は 1 バイトも書かない）
# =====================================================================
def test_healing_a_window_that_already_agrees_writes_nothing(tmp_path, monkeypatch):
    """H-3: 一致している窓を修復に掛けても byte 一致で、書込の発行が 0 である。"""
    # Arrange
    refs = _series_set()
    scene = _scene(tmp_path, refs, history_days=2, written=_PARTIAL_TICKS)
    _heal(scene.broken, scene.window, refs)          # 1 回目で一致させる。
    digests = {ref: _sha256(_m1_path(ref, scene.broken)) for ref in refs}
    writes = spy(monkeypatch, tick_m1, "_replace_m1_tail")

    # Act
    healed = _heal(scene.broken, scene.window, refs)

    # Assert
    assert healed == {ref: 0 for ref in refs}
    assert {ref: _sha256(_m1_path(ref, scene.broken)) for ref in refs} == digests
    assert writes == [], f"一致している窓へ書込が {len(writes)} 回発行された"


# =====================================================================
# H-4. 有界（窓より前の日は書き換えない）
# =====================================================================
def test_days_before_the_window_are_left_alone_even_when_they_disagree(tmp_path):
    """H-4: 窓の外の日がティック実体と食い違っていても、その領域は 1 バイトも変わらない。

    「窓の外は触らない」を byte の同一だけで測ると、素材と一致していて当然の領域を測って
    しまう（空振り）。ここでは**窓の外を意図的に食い違わせて**おき、それが直らないことで
    有界を示す（直ってしまう実装は履歴全体を作り直している）。
    """
    # Arrange
    refs = _series_set()
    days = [day(k) for k in range(3)]
    data_dir = tmp_path / "broken"
    for when in days:
        put_day_of_spread_points(data_dir, when, _PARTIAL_TICKS)  # 全日を不完全に書く。
    _build(data_dir, days, refs)
    for when in days:
        put_day_of_spread_points(data_dir, when, _FULL)           # 全日のティックが出揃う。
    outside = days[0].strftime("%Y-%m-%d")
    before = {
        ref: [
            line for line in _m1_path(ref, data_dir).read_text(encoding="utf-8").splitlines()
            if line.startswith(outside)
        ]
        for ref in refs
    }

    # Act
    _heal(data_dir, [days[-2], days[-1]], refs)

    # Assert
    assert {
        ref: [
            line for line in _m1_path(ref, data_dir).read_text(encoding="utf-8").splitlines()
            if line.startswith(outside)
        ]
        for ref in refs
    } == before
    assert all(rows for rows in before.values()), "窓の外の行が 0 件（この検定が空振りしている）"


# =====================================================================
# H-5. 組の全系列に効く
# =====================================================================
def test_every_series_of_the_set_is_healed(tmp_path):
    """H-5: 列の上位集合とその列を持たない側の**両方**が直る（片方だけ直すと系列間が食い違う）。"""
    # Arrange
    refs = _series_set()
    scene = _scene(tmp_path, refs, history_days=1, written=_PARTIAL_TICKS)

    # Act
    healed = _heal(scene.broken, scene.window, refs)

    # Assert
    assert len(refs) >= 2, f"同じ木を読む系列が {len(refs)} 件（この検定が空振りしている）"
    assert set(healed) == set(refs)
    assert all(v > 0 for v in healed.values()), f"直った系列が揃っていない: {healed}"
    assert {ref: _m1_path(ref, scene.broken).read_bytes() for ref in refs} == {
        ref: _m1_path(ref, scene.reference).read_bytes() for ref in refs
    }


# =====================================================================
# H-6. 素材に無い分は消さない
# =====================================================================
def test_minutes_absent_from_the_material_are_not_deleted(tmp_path, caplog):
    """H-6: 素材が縮んだ（M1 に在る分がティックに無い）ときは書き換えず、警告を残す。

    修復が足を**消す**方向に働くと、一過性の取得不足がそのまま確定データの欠落になる。減る方向の
    食い違いは素材側の欠陥であり、突合では**どちらが正しいか決められない**。止めて人へ見せる。
    """
    # Arrange
    refs = _series_set()
    days = [day(k) for k in range(2)]
    data_dir = tmp_path / "broken"
    for when in days:
        put_day_of_spread_points(data_dir, when, _FULL)
    _build(data_dir, days, refs)
    put_day_of_spread_points(data_dir, days[-1], _PARTIAL_MINUTES)  # 素材が縮む。
    digests = {ref: _sha256(_m1_path(ref, data_dir)) for ref in refs}

    # Act
    with caplog.at_level("WARNING"):
        healed = _heal(data_dir, [days[-1]], refs)

    # Assert
    assert healed == {ref: 0 for ref in refs}
    assert {ref: _sha256(_m1_path(ref, data_dir)) for ref in refs} == digests
    assert caplog.records, "素材に無い分を見つけたのに無言で通した"


# =====================================================================
# H-7 / H-8 / H-9. Green で先取り実装した「書かない条件」の回帰の壁
# =====================================================================
# 記録（TDD の順序についての正直な申し送り）: 以下 3 件は step S-4（Green）で最小実装を超えて
#   書いた守り（AP.2 G-1 過剰実装）であり、**Red を観測していない**（テストを書いた時点で既に
#   通った＝原因分類 ① 過剰実装）。撤去でなく検定の追加を選んだのは、3 件いずれも「修復が
#   データを壊す経路」を塞いでいるためである（塞がないと、突合が欠落や列崩れを作る）。
#   検出力は変異試験で実測する（``tools/tests`` 外の一時スクリプト・報告に記載）。


def test_h7_a_day_without_material_at_the_head_does_not_block_the_days_after_it(tmp_path):
    """H-7: 素材（日別 parquet）が無い日が窓の先頭に混ざっても、その後ろの日は突合できる。

    素材が無い日は「M1 が正しいか」を決める権威が無いので突合できない。その日を窓に残したまま
    突合すると、その日の既存 M1 行が「素材に無い分」となり H-6 の守りで**窓ごと**見送りになる
    ——つまり素材が 1 日欠けるだけで当日の修復が永久に止まる。窓は末尾から見て素材が揃っている
    並びへ刈り込む。
    """
    # Arrange
    refs = _series_set()
    days = [day(k) for k in range(3)]
    data_dir = tmp_path / "broken"
    for when in days:
        put_day_of_spread_points(data_dir, when, _FULL)
    _build(data_dir, days, refs)
    put_day_of_spread_points(data_dir, days[-1], _PARTIAL_TICKS)
    _build(data_dir, days, refs)                       # 当日を不完全に書き直す。
    put_day_of_spread_points(data_dir, days[-1], _FULL)
    tick_m1.day_parquet_path(
        days[-2], symbol=TICK_TREE, data_dir=data_dir
    ).unlink()                                         # 窓の先頭の素材が無い。

    # Act
    healed = _heal(data_dir, [days[-2], days[-1]], refs)

    # Assert
    assert all(v > 0 for v in healed.values()), (
        f"素材が無い日が窓に混ざったら当日の修復も止まった: {healed}"
    )


def test_h8_a_column_shape_that_disagrees_with_the_existing_header_is_not_written(
    tmp_path, caplog
):
    """H-8: 既存ヘッダと素材の列が食い違うときは書かない（追記側の全構築へ委ねる）。

    ここで書くと 6 列ヘッダの下に 8 列行を積む（ISSUE-455 の再来: 以後その系列の読取が全部落ちる）。
    """
    # Arrange
    ref = _series_set()[0]
    days = [day(k) for k in range(2)]
    data_dir = tmp_path / "broken"
    for when in days:
        put_day_of_spread_points(data_dir, when, _PARTIAL_TICKS)
    _build(data_dir, days, (ref,))
    put_day_of_spread_points(data_dir, days[-1], _FULL)
    path = _m1_path(ref, data_dir)
    narrowed = pd.read_csv(path).drop(columns=["up", "dn"])   # 旧 6 列 CSV を作る。
    narrowed.to_csv(path, index=False)
    digest = _sha256(path)

    # Act
    with caplog.at_level("WARNING"):
        healed = tick_m1.heal_m1_days_for_series(
            days, refs=(ref,), symbol=TICK_TREE, data_dir=data_dir
        )

    # Assert
    assert healed == {ref: 0}
    assert _sha256(path) == digest
    assert caplog.records, "列が食い違ったのに無言で通した"


def test_h9_a_window_whose_days_are_not_consecutive_is_refused(tmp_path):
    """H-9: 窓の日が連続していなければ書かずに止める（fail-fast）。

    間が空いた窓を通すと、素材を読まない日が窓の中に生まれ、その日の既存 M1 行を書き直しの
    巻き添えで失う。受け取ってから守るのではなく、受け取らない。
    """
    # Arrange
    refs = _series_set()
    days = [day(k) for k in range(3)]
    data_dir = tmp_path / "broken"
    for when in days:
        put_day_of_spread_points(data_dir, when, _FULL)
    _build(data_dir, days, refs)

    # Act / Assert
    with pytest.raises(ValueError, match="連続"):
        _heal(data_dir, [days[0], days[2]], refs)


# =====================================================================
# CX-1. 素材側の費用が履歴の長さ・系列数で増えない
# =====================================================================
class _Issued(NamedTuple):
    """1 回の修復で観測した発行（parquet 読込・畳み・既存 M1 の読取バイト）と使用（素材の数）。"""

    reads: int
    folds: int
    tail_bytes: int
    used: int


def _spy_reads_and_folds(monkeypatch) -> "tuple[list, list]":
    """parquet の読込と唯一の畳み点を包む（畳みは共通前段で数える＝入口で数えると 2 回が 1 に見える）。"""
    return (
        spy(monkeypatch, pd, "read_parquet"),
        spy(monkeypatch, tick_m1, "_fold_ticks"),
    )


def _spy_tail_bytes(monkeypatch) -> "list[int]":
    """既存 M1 の逆シーク読取を包み、読んだバイト数を記録する。

    継ぎ目を :mod:`marketdata.tail_reader` の ``open`` に取るのは、修復が既存 M1 を読む経路が
    逆シークの唯一源だけであり、全件読み（``pd.read_csv`` / ``read_text``）へ戻す変異がこの
    継ぎ目の**外**へ出ることで観測できるためである（継ぎ目の内側だけを数えると、外へ逃げた
    読込を見逃す）。よって本検定は「バイト数が履歴で増えない」と「窓の行が実際に読まれている」
    の両方を測る。
    """
    sizes: "list[int]" = []
    real_open = open

    class _Counting:
        """読んだバイト数を記録するファイルの包み（``with`` も委譲する）。"""

        def __init__(self, fh) -> None:
            self._fh = fh

        def read(self, *args):
            chunk = self._fh.read(*args)
            sizes.append(len(chunk))
            return chunk

        def readline(self, *args):
            line = self._fh.readline(*args)
            sizes.append(len(line))
            return line

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def counting_open(*args, **kwargs):
        return _Counting(real_open(*args, **kwargs))

    monkeypatch.setattr(tail_reader, "open", counting_open, raising=False)
    return sizes


def _issued(monkeypatch, tmp_path: Path, refs, *, history_days: int) -> _Issued:
    """``history_days`` 日の履歴を持つ置き場を 2 日の窓で修復したときの発行と使用。"""
    scene = _scene(
        tmp_path / f"h{history_days}_{len(tuple(refs))}", refs,
        history_days=history_days, written=_PARTIAL_TICKS,
    )
    monkeypatch.setattr(tail_reader, "_BLOCK_SIZE", 256)
    reads, folds = _spy_reads_and_folds(monkeypatch)
    sizes = _spy_tail_bytes(monkeypatch)
    _heal(scene.broken, scene.window, refs)
    used = len(
        tick_m1.day_parquet_files(
            scene.window[0], scene.window[-1], symbol=TICK_TREE, data_dir=scene.broken
        )
    )
    return _Issued(
        reads=len(reads), folds=len(folds), tail_bytes=sum(sizes), used=used
    )


@pytest.mark.parametrize("n_refs", [1, 2])
def test_cx1_the_heal_reads_and_folds_only_the_window(tmp_path, monkeypatch, n_refs):
    """CX-1: 素材の発行は窓の日数で決まり、履歴 2 点・系列 1 → 2 で増えない。発行 − 使用 = 0。

    固定するのは**無駄の不在**であり、回数そのもの（N 回）は期待値に焼き込まない（期待値は窓に
    実在する parquet の数から導く）。履歴全体を作り直す実装・系列ごとに畳む実装は、
    ここで短い履歴と長い履歴の差、または系列 1 と 2 の差として現れる。
    """
    # Arrange / Act
    refs = _series_set()[:n_refs]
    short = _issued(monkeypatch, tmp_path / "short", refs, history_days=2)
    long_ = _issued(monkeypatch, tmp_path / "long", refs, history_days=20)

    # Assert
    assert short.used == _WINDOW_DAYS  # 窓に素材が実在する（空振り防止）
    assert (short.reads - short.used, short.folds - short.used) == (0, 0)
    assert (long_.reads - long_.used, long_.folds - long_.used) == (0, 0), (
        f"履歴 20 日で 読込 {long_.reads} / 畳み {long_.folds} − 使用 {long_.used} ≠ 0"
    )
    assert (long_.reads, long_.folds) == (short.reads, short.folds), (
        f"履歴を 2 日 → 20 日にしたら 読込 {short.reads} → {long_.reads} /"
        f" 畳み {short.folds} → {long_.folds} へ増えました（履歴を作り直しています）。"
    )


def test_cx1_the_heal_does_not_fold_more_when_the_set_grows(tmp_path, monkeypatch):
    """CX-1（系列軸）: 系列 1 → 2 で素材の発行が増えない（系列ごとに読み直さない）。"""
    # Arrange / Act
    one = _issued(monkeypatch, tmp_path / "one", _series_set()[:1], history_days=2)
    two = _issued(monkeypatch, tmp_path / "two", _series_set(), history_days=2)

    # Assert
    assert len(_series_set()) >= 2
    assert (two.reads, two.folds) == (one.reads, one.folds), (
        f"系列を 1 → {len(_series_set())} にしたら 読込 {one.reads} → {two.reads} /"
        f" 畳み {one.folds} → {two.folds} へ増えました。"
    )


# =====================================================================
# CX-2. 既存 M1 側の費用が履歴の長さで増えない
# =====================================================================
def test_cx2_the_heal_reads_the_window_of_the_existing_m1_not_the_whole_history(
    tmp_path, monkeypatch
):
    """CX-2: 既存 M1 から読んだバイト数が履歴 2 点で等しい（全件読みへ戻す変異が現れる）。"""
    # Arrange / Act
    short = _issued(monkeypatch, tmp_path / "short", _series_set(), history_days=2)
    long_ = _issued(monkeypatch, tmp_path / "long", _series_set(), history_days=20)

    # Assert
    assert short.tail_bytes > 0, "既存 M1 を 1 バイトも読んでいない（窓を突合していない）"
    assert long_.tail_bytes == short.tail_bytes, (
        f"履歴を 2 日 → 20 日にしたら既存 M1 の読取が {short.tail_bytes} → "
        f"{long_.tail_bytes} バイトへ増えました（窓でなく履歴を読んでいます）。"
    )
