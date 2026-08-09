"""層名: 入力アダプタ（CSV → OHLC DataFrame）。

責務:
    呼び出し側が用意した CSV を読み、計算層が要求する OHLC 列（open/high/low/
    close）を備えた DataFrame に正規化する。ブローカー接続・チャートデータ取得
    （元 MQL4 ``OnCalculate`` 引数 high/low/close[] の供給）は本層の責務外。具体
    描画ライブラリ・pandas 以外の依存を内側（core/成果物層）へ侵入させない
    （依存内向き・PORTING_GUIDE §2）。

    RSIMACD は iRSI を価格 Typical（=(H+L+C)/3）に当てるため、**出来高（volume）は
    不要**。先例 profit_mfi_macd の ``load_ohlcv_csv`` から volume 必須を外し、
    open/high/low/close を必須化する（open は RSIMACD 計算には不使用だが OHLC 整合
    のため必須化）。

元 MQL4 対応:
    ``OnCalculate`` 引数 high/low/close[]（PRICE_TYPICAL 固定）相当のデータ供給を
    CSV から行う。出来高は参照しない（iMFI ではなく iRSI 起点）。

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


# RSIMACD 必須列（volume は不要）。open は計算に不使用だが OHLC 整合のため必須化。
_REQUIRED = ("open", "high", "low", "close")

__all__ = ["load_ohlc_csv"]

# ISSUE-306: 委譲の実体は marketdata.ohlc_csv_loader が 1 つ持つ（17 スライスでの手書き複製を撤去）。
#   本モジュールは自パッケージの読み込み方針（必須列・列名 cast・空 CSV ガード）だけを宣言する。
#   生成される関数の公開シグネチャは複製時と同一（require= の上書きも可）。
load_ohlc_csv = make_csv_loader(_REQUIRED, cast_column_names=True)
