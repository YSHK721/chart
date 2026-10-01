"""UTC 日が閉じた後の M1 再構築（adapter E・設計 §10 の裁定＝案 b）。

なぜ再構築が要るのか:
    日中の M1 は「閉じた分の新着ティックだけを畳む」増分経路（:mod:`marketdata.mt5_ticks.m1_chain`）
    が作る。その経路には**日次クリーニング**（日内 close 中央値から ±30% 乖離する分バーの除去・
    ISSUE-107）を適用できない。中央値は日単位の統計であり、数本のバーの中央値は日の中央値では
    ないからである。よって日中の M1 は**暫定値**であり、外れ値を含む日は権威（全量経路）と
    食い違う（TDD 工程の実測: 外れ値日で増分 10 バー対 全量 8 バー。清浄日はバイト一致）。

    裁定は「日次確定時に再構築」である。UTC 日が閉じ確定 parquet が出来た時点で、権威経路と
    同じ計算で当日を作り直し、**差分がある日だけ**該当日区間を置換する。清浄日は書込 0
    （計算量検定 CX-b と整合）。確定記録は既存権威と完全に一致する。

差分が無い日に書かない理由:
    毎日 1 回でも無条件に全体を書き直せば、それは出力を変えない書込＝「作ってから捨てる」
    計算であり、常駐の固定費になる（ISSUE-450 と同型）。差の有無は書く前に判定する。

派生ロールアップを権威の再生成に委ねる理由:
    日次クリーニングが行う操作は**分バーの除去**である。除去された分を含む上位足バーは
    high/low/volume が変わるため、既存バーと新バーの「マージ」では是正できない
    （:func:`marketdata.rollup.incremental_update` の合流は open=first/high=max/… の**合算**で
    あり、消えたはずの外れ値が high に残る）。区間を差し替える公開 API は rollup 側に無く、
    自前で CSV を切り貼りすればロールアップのレイアウト規則の第 2 実装を作ることになる。
    よって是正が要る日に限り :func:`marketdata.rollup.stream_build`（権威・原子的）で
    ref 配下を作り直す。代償は「是正が要る日だけ O(M1 全体)」であり、清浄日は 0 である。

読む parquet は当日 1 個だけである（保存済み日数に比例しない）。

依存宣言: pandas / :mod:`marketdata.tick_m1` /
:mod:`marketdata.rollup` / :mod:`marketdata.mt5_ticks` 下位。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import pandas as pd

from marketdata import tick_m1
from marketdata.mt5_ticks import m1_chain, server_clock
from marketdata.mt5_ticks.port import Mt5SupplyError

#: 差が無かった（1 バイトも書いていない）。
UNCHANGED = "unchanged"
#: 当日区間を権威の内容へ置換した。
REPLACED = "replaced"
#: 素材（確定 parquet）か対象（M1 CSV）が無いので、やることが無い。
MISSING = "missing"


def authoritative_day_m1_for_series(
    day: Any, *, symbol: str, refs: "Sequence[str]", data_dir: Any
) -> "dict[str, pd.DataFrame]":
    """確定 parquet から当日の M1 を、案内の**全系列**ぶん作る（ISSUE-511 段階 8-D-2b の段 3）。

    読むのは当日の parquet **1 個だけ**、畳むのも **1 回だけ**である（系列ごとに素材化を呼ぶと
    同じティックを系列の数だけ畳む。出力は正しいままなので状態検証では原理的に落ちない・
    ISSUE-450 と同型）。その不在は ``marketdata/tests/test_mt5_series_fan_out.py`` の C-3 が
    parquet 読取と唯一の畳み点の発行で固定する。

    案内（:func:`marketdata.tick_m1.series_plan`）は価格基準と spread 宣言の一致を先に照合し、
    既存 CSV の列形とも突き合わせる。価格基準は**渡さない**（唯一の源は台帳・段階 6・TBD-4）。
    """
    plan = tick_m1.series_plan(refs, data_dir=data_dir)
    parquet = tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir)
    return tick_m1.materialize_m1_day_for_series(pd.read_parquet(parquet), plan=plan)


def authoritative_day_m1(day: Any, *, symbol: str, ref: str, data_dir: Any) -> pd.DataFrame:
    """確定 parquet から当日の M1 を**権威経路と同じ計算**で作る。

    1 要素の組を :func:`authoritative_day_m1_for_series` へ通す**薄い包み**である
    （ISSUE-511 段階 8-D-2b の段 3）。名前・シグネチャ・戻り値は変えていない。

    1 日分の素材化（畳む → 日次クリーニング → 残った分だけ気配幅）は :mod:`marketdata.tick_m1`
    の素材化の口に委ねる（順序を手書き複製しない・ISSUE-511 段階 3 前提 (c)）。spread 列の有無と point は ``ref`` の台帳宣言が決める。全量経路との
    同一性は検定（全量経路との突合）が固定する。

    列を射影せずに読むのは、この parquet が :func:`marketdata.mt5_ticks.ingest.rows_to_frame`
    の出力そのもの＝権威の 3 列しか持たないためである（列の権威をここに書き写さない）。
    日別結果の重複畳み（同一分の keep-last）は不要である。それは全量経路が**複数日の M1 を
    連結する**ときに境界分が二重になるための処置であり、ここは 1 日 1 parquet しか読まない
    （単一 parquet 内の分は分 groupby で一意になる）。

    価格基準は**渡さない**。唯一の源は台帳（``ref`` の記述子の ``price_basis``）であり、増分経路
    （:mod:`marketdata.mt5_ticks.m1_chain`）も同じ源から引く（ISSUE-511 段階 3 の段階 6・TBD-4）。
    かつては本モジュールと増分経路が同じ定数を渡していたが、台帳と定数の 2 源が残ると、台帳だけを
    切り替えたときに日中経路が旧基準で走り、日次確定のたびに再構築が「差がある」と判定して当日
    区間を旧基準へ書き戻す。値はどちらも「それらしい」ので、置換されたことにも気付けない。
    """
    return authoritative_day_m1_for_series(
        day, symbol=symbol, refs=(ref,), data_dir=data_dir
    )[ref]


def _read_m1_csv(path: Path) -> pd.DataFrame:
    """M1 CSV を date-index の DataFrame として読む。"""
    frame = pd.read_csv(path)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame["date"]), name="date")
    return frame.drop(columns=["date"])


def _same_bars(current: pd.DataFrame, expected: pd.DataFrame) -> bool:
    """当日区間が既に権威と一致しているか（一致していれば書かない）。"""
    if not current.index.equals(expected.index):
        return False
    if list(current.columns) != list(expected.columns):
        return False
    return current.astype("float64").equals(expected.astype("float64"))


def _refuse_incompatible_columns(
    current: pd.DataFrame, expected: pd.DataFrame, *, path: Path
) -> None:
    """列構成が食い違う CSV へ当日区間を挿し込まない（Fail-Stop）。

    連結してから整形すると、欠けている列は NaN になり、置換したい当日だけでなく**当日以外の
    行**まで空欄付きで書き直される。出力を壊すより先に止める。

    列形の防御は 2 つあり、**統合しない**（見る入力が違う・ISSUE-511 段階 3 の段階 6 の申し送り）:
    本関数は「既に読み込んだ CSV の**全列**」と「権威が組み立てた当日区間の全列」を突き合わせ、
    過不足があれば :class:`marketdata.mt5_ticks.port.Mt5SupplyError` を送出する。もう一方の
    :func:`marketdata.tick_m1._assert_spread_schema` は「系列の**宣言**」と「既存 CSV の先頭行」を
    突き合わせ、spread 列の有無だけを見て
    :class:`marketdata.tick_m1.SpreadSchemaMismatch` を送出する（書く前に止める）。前者は連結時に
    NaN が生える食い違い、後者は宣言との食い違いを見ており、片方に寄せるともう片方の入力が
    手に入らない（本関数は宣言を知らず、あちらは読み込んだ本文を持たない）。
    """
    missing = [c for c in expected.columns if c not in current.columns]
    extra = [c for c in current.columns if c not in expected.columns]
    if missing or extra:
        raise Mt5SupplyError(
            f"M1 CSV の列構成が権威と食い違います（{path}）: 足りない={missing} 余分={extra}。"
            " 当日区間だけを差し替えると他の日が空欄付きで書き直されるため中断します。"
            " 全構築（build_m1_from_ticks）で作り直してください。"
        )


def _write_m1_atomically(frame: pd.DataFrame, path: Path) -> None:
    """全行を tmp へ書いてから ``os.replace`` で確定パスへ差し替える。

    区間の置換はファイルの途中を書き換える操作であり、途中で落ちれば行が半分だけ入れ替わった
    CSV が残る。確定パスを「完全な新 CSV」か「旧 CSV」のいずれかに限定する。書式は公開 API
    :func:`marketdata.tick_m1.append_m1_rows` に委ねる（第 2 定義を作らない）。
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tick_m1.append_m1_rows(frame, tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _regenerate_rollups(*, ref: str, data_dir: Any) -> None:
    """ref 配下のロールアップを権威で作り直す（既に存在する場合のみ）。

    ロールアップがまだ無い ref に対してここで作り始めない（再構築は是正であって生成ではない）。
    """
    from marketdata import rollup  # 遅延 import: 是正が要る日だけ重い依存を触る。

    out_dir = m1_chain.rollup_dir(ref=ref, data_dir=data_dir)
    if rollup.RollupState.load(out_dir) is None:
        return
    m1_path = tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)
    rollup.stream_build(m1_path, rollup.rollup_timeframes(), out_dir, ref).save(out_dir)


