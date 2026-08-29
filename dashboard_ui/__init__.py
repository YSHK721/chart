"""水準到達シート（ISSUE-449 / 基本設計書 .doc/PRICE_LEVEL_REACH_SHEET_BASIC_DESIGN.md）。

依存方向: main → framework → adapter → usecase → domain → common(numpy)。逆流を作らない。
本パッケージは既存の計算結果を**読むだけ**であり、新規の計算を発行しない（§7・§8 OCP）。
"""
