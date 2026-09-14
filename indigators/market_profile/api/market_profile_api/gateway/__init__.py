"""gateway — 外部データ具象への結線（ISSUE-091 🔴-2）。

compute が所有するポート（tick_store_port 等）の実装を置く。marketdata の物理格納
（day parquet・paths）への依存は本パッケージに隔離し、compute からは排除する。
"""
