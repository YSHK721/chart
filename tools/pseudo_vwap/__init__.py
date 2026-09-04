"""tools.pseudo_vwap — 疑似VWAP 検証の層分割パッケージ（ISSUE-479 Wave2 M-4）。

層（依存は上から下へ一方向）:
    data       : 素材化 Gateway。**marketdata / market_profile_api を知る唯一の層**。
    indicators : 指標の純関数（numpy/pandas のみ。素材の出所を知らない）。
    stats      : 検定の純関数（numpy/pandas と因果分位バンドのみ）。
    measure    : 上の 3 層を組み合わせて測定表を作る。
    report     : 表の整形出力（値を作らない）。

CLI（引数解釈・sys.path 注入）は合成点 ``tools/verify_pseudo_vwap.py`` に閉じる。
この層構造は ``tools/tests/test_pseudo_vwap_layers.py`` が AST 走査で強制し、分割前後の
``--json`` 出力一致（golden）も同テストが固定する。
"""
