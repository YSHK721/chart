"""profit_system の公開契約が実態と一致することの検証（ISSUE-182 項目 1）。

事象: ``profit_adx_needle/src/core.py`` が ``profit_system.src.core`` の
アンダースコア名（``_normalize`` / ``_ps_average`` / ``_ps_std_ema`` /
``_unit_conversion``）を直接 import していた一方、``__all__`` は別の 5 件だけを
載せており、公開面が機能していなかった。

判断: 越境実績のある 4 関数を **public 名へ昇格**し ``__all__`` に載せる。
旧アンダースコア名は既存参照面（profit_system 自身のテスト・profit_adx_needle）を
壊さないため **同一オブジェクトの別名として残置**する（挙動は 1 ビットも変わらない）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# src（profit_system 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src as ps_src  # noqa: E402
from src import core as ps_core  # noqa: E402

# 昇格後の public 名 → 旧アンダースコア名。
_PROMOTED = {
    "ps_normalize": "_normalize",
    "ps_average": "_ps_average",
    "ps_std_ema": "_ps_std_ema",
    "ps_unit_conversion": "_unit_conversion",
}


# =========================================================== __all__ が実態と一致
def test_all_lists_promoted_public_names():
    for public in _PROMOTED:
        assert public in ps_src.__all__


def test_all_keeps_existing_public_names():
    for name in (
        "ps_level_count",
        "compute_sigma_levels",
        "SIGMA_LEVELS",
        "level_count_score",
        "compute_marod",
    ):
        assert name in ps_src.__all__


def test_all_entries_are_importable_from_src_package():
    for name in ps_src.__all__:
        assert hasattr(ps_src, name), name


def test_all_has_no_underscore_prefixed_entry():
    """公開面はアンダースコア名を含まない（public 昇格を採ったことの表明）。"""
    assert [n for n in ps_src.__all__ if n.startswith("_")] == []


# =========================================================== 別名は同一オブジェクト
def test_legacy_private_names_are_aliases_of_public_names():
    for public, legacy in _PROMOTED.items():
        assert getattr(ps_core, legacy) is getattr(ps_core, public), (public, legacy)


def test_legacy_private_names_remain_importable_from_core():
    """既存参照面（profit_adx_needle / 既存テスト）の後方互換を固定する。"""
    from src.core import (  # noqa: PLC0415
        _normalize,
        _ps_average,
        _ps_std_ema,
        _unit_conversion,
    )

    assert callable(_normalize)
    assert callable(_ps_average)
    assert callable(_ps_std_ema)
    assert callable(_unit_conversion)


# =========================================================== 値の不変（bit 等価）
def test_promoted_functions_return_identical_values():
    x = np.array([1.0, 2.0, 3.0, 8.0, 5.0])
    assert ps_core.ps_normalize(1.234567891) == ps_core._normalize(1.234567891)
    assert ps_core.ps_average(x) == ps_core._ps_average(x)
    assert ps_core.ps_std_ema(x) == ps_core._ps_std_ema(x)
    assert ps_core.ps_unit_conversion(5.0, 3.0, 4.0, 329.0, 1) == ps_core._unit_conversion(
        5.0, 3.0, 4.0, 329.0, 1
    )
