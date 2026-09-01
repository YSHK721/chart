"""検証・UTC 変換・日分割・列整形（adapter E）。

MT5 の生の並びと marketdata の台帳の境目。台帳側の規約は :mod:`marketdata.tick_m1` が権威で
あり、列も日 partition のパスも本モジュールでは**定義しない**（import して委譲する）。

依存宣言（``test_mt5_module_dependency_declarations.py`` が AST で強制）:
    pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.mt5_ticks` 下位 /
    ``tools.capture_mt5_symbol_spec.sanitize_path_component``。

``tools`` から sanitize を import する理由（層としては逆向き）:
    銘柄・サーバ名 → パス成分の変換規則は ``tools/capture_mt5_symbol_spec.py`` が
    **既に持っている唯一の実装**である（ISSUE-445 の供給連鎖）。同じ規則を marketdata 側へ
    複製すると、片方だけ直った瞬間にトークンが割れて別ディレクトリへ書き始める。
    層の向きより「規則の第 2 実装を作らない」を優先する（設計 §4・検定 M-1）。

異常をすべて Fail-Stop にする理由:
    ここで通した値はそのまま台帳になる。部分的に書かれた台帳は、後から見て
    「取れていないのか壊れているのか」が区別できない。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import List, Optional, Sequence, Tuple

import pandas as pd

from marketdata import tick_m1
from marketdata.mt5_ticks import server_clock
from marketdata.mt5_ticks.port import Mt5SupplyError
from tools.capture_mt5_symbol_spec import sanitize_path_component

#: ティック 1 行 = ``(サーバ時刻ラベル ms, bid, ask)``。
Row = Tuple[int, float, float]

#: 銘柄とサーバを繋ぐ区切り（``JP225@OANDA-Japan-MT5-Live``）。
TOKEN_SEPARATOR = "@"


def token_for(symbol: str, server: str) -> str:
    """銘柄 × サーバの識別トークンを作る（tick 木の ``symbol`` 引数に渡す値）。

    Dukascopy 木（``JP225``）と衝突しない別トークンになるため、既存木へ 1 バイトも
    波及しない。VM 側はトークンを作らない（コンテナ側だけが知る）。
    """
    return (
        sanitize_path_component(symbol)
        + TOKEN_SEPARATOR
        + sanitize_path_component(server)
    )


def _check_types(row: "Sequence") -> Row:
    """1 行の型を確かめる（暗黙変換で異常を隠さない）。"""
    if len(row) != 3:
        raise Mt5SupplyError(f"ティック行の要素数が 3 ではありません: {row!r}")
    ms, bid, ask = row
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise Mt5SupplyError(f"time_msc が整数ではありません: {ms!r}")
    for name, value in (("bid", bid), ("ask", ask)):
        if not isinstance(value, float) or isinstance(value, bool):
            raise Mt5SupplyError(f"{name} が float ではありません: {value!r}")
    return (ms, bid, ask)


def validate_rows(
    rows: "Sequence[Row]", *, from_msc: int, to_msc: "Optional[int]" = None
) -> None:
    """応答行の前提を確かめる。破れていれば :class:`Mt5SupplyError`（書込 0）。

    確かめるのは 4 点である: 型・窓 ``[from_msc, to_msc]``・ms 単調非減少・気配の実在性
    （``ask >= bid`` かつ ``bid > 0`` かつ有限）。同一 ms は正常であり弾かない
    （MT5 では日常的に起きる）。
    """
    previous: "Optional[int]" = None
    for raw in rows:
        ms, bid, ask = _check_types(raw)
        if ms < int(from_msc):
            raise Mt5SupplyError(
                f"要求した窓の外の行が含まれます: time_msc={ms} < from_msc={from_msc}。"
            )
        if to_msc is not None and ms > int(to_msc):
            raise Mt5SupplyError(
                f"要求した窓の外の行が含まれます: time_msc={ms} > to_msc={to_msc}。"
            )
        if previous is not None and ms < previous:
            raise Mt5SupplyError(
                f"time_msc が単調ではありません: {previous} の次に {ms}。"
            )
        previous = ms
        if not (math.isfinite(bid) and math.isfinite(ask)):
            raise Mt5SupplyError(f"気配が有限値ではありません: bid={bid} ask={ask}（ms={ms}）")
        if bid <= 0:
            raise Mt5SupplyError(f"bid が 0 以下です: {bid}（ms={ms}）")
        if ask < bid:
            raise Mt5SupplyError(f"ask < bid です: bid={bid} ask={ask}（ms={ms}）")


def split_by_utc_day(rows: "Sequence[Row]") -> "List[Tuple[dt.date, List[Row]]]":
    """行を **UTC 日**ごとに昇順で分割する（日 partition の決め手）。

    分割の基準はラベルの日付ではなく :func:`server_clock.utc_day_of` の返す UTC 日である。
    ラベルで切ると日 partition がまるごとずれる。
    """
    out: "List[Tuple[dt.date, List[Row]]]" = []
    for row in rows:
        day = server_clock.utc_day_of(row[0])
        if not out or out[-1][0] != day:
            out.append((day, [row]))
        else:
            out[-1][1].append(row)
    return out


def rows_to_frame(rows: "Sequence[Row]") -> pd.DataFrame:
    """行を日別 parquet と同じ列・dtype の DataFrame へ整形する。

    列は :data:`marketdata.tick_m1._TICK_COLUMNS` を import する（第 2 定義を作らない）。
    ``timestamp`` は **UTC へ変換済み**（``datetime64[ms, UTC]``）、価格は ``float64``。
    MT5 に対応物のない ``bidVolume`` / ``askVolume`` は **0 で埋めない**
    （ISSUE-447 承認済み方針 3・捏造しない）。
    """
    columns = list(tick_m1._TICK_COLUMNS)
    utc_ms = [server_clock.to_utc_ms(r[0]) for r in rows]
    frame = pd.DataFrame({
        columns[0]: pd.to_datetime(pd.Series(utc_ms, dtype="int64"), unit="ms", utc=True),
        columns[1]: pd.Series([r[1] for r in rows], dtype="float64"),
        columns[2]: pd.Series([r[2] for r in rows], dtype="float64"),
    })
    frame[columns[0]] = frame[columns[0]].astype("datetime64[ms, UTC]")
    return frame