def rebuild_day_for_series(
    day: Any, *, symbol: str, refs: "Sequence[str]", data_dir: Any,
    update_rollups: bool = True
) -> "dict[str, str]":
    """閉じた UTC 日 ``day`` を、組の**全系列**について権威経路で作り直す（段 8-D-2b の段 3）。

    当日の parquet を読んで畳むのは **1 回だけ**で（:func:`authoritative_day_m1_for_series`）、
    その結果を系列ごとの M1 CSV へ突き合わせる。差が無い系列には 1 バイトも書かない
    （清浄日は書込 0＝計算量検定 CX-b と整合）。

    素材（当日 parquet）か対象（その系列の M1 CSV）が無い系列は :data:`MISSING` である。
    **どの系列にも対象が無ければ parquet を読まない**（是正の当てが無いのに畳むのは、出力に
    使わない計算そのもの）。案内は「対象が在る系列」だけで組む——書かない系列の列形まで照合すると、
    まだ作っていない系列の宣言で当日の是正が止まる。

    戻り値は ref ごとの :data:`UNCHANGED` / :data:`REPLACED` / :data:`MISSING`。
    """
    refs = tuple(refs)
    paths = {ref: tick_m1.m1_csv_path(ref=ref, data_dir=data_dir) for ref in refs}
    parquet = tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir)
    present = [ref for ref in refs if paths[ref].is_file()]
    if not present or not parquet.is_file():
        return {ref: MISSING for ref in refs}

    expected = authoritative_day_m1_for_series(
        day, symbol=symbol, refs=present, data_dir=data_dir
    )
    outcome = {ref: MISSING for ref in refs}
    for ref in present:
        outcome[ref] = _replace_day_if_changed(
            day, expected[ref], path=paths[ref], ref=ref, data_dir=data_dir,
            update_rollups=update_rollups,
        )
    return outcome


