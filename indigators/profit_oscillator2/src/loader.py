"""層名: 入力アダプタ（CSV → OHLCV DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLCV 列（open/high/low/
    close/**volume**）を備えた DataFrame に正規化する。ブローカー接続・チャート
    データ取得（元 MQL4 ``OnCalculate`` 引数 high/low/close/volume[] の供給）は本層の
    責務外。具体描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ侵入させない
    （依存内向き）。

    PRO!fitOscillator は iMFI（compute_mfi）を内包し出来高（volume）を必須とするため、
    先例 profit_rmm / profit_mfi の ``load_ohlcv_csv`` を踏襲し volume を必須列とする。

元 MQL4 対応:
    ``PRO!fitOscillator.mq4`` の ``OnCalculate`` 引数 high/low/close/volume[]（既定の
    MT4 チャート出来高）相当のデータ供給を CSV から行う。volume の値（tick / 実出来高）
    は CSV の列定義に従いそのまま採用する（bit-exact は CSV 列定義依存・SPEC §9）。

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

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # src→profit_oscillator2→indigators→repo 根
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata.ohlc_csv_loader import make_csv_loader  # noqa: E402


# Oscillator2 必須列（volume を含む）。open は計算未使用だが OHLCV 整合のため必須化。
_REQUIRED = ("open", "high", "low", "close", "volume")

__all__ = ["load_ohlcv_csv"]

# ISSUE-306: 委譲の実体は marketdata.ohlc_csv_loader が 1 つ持つ（17 スライスでの手書き複製を撤去）。
#   本モジュールは自パッケージの読み込み方針（必須列・列名 cast・空 CSV ガード）だけを宣言する。
#   生成される関数の公開シグネチャは複製時と同一（require= の上書きも可）。
load_ohlcv_csv = make_csv_loader(_REQUIRED, cast_column_names=True)
