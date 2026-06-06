"""入力アダプタ: CSV → OHLC DataFrame。

層名/責務:
    入力アダプタ。呼び出し側が用意した CSV を読み、計算層が要求する価格列を備えた
    DataFrame に正規化する。データ取得そのもの（ブローカー接続 = 元 MQL の CopyRates）は
    本層の責務外。

元 MQL4 の対応:
    ``OnCalculate`` の ``Open[]`` 供給元。元は MT4 がレートを供給するが、移植では CSV から。

依存:
    標準: __future__, pathlib / 外部: pandas / プロジェクト内: なし
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# btlm は Open 価格を使うが、汎用性のため OHLC を要求し price 引数で選択可能にする。
_REQUIRED = ("open", "high", "low", "close")


def load_ohlc_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    require: tuple[str, ...] = _REQUIRED,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み価格列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        time_column: 指定すると当該列を datetime 化して index に設定する（大小不問）。
        require: 必須列（既定は open/high/low/close。最低限 open があればよい場合は
            ``require=("open",)`` を渡す）。
        **read_csv_kwargs: pandas.read_csv へ渡す追加引数。

    Returns:
        必須列（および任意の追加列）を持つ DataFrame。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: 必須列が欠けている場合。
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    df = pd.read_csv(csv_path, **read_csv_kwargs)
    lower_map = {c.lower(): c for c in df.columns}

    missing = [k for k in require if k not in lower_map]
    if missing:
        raise KeyError(f"CSV に必須列が不足しています: {missing}（存在する列: {list(df.columns)}）")

    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        df[tcol] = pd.to_datetime(df[tcol])
        df = df.set_index(tcol)

    return df