def rebuild_day(
    day: Any, *, symbol: str, ref: str, data_dir: Any, update_rollups: bool = True
) -> str:
    """閉じた UTC 日 ``day`` を権威経路で作り直し、差分がある場合だけ置換する。

    1 要素の組を :func:`rebuild_day_for_series` へ通す**薄い包み**である（段 8-D-2b の段 3）。
    名前・シグネチャ・戻り値は変えていない。

    戻り値は :data:`UNCHANGED` / :data:`REPLACED` / :data:`MISSING`。
    """
    return rebuild_day_for_series(
        day, symbol=symbol, refs=(ref,), data_dir=data_dir, update_rollups=update_rollups
    )[ref]


def _replace_day_if_changed(
    day: Any, expected: pd.DataFrame, *, path: Path, ref: str, data_dir: Any,
    update_rollups: bool
) -> str:
    """1 系列の当日区間を権威の内容へ置換する（差が無ければ 1 バイトも書かない）。

    :func:`rebuild_day_for_series` から系列ごとに呼ぶ。1 日分の畳みは呼出側が既に済ませており、
    ここは「読んだ CSV と権威の当日区間を突き合わせて、違うときだけ原子的に差し替える」だけを
    行う（判定・切り貼り・置換の規則は本関数 1 つが持つ＝系列が増えても第 2 定義を作らない）。
    """
    m1_path = path
    current = _read_m1_csv(m1_path)
    # 日窓 ``[真夜中, 翌日の真夜中)`` の定義は :mod:`server_clock` が唯一源である
    # （ISSUE-502 D-15）。ここで真夜中を自前で組むと第 2 定義になり、片方だけ直した日に
    # 「置換する区間」が確定した日 partition と 1 日ずれる。M1 CSV の index は naive UTC
    # なので、epoch ms から naive な ``Timestamp`` を起こして突き合わせる。
    day_date = pd.Timestamp(day).date()
    start = pd.Timestamp(server_clock.utc_day_start_ms(day_date), unit="ms")
    end = pd.Timestamp(server_clock.utc_day_end_ms(day_date), unit="ms")
    inside = current[(current.index >= start) & (current.index < end)]
    if _same_bars(inside, expected):
        return UNCHANGED

    before = current[current.index < start]
    after = current[current.index >= end]
    _refuse_incompatible_columns(current, expected, path=m1_path)
    _write_m1_atomically(pd.concat([before, expected, after]), m1_path)
    if update_rollups:
        _regenerate_rollups(ref=ref, data_dir=data_dir)
    return REPLACED


def rebuild_days(
    days: "Optional[Iterable[Any]]" = (), *, symbol: str, ref: str, data_dir: Any,
    update_rollups: bool = True
) -> "dict[Any, str]":
    """複数日を昇順に再構築する（常駐ループが確定した日をそのまま渡せる形）。"""
    return {
        day: rebuild_day(
            day, symbol=symbol, ref=ref, data_dir=data_dir, update_rollups=update_rollups
        )
        for day in sorted(set(days or ()))
    }


def rebuild_days_for_series(
    days: "Optional[Iterable[Any]]" = (), *, symbol: str, refs: "Sequence[str]", data_dir: Any,
    update_rollups: bool = True
) -> "dict[Any, dict[str, str]]":
    """複数日を昇順に、組の全系列について再構築する（段 8-D-2b の段 3）。

    日をまたいで束ねられる計算は無い（確定 parquet は日ごとに別のファイルである）。束ねるのは
    「1 日を系列の数だけ読んで畳む」ことの方であり、それは :func:`rebuild_day_for_series` が
    担う。
    """
    return {
        day: rebuild_day_for_series(
            day, symbol=symbol, refs=refs, data_dir=data_dir, update_rollups=update_rollups
        )
        for day in sorted(set(days or ()))
    }
