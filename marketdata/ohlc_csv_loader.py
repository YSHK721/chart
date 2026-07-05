"""CSV から OHLC データを読み込む入力アダプタ。

データ取得そのもの（ブローカー接続等）は本ライブラリの責務外。呼び出し側が
用意した CSV を読み込み、コア計算が要求する OHLC 列を備えた DataFrame に正規化する。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_REQUIRED = ("open", "high", "low", "close")


def load_ohlc_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み OHLC 列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        time_column: 指定すると当該列を datetime としてパースし index に設定する
            （列名は大文字小文字を区別しない）。None なら既定の連番 index。
        **read_csv_kwargs: pandas.read_csv へそのまま渡す追加引数（sep 等）。

    Returns:
        open/high/low/close 列（および任意の追加列）を持つ DataFrame。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: open/high/low/close のいずれかが欠けている場合。
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    df = pd.read_csv(csv_path, **read_csv_kwargs)

    lower_map = {c.lower(): c for c in df.columns}
    missing = [k for k in _REQUIRED if k not in lower_map]
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
