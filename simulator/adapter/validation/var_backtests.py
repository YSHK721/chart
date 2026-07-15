"""BacktestTestPort 実装：Kupiec POF / Christoffersen 独立性（詳細設計 §5.4・D3）。

実体は共有統計核 :mod:`common.stats_boot`（ISSUE-091 A1: mp_stats と simulator の双方が依存する
検定核を中立共有核へ抽出）。本モジュールは simulator 側の従来公開名（VarBacktests / chi2_sf_df1 /
norm_cdf）を温存する再エクスポートで、既存の消費者・テストを不変に保つ。
usecase へは float p 値のみ返す契約は不変。
"""
from __future__ import annotations

from common.stats_boot import (  # noqa: F401
    VarBacktests,
    _xlogx_term,
    chi2_sf_df1,
    norm_cdf,
)
