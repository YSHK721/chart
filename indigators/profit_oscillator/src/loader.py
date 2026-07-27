"""層名: 入力アダプタ（CSV → OHLCV DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLCV 列（open/high/low/
    close/**volume**）を備えた DataFrame に正規化する。ブローカー接続・チャート
    データ取得（元 MQL4 ``OnCalculate`` 引数 open/high/low/close/volume[] の供給）は
    本層の責務外。具体描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ
    侵入させない（依存内向き・PORTING_GUIDE §2）。

    PRO!fit_Oscillator は 18 サブ系列の一つに iMFI（出来高を要する）を含むため
    volume を必須とする。先例 profit_mfi/src/loader.py の ``load_ohlcv_csv``
    （OHLCV 必須）を踏襲する。open も iRSI/iMARD の 7 適用価格・iRVI（close-open）に
    入るため必須列に含む。

元 MQL4 対応:
    ``CopyRates`` / ``MqlRates``（OHLCTV）相当のデータ供給を CSV から行う。volume の
    値（tick / 実出来高）は CSV の列定義に従いそのまま採用する（bit-exact は CSV 列
    定義依存・SPEC §9）。

依存（PORTING_GUIDE §8）:
    標準: __future__, pathlib, sys / 外部: なし / プロジェクト内: marketdata.ohlc_csv_loader
"""

from __future__ import annotations

# ISSUE-179 項目 1: CSV 読み込みの実体は最下層共有パッケージ marketdata.ohlc_csv_loader に
#   一本化した。本モジュールは「自パッケージの読み込み方針（必須列・列名 cast・空 CSV
#   ガード）」だけを宣言して共有機構へ委譲する薄い shim であり、挙動は一本化前と一致する。
#   profit_band/src/loader.py と対称に repo 根を sys.path へ挿入し、パッケージを standalone
#   実行/import する文脈（demo.py・lwc_demo.py・単体テスト）で cwd が repo 根でない場合の
#   ModuleNotFoundError: marketdata を防ぐ。
import sys as _sys
from pathlib import Path
from typing import TYPE_CHECKING

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # src→profit_oscillator→indigators→repo 根
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata.ohlc_csv_loader import read_ohlc_csv_with_policy  # noqa: E402

if TYPE_CHECKING:  # 型注釈専用（PEP 563 により実行時評価されない）
    import pandas as pd

# Oscillator 必須列（volume を含む）。
_REQUIRED = ("open", "high", "low", "close", "volume")

__all__ = ["load_ohlcv_csv"]


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
        KeyError: 必須列（volume 含む）が欠けている場合、または指定の時刻列が存在しない場合。
    """
    return read_ohlc_csv_with_policy(
        path,
        read_csv_kwargs,
        time_column=time_column,
        require=require,
        cast_column_names=True,
    )
