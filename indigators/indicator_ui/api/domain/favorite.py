"""Favorite 値オブジェクト（内部設計書 §3.1・基本設計 §5.2 FAVORITE）。

★ 登録は指標 id 単位（UC-06 toggle_favorite）。frozen dataclass の値等価により
集合での add/remove 重複判定を成立させる。

標準ライブラリのみ。`@dataclass(frozen=True)`（DTO は不変）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Favorite:
    """お気に入り登録 1 件（基本設計 §5.2：指標 id 単位）。

    frozen dataclass は indicator_id による値等価・hash を自動生成するため、
    set への add/remove で indicator_id が同一の Favorite を重複なく扱える
    （UC-06 toggle_favorite の重複判定基礎）。同一性は indicator_id のみで決まる。
    """

    indicator_id: str
