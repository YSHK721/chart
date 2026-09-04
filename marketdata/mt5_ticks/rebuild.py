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

依存宣言: pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.outlier_policy` /
:mod:`marketdata.rollup` / :mod:`marketdata.mt5_ticks` 下位。
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from marketdata import outlier_policy, tick_m1
from marketdata.mt5_ticks import ingest, m1_chain
from marketdata.mt5_ticks.port import Mt5SupplyError

#: 差が無かった（1 バイトも書いていない）。
UNCHANGED = "unchanged"
#: 当日区間を権威の内容へ置換した。
REPLACED = "replaced"
#: 素材（確定 parquet）か対象（M1 CSV）が無いので、やることが無い。
MISSING = "missing"

#: 日次クリーニングの**唯一の実装**（``tick_m1`` の私的ラッパと同一の関数を指す）。
#: ここで参照を持つのは「第 2 実装を作らない」ためであり、同一性は検定が固定する。
clean_day_m1 = outlier_policy.repair_day_outliers


def authoritative_day_m1(day: Any, *, symbol: str, data_dir: Any) -> pd.DataFrame:
    """確定 parquet から当日の M1 を**権威経路と同じ計算**で作る。

    :func:`marketdata.tick_m1.build_m1_from_ticks` の日別段（集計 → 日次クリーニング）と
    同一である。同一性は検定（全量経路との突合）が固定する。

    列を射影せずに読むのは、この parquet が :func:`marketdata.mt5_ticks.ingest.rows_to_frame`
    の出力そのもの＝権威の 3 列しか持たないためである（列の権威をここに書き写さない）。
    日別結果の重複畳み（同一分の keep-last）は不要である。それは全量経路が**複数日の M1 を
    連結する**ときに境界分が二重になるための処置であり、ここは 1 日 1 parquet しか読まない
    （単一 parquet 内の分は :func:`marketdata.tick_m1.ticks_to_m1` の groupby で一意になる）。

    価格基準は増分経路と同じ :data:`marketdata.mt5_ticks.ingest.PRICE_BASIS` を渡す。ここが
    既定（mid）のままだと、日次確定のたびに再構築が「差がある」と判定して当日区間を mid へ
    書き戻す。値はどちらも「それらしい」ので、置換されたことにも気付けない。
    """
    parquet = tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir)
    return clean_day_m1(
        tick_m1.ticks_to_m1(pd.read_parquet(parquet), price_basis=ingest.PRICE_BASIS)
    )


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


def rebuild_day(
    day: Any, *, symbol: str, ref: str, data_dir: Any, update_rollups: bool = True
) -> str:
    """閉じた UTC 日 ``day`` を権威経路で作り直し、差分がある場合だけ置換する。

    戻り値は :data:`UNCHANGED` / :data:`REPLACED` / :data:`MISSING`。
    """
    m1_path = tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)
    parquet = tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir)
    if not m1_path.is_file() or not parquet.is_file():
        return MISSING

    expected = authoritative_day_m1(day, symbol=symbol, data_dir=data_dir)
    current = _read_m1_csv(m1_path)
    start = pd.Timestamp(dt.datetime.combine(pd.Timestamp(day).date(), dt.time(0, 0)))
    end = start + pd.Timedelta(days=1)
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
