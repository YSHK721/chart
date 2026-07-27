"""層名: 入力アダプタ（CSV → OHLC DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLC 列（open/high/low/
    close）を備えた DataFrame に正規化する。ブローカー接続・チャートデータ取得
    （元 MQL4 ``OnCalculate`` 引数 open/high/low/close[] の供給）は本層の責務外。
    具体描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ侵入させない
    （依存内向き・PORTING_GUIDE §2）。

    RSI は出来高（volume）を **不要** とする（適用価格は OHLC のみで選択される）。
    そのため先例 profit_mfi の OHLCV ローダではなく、profit_stc 系の OHLC ローダ
    （``load_ohlc_csv``）を踏襲する（volume を外す）。

元 MQL4 対応:
    ``OnCalculate`` 引数 open/high/low/close[]（MT4 チャートの OHLC）相当のデータ
    供給を CSV から行う。volume / tick_volume は元 ``iRSI`` が参照しないため取り込まない。

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

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # src→profit_rsi→indigators→repo 根
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata.ohlc_csv_loader import read_ohlc_csv_with_policy  # noqa: E402

if TYPE_CHECKING:  # 型注釈専用（PEP 563 により実行時評価されない）
    import pandas as pd

# RSI 必須列（OHLC のみ・volume 不要）。
_REQUIRED = ("open", "high", "low", "close")

__all__ = ["load_ohlc_csv"]


def load_ohlc_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    require: tuple[str, ...] = _REQUIRED,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み OHLC 列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        time_column: 指定すると当該列を datetime 化して index に設定する（大小不問）。
        require: 必須列（既定は open/high/low/close。**volume 不要**）。
        **read_csv_kwargs: pandas.read_csv へ渡す追加引数。

    Returns:
        必須列（および任意の追加列）を持つ DataFrame。行は時系列昇順を前提とする。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: 必須列が欠けている場合、または指定の時刻列が存在しない場合。
    """
    return read_ohlc_csv_with_policy(
        path,
        read_csv_kwargs,
        time_column=time_column,
        require=require,
        cast_column_names=True,
    )
