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

依存宣言: pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.rollup` /
:mod:`marketdata.rollup_paths`（ロールアップ配置の唯一権威・ISSUE-502 D-16）/
:mod:`marketdata.mt5_ticks` 下位。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, NamedTuple, Optional, Sequence, Tuple

import pandas as pd

from marketdata import rollup_paths, tick_m1
from marketdata.mt5_ticks import ingest, server_clock

Row = Tuple[int, float, float]

#: rollup の出力先ディレクトリ名（``<data_dir>/rollups/<ref>/``）。綴りの唯一の所有者は
#: :mod:`marketdata.rollup_paths`（ISSUE-502 D-16）。本名は既存参照の互換のための別名である。
ROLLUP_DIRNAME = rollup_paths.ROLLUPS_DIRNAME


class AppendResult(NamedTuple):
    """追記結果。``pending_rows`` は次の周期へ持ち越す形成中の分の行。"""

    bars: int
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


def append_m1_for_closed_minutes(
    rows: "Sequence[Row]", *, ref: str, data_dir: Any, until: Any
) -> AppendResult:
    """``until`` より前の分（＝閉じた分）だけを畳んで M1 CSV へ追記する。

    ``rows`` は**新着分のみ**（前周期からの持ち越しを含む）。畳みに渡すのは閉じた分の行だけで、
    当日の累積は 1 行も読み直さない（既存側は末尾 1 行だけを見る・下記ガード）。

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
    rows = list(rows)
    if not rows:
        return AppendResult(bars=0, pending_rows=[])  # 行 0 なら既存側の読みも発行しない。
    boundary = pd.Timestamp(until)
    if boundary.tzinfo is None:
        boundary = boundary.tz_localize("UTC")

    settled = tick_m1.last_m1_date(tick_m1.m1_csv_path(ref=ref, data_dir=data_dir))
    if settled is not None and settled.tzinfo is None:
        settled = settled.tz_localize("UTC")  # M1 CSV の date は naive=UTC（既存契約）。

    closed: "List[Row]" = []
    pending: "List[Row]" = []
    for row in rows:
        minute = _utc_minute(row)
        if settled is not None and minute <= settled:
            continue  # 確定済みの分（等号側を含む）＝重複。畳みへ入れない。
        (closed if minute < boundary else pending).append(row)

    if not closed:
        return AppendResult(bars=0, pending_rows=pending)

    # 価格基準は :data:`marketdata.mt5_ticks.ingest.PRICE_BASIS` が唯一の宣言である
    #   （綴りを書き写さない・権威経路 rebuild と必ず同じ値を使う）。
    m1 = tick_m1.ticks_to_m1(
        ingest.rows_to_frame(closed), price_basis=ingest.PRICE_BASIS
    )
    bars = tick_m1.append_m1_rows(m1, tick_m1.m1_csv_path(ref=ref, data_dir=data_dir))
    return AppendResult(bars=bars, pending_rows=pending)


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
        m1_path, state, rollup.rollup_timeframes(), out_dir, ref_prefix=ref
    )
    new_state.save(out_dir)
    return new_state
