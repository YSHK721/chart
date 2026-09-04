"""検証・UTC 変換・日分割・列整形（adapter E）。

MT5 の生の並びと marketdata の台帳の境目。台帳側の規約は :mod:`marketdata.tick_m1` が権威で
あり、列も日 partition のパスも本モジュールでは**定義しない**（import して委譲する）。

依存宣言（``test_mt5_module_dependency_declarations.py`` が AST で強制）:
    pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.path_tokens` /
    :mod:`marketdata.mt5_ticks` 下位。

sanitize を :mod:`marketdata.path_tokens` から取る理由（ISSUE-479 F-1）:
    銘柄・サーバ名 → パス成分の変換規則の実体は、かつて ``tools/capture_mt5_symbol_spec.py``
    にあり、本モジュールが tools を import していた（層の逆流・循環 C-1）。実害は例外型に
    出ていた: sanitize が送出する CaptureError は tools の型なので
    ``tools/mt5_tick_watch.py`` の捕捉集合をすり抜け、周期処理がトレースバックで exit 1 に
    なっていた。所有権を依存ゼロの最下層へ移し、tools 側が同一関数を再エクスポートする
    形にした（「規則の第 2 実装を作らない」は保ったまま層の向きが正になる・検定 M-1）。

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
from marketdata.path_tokens import PathTokenError, sanitize_path_component

#: ティック 1 行 = ``(サーバ時刻ラベル ms, bid, ask)``。
Row = Tuple[int, float, float]

#: 銘柄とサーバを繋ぐ区切り（``JP225@OANDA-Japan-MT5-Live``）。
TOKEN_SEPARATOR = "@"

#: MT5 系列の**価格基準**（``marketdata.tick_m1.ticks_to_m1`` の ``price_basis`` へ渡す値）。
#:
#: MT5 端末のチャートは **bid** を描いている（``chart_mode=0``・依頼者裁定 2026-09-02）。
#: ISSUE.md 段階 0 実測 T5 は、同日の Dukascopy M1（mid）と MT5 M1 を突き合わせて中央値
#: ``duka(mid) - mt5(bid) = +6.97`` を得ており、MT5 のスプレッド平均 11.41（T7）のちょうど
#: 半分に相当する。同じティックから mid で M1 を作れば、端末表示に対して半スプレッドぶん
#: 系統的にずれる。
#:
#: 宣言をここ 1 箇所に置く理由: 日中増分（:mod:`~marketdata.mt5_ticks.m1_chain`）と日次権威
#: 再構築（:mod:`~marketdata.mt5_ticks.rebuild`）は別の経路だが、**同じ系列**を作らねば
#: ならない。片方だけが mid のままなら、日次再構築が表示中の系列を静かに mid へ書き戻す。
#: 出力はどちらも「それらしい」ので、値を見ているだけでは気付けない。よって基準は綴りを
#: 書き写すのではなく、この 1 定数を参照で渡す
#: （``marketdata/tests/test_mt5_price_basis.py`` が AST で複製と未参照を禁じる）。
#:
#: 台帳（ティック parquet・ジャーナル）は bid と ask の**両方**を持ち続ける。基準は
#: 「保存するもの」ではなく「M1 の価格として何を採るか」の選択である。
PRICE_BASIS = tick_m1.PRICE_BASIS_BID


def token_for(symbol: str, server: str) -> str:
    """銘柄 × サーバの識別トークンを作る（tick 木の ``symbol`` 引数に渡す値）。

    Dukascopy 木（symbol="JP225"）と衝突しない別トークンになるため、既存木へ 1 バイトも
    波及しない。VM 側はトークンを作らない（コンテナ側だけが知る）。

    失敗型を翻訳する理由（本モジュールが担う唯一の追加分岐）:
        規則の所有者（:mod:`marketdata.path_tokens`）は「入力値が規則を満たさない」だけを
        ``PathTokenError``（ValueError 系）で表明する。供給の失敗分類は
        :mod:`marketdata.mt5_ticks.port` が持つので、ここで ``Mt5SupplyError`` へ写す。
        写さないと ``tools/mt5_tick_watch.py`` の捕捉集合（SupplyUnavailable /
        Mt5SupplyError / WireError）をすり抜け、周期処理がトレースバックで exit 1 になる。
        原因は ``from exc`` で残す（どの値が規則を破ったかを捨てない）。
    """
    try:
        return (
            sanitize_path_component(symbol)
            + TOKEN_SEPARATOR
            + sanitize_path_component(server)
        )
    except PathTokenError as exc:
        raise Mt5SupplyError(str(exc)) from exc


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
    時刻列は **UTC へ変換済み**（``datetime64[ms, UTC]``）、価格は ``float64``。
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
