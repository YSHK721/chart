"""層名: 入力アダプタ（CSV → OHLCV DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLCV 列（open/high/low/
    close/**volume**）を備えた DataFrame に正規化する。ブローカー接続・チャート
    データ取得（元 MQL4 ``OnCalculate`` 引数 high/low/close/volume[] の供給）は
    本層の責務外。具体描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ
    侵入させない（依存内向き・PORTING_GUIDE §2）。

    RMMMACD は level_count 算出に iMFI（出来高加重）を含むため出来高（volume）を
    必須とする。先例 profit_mfi_macd の ``load_ohlcv_csv`` を踏襲し、open/high/low/
    close/volume を必須化する（open は RMMMACD 計算には不使用だが OHLCV 整合のため
    必須化）。

元 MQL4 対応:
    ``OnCalculate`` 引数 high/low/close/volume[]（既定の MT4 チャート出来高）相当の
    データ供給を CSV から行う。volume 列の値（tick / 実出来高）は CSV の列定義に従い
    そのまま採用する（bit-exact は CSV 列定義依存・SPEC §9）。

依存（PORTING_GUIDE §8）:
    標準: __future__, pathlib / 外部: pandas / プロジェクト内: なし
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# RMMMACD 必須列（volume を含む）。open は計算に不使用だが OHLCV 整合のため必須化。
_REQUIRED = ("open", "high", "low", "close", "volume")


def load_ohlcv_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    require: tuple[str, ...] = _REQUIRED,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み OHLCV 列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        time_column: 指定すると当該列を datetime 化して index に設定する（大小不問）。
        require: 必須列（既定は open/high/low/close/**volume**）。
        **read_csv_kwargs: pandas.read_csv へ渡す追加引数。

    Returns:
        必須列（および任意の追加列）を持つ DataFrame。行は時系列昇順を前提とする。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: 必須列（volume 含む）が欠けている場合、または指定の時刻列が
            存在しない場合。
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    df = pd.read_csv(csv_path, **read_csv_kwargs)
    lower_map = {str(c).lower(): c for c in df.columns}

    missing = [k for k in require if k not in lower_map]
    if missing:
        raise KeyError(
            f"CSV に必須列が不足しています: {missing}（存在する列: {list(df.columns)}）"
        )

    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        df[tcol] = pd.to_datetime(df[tcol])
        df = df.set_index(tcol)

    return df
