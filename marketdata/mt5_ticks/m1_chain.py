"""閉じた分だけの M1 追記と rollup 差分更新（adapter E）。

``tick_m1.append_m1_from_ticks`` を使わない理由（P5 棄却）:
    それは最終バー日の**日別 parquet を丸ごと読み直して**再集計する。当日はその parquet が
    まだ無く、作れば 1 周期ごとに当日全量を再計算することになる（当日累積に比例＝CX-d 違反）。
    当日の M1 化は「閉じた分の新着ティックだけを畳む」本モジュールが担う。

形成中の分を持ち越す理由:
    1 つの分バーのティックは複数のポーリング周期にまたがって届く。その周期のぶんだけで確定
    させると**途中までのバー**が確定値として CSV に入る。よって ``until``（通常
    ``floor(now, "min")``）以降の行は確定させず :attr:`AppendResult.pending_rows` で呼び出し側へ
    返し、次の周期の入力に混ぜる。畳みへ渡る行数は常に「閉じた分のティック数」に等しい（CX-f）。

日次クリーニングとの非対称（裁定済み・設計 §10）:
    :func:`marketdata.tick_m1.build_m1_from_ticks` は日別 M1 へ日内 close 中央値からの ±30%
    乖離バー除去（ISSUE-107）を適用する。これは**日単位の統計**を要するため、分単位の増分では
    同じ判断ができない（数本のバーの中央値は日の中央値ではない）。本モジュールは日次
    クリーニングを**適用しない**＝日中の M1 は暫定値である。UTC 日が閉じた時点で
    ``marketdata/mt5_ticks/rebuild.py`` が権威経路で当日を再計算し、差分がある日だけ
    該当日区間を原子置換する（案 b・2026-09-01 裁定）。この非対称を隠さないために明記する。

書式の権威:
    追記は ``tick_m1.append_m1_rows``（公開 API）へ委譲する（第 2 定義を作らない・検定 M-3）。
    かつては private の整形関数を直接 import していたが、承認事項 A-5 によりその依存は
    恒久解消した（``marketdata/tests/test_mt5_m1_append_api.py`` が AST で再発を禁じる）。

系列の組（ISSUE-511 段階 8-D-2b の段 3）:
    追記は「1 つのティック列 → 系列の集合（refs）」を受ける。畳みは案内
    （``tick_m1.series_plan``）を 1 つ組んで ``tick_m1.fold_ticks_for_series`` へ **1 回だけ**
    発行し、spread 列を持たない系列へは列を落とした射影が配られる。行選択の下限は
    ``min(先端)`` で 1 回だけ決め、各系列へは自分の先端より後の分バーだけを追記する
    （設計 D-1）。既存の 1 系列の口（``append_m1_for_closed_minutes``）は 1 要素の薄い包みとして
    名前も戻り値も変わらない。

列形の権威（ISSUE-511 段階 3 の段階 5・V-2）:
    畳みは ``tick_m1`` の系列版の畳み口へ委譲し、``ref`` を渡す。spread 列の有無と、その列を数える
    point は**台帳の宣言**（``marketdata.dataset_registry`` の ``spread_point_snapshot``）が決め、
    既存 M1 CSV の列形との照合は書き手の入口（``tick_m1.build_m1_from_ticks`` /
    ``tick_m1.append_m1_from_ticks``）と同じ規則を通る。かつて本モジュールは ``ref`` を渡さない
    唯一の畳み口であり、照合を迂回していた。迂回したままで台帳が spread を宣言すると、日中追記
    だけが宣言と違う列形を書こうとして ISSUE-455 のヘッダ不一致 ``ValueError`` で落ちる
    （常駐の捕捉集合の外＝traceback のまま exit 1・段階 4 で実測）。

依存宣言: pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.rollup` /
:mod:`marketdata.rollup_paths`（ロールアップ配置の唯一権威・ISSUE-502 D-16）/
:mod:`marketdata.dataset_registry`（保存物の名前＝series・ISSUE-511 段階 1d）/
:mod:`marketdata.mt5_ticks` 下位。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, NamedTuple, Optional, Sequence, Tuple

import pandas as pd

from marketdata import dataset_registry, rollup_paths, tick_m1
from marketdata.mt5_ticks import ingest, server_clock

Row = Tuple[int, float, float]

#: rollup の出力先ディレクトリ名（``<data_dir>/rollups/<ref>/``）。綴りの唯一の所有者は
#: :mod:`marketdata.rollup_paths`（ISSUE-502 D-16）。本名は既存参照の互換のための別名である。
ROLLUP_DIRNAME = rollup_paths.ROLLUPS_DIRNAME


class AppendResult(NamedTuple):
    """追記結果。``pending_rows`` は次の周期へ持ち越す形成中の分の行。"""

    bars: int
    pending_rows: "List[Row]"


class SeriesAppendResult(NamedTuple):
    """系列の組への追記結果（ISSUE-511 段階 8-D-2b の段 3）。

    ``bars`` は ref ごとの追記本数。``pending_rows`` は**組に 1 本**である——形成中の分
    （``until`` 以降）は ref に依らないので、系列ごとに持ち越しを複製すると次の周期で同じ
    ティックを系列の数だけ畳むことになる。
    """

    bars: "dict[str, int]"
    pending_rows: "List[Row]"


def rollup_dir(*, ref: str, data_dir: Any) -> Path:
    """``ref`` のロールアップ出力ディレクトリ（配置権威へ委譲・ISSUE-502 D-16）。"""
    return rollup_paths.ref_dir(ref, data_dir=data_dir)


def _utc_minute(row: Row) -> pd.Timestamp:
    """行が属する UTC 分（畳みの分割キー）。**分境界の定義はここ 1 箇所**である。

    再種付け（:func:`rows_of_last_minute`）と閉じた分の判定が別の定義を持つと、
    「種にした行」と「畳まれる行」の分け方が食い違い、境界分の欠け・重複が静かに戻る。
    """
    return pd.Timestamp(server_clock.to_utc_ms(row[0]), unit="ms", tz="UTC").floor("min")


def rows_of_last_minute(rows: "Sequence[Row]") -> "List[Row]":
    """``rows`` のうち**最後の UTC 分**（＝形成中だった境界分）に属する行だけを返す。

    用途は再起動時の pending 再種付け（ISSUE-477・UC-06）である。pending はメモリにしか
    無く再起動で失われるため、ジャーナル末尾からこの分の全行を読み直して最初の周期の畳みに
    混ぜる。分の定義は畳み（:func:`append_m1_for_closed_minutes`）と同じ :func:`_utc_minute`
    であり、第 2 の分境界定義を作らない。
    """
    rows = list(rows)
    if not rows:
        return []
    minutes = [_utc_minute(r) for r in rows]
    last = max(minutes)
    return [r for r, minute in zip(rows, minutes) if minute == last]


def _settled_minute(path: Path) -> "Optional[pd.Timestamp]":
    """既存 M1 CSV の先端（最終 date）を **tz-aware な UTC** で返す。不在・空は ``None``。

    M1 CSV の date は naive=UTC（既存契約）であり、読み替えを使う側それぞれに書くと、片方だけ
    直った瞬間に「確定済みの分」の判断が経路ごとに 1 分ずれる。読み替えはここ 1 箇所である。
    """
    settled = tick_m1.last_m1_date(path)
    if settled is not None and settled.tzinfo is None:
        settled = settled.tz_localize("UTC")
    return settled


def append_m1_for_closed_minutes_for_series(
    rows: "Sequence[Row]", *, refs: "Sequence[str]", data_dir: Any, until: Any
) -> SeriesAppendResult:
    """``until`` より前の分だけを**1 回だけ畳んで**、案内の全系列の M1 CSV へ追記する。

    行選択（設計 D-1・ISSUE-511 段階 8-D-2b の段 3）:
        畳みへ渡す行は**1 回だけ**選ぶ。下限は ``min(先端)``（先端の無い系列が 1 つでもあれば
        下限なし）であり、そこから ``until`` までの行が「閉じた分」である。各系列へは
        ``index > その系列の先端`` の分バーだけを追記する。先端に差があるときに落ちるのは
        「進んでいる側で既に確定済みの分」だけで、その分は**必ず遅れている側が使う**——
        つまり畳んだ分に「どの系列も使わない分」は無い。系列ごとに行を選び直して畳むと、
        同じティックを系列の数だけ畳むことになる（出力は正しいままなので状態検証では原理的に
        落ちない・ISSUE-450 と同型）。その不在は
        ``marketdata/tests/test_mt5_series_fan_out.py`` の C-1 / C-2 / D-1 が固定する。

    重複ガード（ISSUE-477・last_date の等号側）の規則は 1 系列のときと同じで、等号側を含めて
    畳みの**前**に落とす。違うのは「どの先端で落とすか」だけであり、組では下限（``min``）で
    落とし、残りは系列ごとに追記の直前で落とす。

    形成中の分（pending）は ``until`` 以降の行で、ref に依らないので**組に 1 本**返す。
    """
    rows = list(rows)
    refs = tuple(refs)
    if not rows:
        # 行 0 なら既存側の読みも案内の組み立ても発行しない（新着 0 の周期で書込・読取 0）。
        return SeriesAppendResult(bars={ref: 0 for ref in refs}, pending_rows=[])
    boundary = pd.Timestamp(until)
    if boundary.tzinfo is None:
        boundary = boundary.tz_localize("UTC")

    # 案内は 1 つだけ組む。価格基準と spread 宣言の照合・置き場の解決・既存 CSV の列形の照合は
    #   ここを通る（案内を作らずに書く経路を作らない・設計 D-4）。畳みの中の列形の照合も同じ
    #   案内を使うため、照合した対象と書く対象は構造的に一致する。
    plan = tick_m1.series_plan(refs, data_dir=data_dir)
    settled = {ref: _settled_minute(plan.paths[ref]) for ref in plan.refs}
    floor = None if any(v is None for v in settled.values()) else min(settled.values())

    closed: "List[Row]" = []
    pending: "List[Row]" = []
    for row in rows:
        minute = _utc_minute(row)
        if floor is not None and minute <= floor:
            continue  # どの系列でも確定済みの分（等号側を含む）＝重複。畳みへ入れない。
        (closed if minute < boundary else pending).append(row)

    if not closed:
        return SeriesAppendResult(bars={ref: 0 for ref in plan.refs}, pending_rows=pending)

    folded = tick_m1.fold_ticks_for_series(ingest.rows_to_frame(closed), plan=plan)
    bars: "dict[str, int]" = {}
    for ref in plan.refs:
        m1 = folded[ref]
        mark = settled[ref]
        if mark is not None:
            # M1 の index は naive UTC。先端の読み替えは _settled_minute の 1 箇所だけなので、
            #   突き合わせのためにここで naive へ戻す（第 2 の読み替え規則を作らない）。
            m1 = m1[m1.index > mark.tz_localize(None)]
        bars[ref] = tick_m1.append_m1_rows(m1, plan.paths[ref])
    return SeriesAppendResult(bars=bars, pending_rows=pending)


def append_m1_for_closed_minutes(
    rows: "Sequence[Row]", *, ref: str, data_dir: Any, until: Any
) -> AppendResult:
    """``until`` より前の分（＝閉じた分）だけを畳んで M1 CSV へ追記する。

    ``rows`` は**新着分のみ**（前周期からの持ち越しを含む）。畳みに渡すのは閉じた分の行だけで、
    当日の累積は 1 行も読み直さない（既存側は末尾 1 行だけを見る・下記ガード）。

    1 要素の組を :func:`append_m1_for_closed_minutes_for_series` へ通す**薄い包み**である
    （ISSUE-511 段階 8-D-2b の段 3）。名前・シグネチャ・戻り値は変えていない: 組が 1 つなら
    下限（``min(先端)``）はその系列の先端そのものなので、行選択も追記も従来と 1 ビットも
    変わらない（``marketdata/tests/test_mt5_series_fan_out.py`` の I-1 が byte で固定する）。

    重複ガード（ISSUE-477・**last_date の等号側**）:
        既存 M1 の最終 date と同じ分（またはそれ以前）の行は、既に確定済みであり畳まない・
        書かない。再起動時の pending 再種付け（UC-06）は「ジャーナルの最後の分」を機械的に
        種にするため、その分が停止前に確定済みでもティックがもう一度届く。ここで落とさないと
        同じ date の M1 行が 2 行になる（実測: volume 44+28 の 2 行割れ）。落とすのは畳みの
        **前**（行単位）である — 畳んでから捨てると、出力に使わない計算を毎起動発行する
        （ISSUE-450 と同型・計算量検定が固定する）。既存側の読みは
        :func:`marketdata.tick_m1.last_m1_date`（末尾 1 行・メモリ有界）だけで、
        規則は :func:`marketdata.tick_m1.append_m1_from_ticks` の ``index > last_date``
        と同じ「date 狭義単調増加」である。
    """
    appended = append_m1_for_closed_minutes_for_series(
        rows, refs=(ref,), data_dir=data_dir, until=until
    )
    return AppendResult(bars=appended.bars[ref], pending_rows=appended.pending_rows)


def update_rollups(*, ref: str, data_dir: Any, timeframes: "Optional[Sequence[str]]" = None):
    """M1 CSV の追記ぶんだけを上位足へ反映する（既存 rollup の増分更新へ委譲）。

    ``marketdata.rollup`` は本関数の内部でのみ import する（遅延 import）。domain 側の検定が
    rollup の重い依存を引き込まないようにするため。M1 CSV が無ければ何もしない
    （空のロールアップを置かない）。
    """
    from marketdata import rollup  # 遅延 import: 実行時だけ重い依存を触る。

    m1_path = tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)
    if not m1_path.is_file():
        return None

    out_dir = rollup_dir(ref=ref, data_dir=data_dir)
    state = rollup.RollupState.load(out_dir)
    new_state = rollup.incremental_update(
        m1_path, state, rollup.rollup_timeframes(), out_dir,
        ref_prefix=dataset_registry.series_of(ref),   # 保存物の名前（台帳・ISSUE-511 段階 1d）。
    )
    new_state.save(out_dir)
    return new_state


def update_rollups_for_series(*, refs: "Sequence[str]", data_dir: Any) -> "dict[str, Any]":
    """組の各系列の上位足を差分更新する（ISSUE-511 段階 8-D-2b の段 3）。

    ロールアップは系列ごとに別の置き場（``rollups/<series>/``）を持ち、入力も系列ごとの M1 CSV
    である。よってここに束ねられる計算は無く、規則の実体は :func:`update_rollups` 1 つのまま
    （本関数は組を回すだけ）。畳みと違って「1 回で済む共通部分」が無いことを、関数を分けずに
    ループで示す。
    """
    return {ref: update_rollups(ref=ref, data_dir=data_dir) for ref in refs}
