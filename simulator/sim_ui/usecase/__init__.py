"""sim_ui usecase 層 — ジョブ実行系のアプリケーション規則（F-3）。

Port（境界）と Interactor（規則）を置く。FS・子プロセス・HTTP・pydantic といった
偶有的技術は一切持たない（それらは adapter / framework）。
"""
