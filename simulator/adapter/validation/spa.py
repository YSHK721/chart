"""SpaTestPort 実装：Hansen(2005) SPA_c consistent（詳細設計 §5.5・D3）。

実体は共有統計核 :mod:`common.stats_boot`（ISSUE-091 A1: mp_stats と simulator の双方が
依存する定常ブート・PW ブロック長・SPA を中立共有核へ抽出）。本モジュールは simulator 側の
従来公開名（HansenSpa / _pw_block_len 等）を温存する再エクスポートで、既存の消費者・テスト・
monkeypatch 経路を不変に保つ。numpy は共有核に局所化。
"""
from __future__ import annotations

from common.hansen_spa import HansenSpa  # noqa: F401
from common.stats_boot import (  # noqa: F401
    autocorr as _autocorr,
    bootstrap_std as _bootstrap_std,
    flat_top_weight as _flat_top_weight,
    pw_block_len as _pw_block_len,
    pw_block_len_one as _pw_block_len_one,
    stationary_bootstrap_indices as _stationary_bootstrap_indices,
)
