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

__all__ = ["HEADER", "OHLCV_COLUMNS", "DATE_FMT"]
