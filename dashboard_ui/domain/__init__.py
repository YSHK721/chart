"""domain 層（最内）。stdlib + numpy + `common` のみに依存する純粋ロジック。

DOM・HTTP・pandas・指標パッケージのいずれにも依存しない（`tests/unit/
test_dashboard_import_direction.py` が AST で機械強制する）。
"""
