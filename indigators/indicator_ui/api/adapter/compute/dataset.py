"""dataset — datasetRef ホワイトリスト解決と OHLC/candles 供給（§7.3 / §6.3）。

datasetRef 識別子 → 実 CSV パスのホワイトリスト解決を単一定義し、生パス直送・パス
トラバーサルを防ぐ（外から組み立てたパスは解決しない・基本設計 §7.3）。

- ``DATASET_WHITELIST`` : 識別子 → 実 CSV パス（唯一の定義）。
- ``is_known(ref)``     : ホワイトリストに存在するか。
- ``load_dataframe(ref)``: 既存 loader で DataFrame 化（time 列を index に解決・キャッシュ）。
- ``load_candles(ref)`` : candles JSON（``[{time(UNIX秒),open,high,low,close}]``）へ変換。

時刻は **解像度非依存** に ``int(pd.Timestamp(v).timestamp())`` で UNIX 秒へ変換する
（pandas3 で ``astype // 10**9`` は誤り）。既存 loader / 指標 src は read-only。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from adapter.compute.module_loader import load_module

# workspace ルート（このファイル: api/adapter/compute/ → parents[5] = /workspaces/app）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]

# datasetRef ホワイトリスト（§7.3）。識別子 → 実 CSV パス。生パス直送・パストラバーサルを
# 防ぐため、ここに無いキーはすべて拒否する（外から組み立てたパスは解決しない）。
DATASET_WHITELIST: dict[str, Path] = {
    "sample": _WORKSPACE_ROOT
    / "lightweight-charts-python-main"
    / "examples"
    / "4_line_indicators"
    / "ohlcv.csv",
    # JP225（日経225・Dukascopy E_N225Jap）。marketdata から書き出した日足 CSV
    # （date,open,high,low,close・外れ値補正済み）。生成: indicator_ui/tools/export_jp225_csv.py。
    "jp225": _WORKSPACE_ROOT / "marketdata" / "data" / "jp225_daily.csv",
}

# サンプル CSV の時刻列（解像度非依存に UNIX 秒へ変換する起点）。
_SAMPLE_TIME_COLUMN = "date"

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")


def is_known(ref: Any) -> bool:
    """datasetRef がホワイトリストに存在するか（未知・生パスは False）。"""
    return ref in DATASET_WHITELIST


def _to_unix_seconds(value: Any) -> int:
    """時刻値を UNIX 秒（整数・解像度非依存）へ変換する（fake_chart と同一式）。"""
    return int(pd.Timestamp(value).timestamp())


@lru_cache(maxsize=None)
def load_dataframe(ref: str) -> pd.DataFrame:
    """ホワイトリスト解決済みキーの CSV を DataFrame 化する（キャッシュ）。

    既存 loader を再利用し、time 列（date）を index へ解決する（line 系指標の時刻解決）。
    """
    loader = _load_loader()
    return loader.load_ohlc_csv(
        str(DATASET_WHITELIST[ref]), time_column=_SAMPLE_TIME_COLUMN
    )


@lru_cache(maxsize=None)
def load_candles(ref: str) -> list[dict[str, Any]]:
    """ホワイトリスト解決済みキーを candles JSON へ変換する（§6.3・lightweight-charts 形）。

    Returns:
        ``[{time: UNIX秒, open, high, low, close}, ...]``（time 昇順）。time は index
        （load_ohlc_csv が date 列を index 化）から解像度非依存で UNIX 秒へ変換する。
    """
    df = load_dataframe(ref)
    lower_map = {str(c).lower(): c for c in df.columns}
    cols = {k: lower_map[k] for k in _OHLC_COLUMNS}
    candles: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        candles.append(
            {
                "time": _to_unix_seconds(idx),
                "open": float(row[cols["open"]]),
                "high": float(row[cols["high"]]),
                "low": float(row[cols["low"]]),
                "close": float(row[cols["close"]]),
            }
        )
    return candles


@lru_cache(maxsize=None)
def _load_loader():
    """指標 src の loader モジュールを一意名で読み込む（read-only・改変しない）。

    importlib 機構は ``module_loader.load_module`` に集約（重複解消）。
    """
    pkg_dir = _WORKSPACE_ROOT / "indigators" / "profit_band" / "src"
    return load_module("_dataset_loader_src", pkg_dir / "loader.py")
