"""market_profile_zp_store — 移設互換シム（ISSUE-092 ④）。

実体は :mod:`market_profile_api.gateway.zp_store` へ移設した（永続化の物理 I/O を gateway 層へ
隔離＝レイヤ責務違反 ISSUE-091 #5 の是正）。本モジュールは旧 import パス
（``market_profile_api.compute.market_profile_zp_store``）を無変更で動かすための薄い再エクスポート
のみを担い、新規コードは gateway を直参照する。:class:`ZpStore` は移設先と同一クラス
（identity 一致）で、保存形式・挙動は完全不変。
"""
from __future__ import annotations

from market_profile_api.gateway.zp_store import *  # noqa: F401,F403
from market_profile_api.gateway.zp_store import ZpStore  # noqa: F401

__all__ = ["ZpStore"]
