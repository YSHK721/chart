"""csv_schema — ロールアップ互換 M1/上位足 CSV スキーマの単一定義（ISSUE-094 🟡-6）。

``jp225_m1.csv`` 系（:mod:`marketdata.tick_m1` が書き出す M1 原子）と上位足ロールアップ
（:mod:`marketdata.rollup` が書き出す ``<prefix>_<tf>.csv``）は **同一の loader 互換 CSV
スキーマ**（列順・date 書式）を共有する。従来は両モジュールが ``_HEADER`` / ``_DATE_FMT``
リテラルを各自に持ち「一致させること」コメントで人手同期していた（同一アクター＝CSV
スキーマ所有者の二重定義）。本モジュールがその唯一の規則源であり、両者は import 共有する。

依存方向: 本モジュールは **依存ゼロ**（純粋な定数）。:mod:`marketdata.tick_m1` /
:mod:`marketdata.rollup` が本モジュールを参照する（逆は無い・循環禁止）。
"""

from __future__ import annotations

# loader 互換 CSV の列（date + OHLCV）。:func:`marketdata.ohlc_csv_loader.load_ohlc_csv` が
# date を index、open/high/low/close/volume を値列として解決できる順序。
HEADER = ["date", "open", "high", "low", "close", "volume"]

# HEADER から date（先頭）を除いた値列（open/high/low/close/volume）。
OHLCV_COLUMNS = HEADER[1:]

# date 列の文字列書式（UTC 壁時計・秒精度）。
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# 方向内訳（tick 由来データだけが持つ **任意** 列）。up=上昇ティック数 / dn=下落ティック数。
#
# なぜ任意列か: 既存の CSV（jp225_m1 / jp225_daily / sample）は 1 分足 OHLC から作られており、
#   ティック単位の方向を復元できない。必須列にすると既存データが全滅するため、**持つデータだけが
#   持つ**追加列として末尾へ足す。読み側（marketdata.ohlc_csv_loader）は任意の追加列を素通しする
#   契約なので、無い CSV は従来どおり動く（列が増えても既存の列順・書式は 1 バイトも変わらない）。
UPDOWN_COLUMNS = ["up", "dn"]

# 合算集約する列（上位足へ resample するとき "last" でなく "sum" を使うもの）。
#   volume と同じ性質（期間内の件数）を持つ列をここで宣言する（規則の二重定義を避ける）。
SUM_COLUMNS = ["volume", "vol", *UPDOWN_COLUMNS]


def header_for(columns) -> list[str]:
    """値列の集合から CSV ヘッダ（date + 既知列の順）を返す。

    既知列（OHLCV → up/dn）の順序を固定し、未知列は末尾へ出現順で置く。up/dn を持たない
    データでは :data:`HEADER` と完全一致する（既存 CSV の書式不変）。
    """
    have = {str(c).lower() for c in columns}
    ordered = [c for c in (*OHLCV_COLUMNS, *UPDOWN_COLUMNS) if c in have]
    rest = [str(c) for c in columns if str(c).lower() not in set(ordered)]
    return [HEADER[0], *ordered, *rest]


__all__ = [
    "HEADER", "OHLCV_COLUMNS", "DATE_FMT",
    "UPDOWN_COLUMNS", "SUM_COLUMNS", "header_for",
]
