"""adapter 層（外側の技術を内側の言葉へ翻訳する）。

ここから外（indicator_ui の計算 Facade・pandas・HTTP）を知ってよいのは本層だけである。
usecase / domain は Protocol（`dashboard_ui.usecase.sheet_ports`）越しにしか外を知らない。
依存方向は `dashboard_ui/tests/unit/test_dashboard_import_direction.py` が機械強制する。
"""
