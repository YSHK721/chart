"""層名: 入力アダプタ（CSV → OHLC DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLC 列を備えた DataFrame に
    正規化する。ブローカー接続・チャートデータ取得（元 MQL4 ``OnCalculate`` 引数
    high/low/close[] の供給、iHigh/iLow/iClose による系列参照）は本層の責務外。
    具体描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ侵入させない
    （依存内向き・PORTING_GUIDE §2）。

    本指標の計算は high/low/close を使う（dist=|H-C|/|L-C|・起点 close[-2]）が、
    OHLC 規約として open も必須列に含める（profit_hlband 先例準拠）。本指標に計算
    input は無い（元 input は inpSymbol/inpTimeFrame のみで計算 period でない）。

元 MQL4 対応:
    ``CopyRates`` / ``MqlRates``（OHLCTV）相当のデータ供給を CSV から行う。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: なし / プロジェクト内: marketdata.ohlc_csv_loader
"""

from __future__ import annotations

# ISSUE-179 項目 1: CSV 読み込みの実体は最下層共有パッケージ marketdata.ohlc_csv_loader に
#   一本化した。本モジュールは「自パッケージの読み込み方針（必須列・列名 cast・空 CSV
#   ガード）」だけを宣言して共有機構へ委譲する薄い shim であり、挙動は一本化前と一致する。
#   ISSUE-087 🟡-3: 実行時の sys.path insert は撤去した。探索パスの解決は台帳
#   tools/dev_paths.txt を単一源とする既存機構が担う（pytest=indigators/conftest.py と
#   pyproject の pythonpath / 実行時=tools/dev_paths.sh / 対話=tools/install_dev_paths.py の .pth）。
from marketdata.ohlc_csv_loader import make_csv_loader  # noqa: E402


_REQUIRED = ("open", "high", "low", "close")

__all__ = ["load_ohlc_csv"]

# ISSUE-306: 委譲の実体は marketdata.ohlc_csv_loader が 1 つ持つ（17 スライスでの手書き複製を撤去）。
#   本モジュールは自パッケージの読み込み方針（必須列・列名 cast・空 CSV ガード）だけを宣言する。
#   生成される関数の公開シグネチャは複製時と同一（require= の上書きも可）。
load_ohlc_csv = make_csv_loader(_REQUIRED, require_non_empty=True)
