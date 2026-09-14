"""外部（indicator_ui の計算 Facade・marketdata）へ出ていく面の実装群。

P-1 / P-2（既存 `/compute` と足の供給）と P-3（前進評価）はここに閉じる。pandas を知って
よいのは本パッケージだけである（依存方向の検定 R3 が機械強制する）。
"""
