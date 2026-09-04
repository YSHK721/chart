"""E-2 FormingBar — 形成中バーの末尾差し込み（domain・依存ゼロ）。

ISSUE-250 Phase 1: 規則の実体は中立共有核 :mod:`common.forming_window` へ**移設**した
（移設であり複製ではない＝二重情報源を作らない）。ライブ（indicator_ui）も毎ティック更新で
同じ分割規則を要するため、両アプリが同一実体を通す必要があるからである（アプリ間の側方依存を
作らず中立核へ抽出する規律＝ISSUE-091 A1 と同型）。

本モジュールは既存の import 面（``from ...domain.forming_bar import apply``）を温存する
薄い再公開に徹する。規則（proto_server._apply_forming ＝本番 forming_bar.apply_forming_bar
に bit 一致）は共有核の docstring が唯一の記述源:

    - forming.time == 末尾 time  → その足を暫定 OHLC で置換
    - forming.time  > 末尾 time  → 新しい足として追加
    - forming.time  < 末尾 time  → 触らない（異常時の防御）
    - forming が None/非 Mapping、time が欠落/不正 → 無変更
    - 列名は大小無視で照合（open/high/low/close/volume）
    - forming に存在するキーのみ更新（他フィールドは保存）

バーは plain な Mapping の昇順列。pandas/numpy を import しない。
"""
from __future__ import annotations

from common.forming_window import apply_forming as apply  # noqa: F401  (既存 import 面の温存)

__all__ = ["apply"]
