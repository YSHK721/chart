"""入力アダプタ: CSV → OHLC DataFrame。

層名/責務:
    入力アダプタ。呼び出し側が用意した CSV を読み、計算層が要求する OHLC 列を備えた
    DataFrame に正規化する。ブローカー接続・チャートデータ取得（元 MQL4 の
    ``OnCalculate`` 引数 open/high/low/close[] の供給）は本層の責務外。

元 MQL4 の対応:
    ``CopyRates`` / ``MqlRates``（OHLCTV）相当のデータ供給を CSV から行う。

差分:
    profit_adx_needle/src/loader.py から踏襲。iARCTAN は ``open`` も計算に入る
    （7 適用価格に O が含まれる）ため、必須列は open/high/low/close の 4 列。

依存:
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

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # src→profit_arctan→indigators→repo 根
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata.ohlc_csv_loader import make_csv_loader  # noqa: E402


_REQUIRED = ("open", "high", "low", "close")

__all__ = ["load_ohlc_csv"]

# ISSUE-306: 委譲の実体は marketdata.ohlc_csv_loader が 1 つ持つ（17 スライスでの手書き複製を撤去）。
#   本モジュールは自パッケージの読み込み方針（必須列・列名 cast・空 CSV ガード）だけを宣言する。
#   生成される関数の公開シグネチャは複製時と同一（require= の上書きも可）。
load_ohlc_csv = make_csv_loader(_REQUIRED, cast_column_names=True)
