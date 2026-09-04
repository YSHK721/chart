"""指標ごとの呼出規約フック（CALL_BINDING の協働子）。

``call_binding._TABLE`` の各エントリが宣言する preprocess / value_error_types 等の
**指標固有の知識** をここへ置く。call_binding 本体は表を引くだけで指標名を知らない
（SRP: 「1 指標の都合が変わったとき改変するファイル」を指標ごとに 1 本へ分ける）。
"""
