"""CSV から OHLC / OHLCV データを読み込む入力アダプタ（共有実体）。

データ取得そのもの（ブローカー接続等）は本ライブラリの責務外。呼び出し側が
用意した CSV を読み込み、コア計算が要求する OHLC 列を備えた DataFrame に正規化する。

本モジュールは 2 つの公開面を持つ（ISSUE-179 項目 1: `indigators/*/src/loader.py` 一本化）。

``load_ohlc_csv``
    従来からの公開 API。OHLC 4 列を必須とし、必須列判定は既定方針で行う。
    シグネチャ・挙動は移設時点から不変（``marketdata.dataset`` /
    ``marketdata.serving_cache`` / ``indigators/profit_band/src/loader.py`` が利用）。

``read_ohlc_csv_with_policy``
    各指標パッケージの ``src/loader.py`` が自パッケージの方針を渡して使う共有機構。
    パッケージ間で実測された 4 軸の差異（必須列 / 列名 cast / 空 CSV ガード /
    ``require=`` の公開有無）をパラメータ化した上位集合であり、既定値は
    ``load_ohlc_csv`` の挙動と完全一致する。

``read_csv_kwargs`` を「可変長キーワードではなく位置引数の Mapping」で受けるのは意図的
である。``**kwargs`` で受けると ``require`` 等の方針パラメータ名が pandas へ渡すべき
キーワードを横取りし、``load_ohlc_csv(path, require=...)`` が現在送出している
``TypeError: read_csv() got an unexpected keyword argument 'require'`` を消してしまう。
名前空間を分離することで、方針を公開するか否かを呼び出し側の ``def`` だけで決められる。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

_REQUIRED = ("open", "high", "low", "close")


def read_ohlc_csv_with_policy(
    path: str | Path,
    read_csv_kwargs: Mapping[str, Any],
    *,
    time_column: str | None = None,
    require: tuple[str, ...] = _REQUIRED,
    cast_column_names: bool = False,
    require_non_empty: bool = False,
) -> pd.DataFrame:
    """方針を受け取って CSV を読み込み、必須列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        read_csv_kwargs: pandas.read_csv へそのまま渡す追加引数（sep 等）の Mapping。
            方針パラメータとの名前衝突を避けるため位置引数で受ける。
        time_column: 指定すると当該列を datetime 化して index に設定する（大小不問）。
        require: 必須列（既定は open/high/low/close）。
        cast_column_names: True なら列名を ``str(c).lower()`` で正規化する。False なら
            ``c.lower()``（非文字列の列名は AttributeError を送出する）。
        require_non_empty: True なら読み込み結果が 0 行のとき ValueError を送出する。
            判定順は「必須列 → 空行 → 時刻列」で固定する。

    Returns:
        必須列（および任意の追加列）を持つ DataFrame。行は時系列昇順を前提とする。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: 必須列が欠けている場合、または指定の時刻列が存在しない場合。
        ValueError: require_non_empty=True かつ読み込んだ CSV が空（0 行）の場合。
        AttributeError: cast_column_names=False かつ列名が文字列でない場合。
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    df = pd.read_csv(csv_path, **read_csv_kwargs)

    if cast_column_names:
        lower_map = {str(c).lower(): c for c in df.columns}
    else:
        lower_map = {c.lower(): c for c in df.columns}

    missing = [k for k in require if k not in lower_map]
    if missing:
        raise KeyError(
            f"CSV に必須列が不足しています: {missing}（存在する列: {list(df.columns)}）"
        )

    if require_non_empty and len(df) == 0:
        raise ValueError("CSV が空です（0 行）。計算には 1 行以上が必要です。")

    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        df[tcol] = pd.to_datetime(df[tcol])
        df = df.set_index(tcol)

    return df


def load_ohlc_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み OHLC 列を備えた DataFrame を返す。

    ``require`` は公開しない。本関数へ ``require=`` を渡した場合は従来どおり
    pandas.read_csv へ転送され ``TypeError`` となる（挙動不変）。

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
    return read_ohlc_csv_with_policy(
        path, read_csv_kwargs, time_column=time_column, require=_REQUIRED
    )
