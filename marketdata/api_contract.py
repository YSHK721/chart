"""API 共有規約表（後方互換の再エクスポート・ISSUE-094 🔵-11）。

ISSUE-091 A2 まで本モジュールが ERROR_STATUS・nested_error の実体を保持していたが、HTTP 契約の
所有者は配信殻であり marketdata のどのアクターでもない（ISSUE-094 🔵-11）。実体は中立共有
パッケージ ``api_shared.http_contract`` へ移設し、本モジュールは後方互換の再エクスポートへ降格した
（既存 import ``from marketdata.api_contract import ...`` を壊さない・破壊的変更なし）。新規参照は
``api_shared.http_contract`` を直接使うこと。
"""
from __future__ import annotations

from api_shared.http_contract import ERROR_STATUS, nested_error

__all__ = ["ERROR_STATUS", "nested_error"]
