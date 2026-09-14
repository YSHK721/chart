"""tick_raw_schema — 生ティック（ベンダネイティブ）parquet の列スキーマの**単一権威**。

日別ティック parquet（``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet``）が持つ列は
``timestamp/bidPrice/askPrice/bidVolume/askVolume`` である。この列を**産出する**のは
marketdata.dukascopy_source の DukascopyTickSource（および同スキーマで追記する
tools/live_tick_watch）であり、木の形の権威は marketdata.tick_tree である。
したがって「その木に置かれるファイルが何の列を持つか」も、供給側である marketdata が持つ。

なぜ移したか（ISSUE-502 C-1）:
    実体は ``simulator/tools/ingest_ticks.py`` の ``RAW_COLUMNS`` にあり、tools の検証スクリプト
    3 本がそこを唯一源として import していた。しかし tools は simulator を駆動する側であり、
    simulator の sim_ui は import パス台帳のために tools を参照していたため、
    ``tools ⇄ simulator`` の循環が成立していた。列は「データ供給の規則」であって消費側
    （simulator の tick-store 取込）の所有物ではない。産出側 marketdata へ所有権を移すことで、
    tools → marketdata・simulator → marketdata という既存の向きだけが残る。
    産出側の docstring が「ingest.RAW_COLUMNS 契約へ適合する」と
    **下流を参照して**自らの出力契約を述べていたのも、この所有権の逆転が原因である。

依存方向: 本モジュールは **依存ゼロ**（純粋な定数）。marketdata.csv_schema
（ロールアップ互換 CSV 列の唯一源）と対称の位置づけであり、consumer 側（tools の検証
スクリプト・simulator の ingest）が本モジュールを参照する（逆は無い・循環禁止）。
"""

from __future__ import annotations

# Dukascopy raw frame の必須列（段1 fetch が保存するネイティブ列）。
#   timestamp : UTC（ms 精度）。
#   bidPrice / askPrice : 気配値。canonical の last=mid=(bid+ask)/2 はここから導出する。
#   bidVolume / askVolume : 気配側の出来高。canonical の volume は両者の和。
RAW_COLUMNS = ("timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume")

#: timestamp を除いた数値列（値の突合に使う側の語彙）。
RAW_VALUE_COLUMNS = RAW_COLUMNS[1:]

__all__ = ["RAW_COLUMNS", "RAW_VALUE_COLUMNS"]
