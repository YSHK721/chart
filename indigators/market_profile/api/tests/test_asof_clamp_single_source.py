"""ISSUE-179（market_profile 項目 A）: as-of 経過分クランプ規則の単一情報源ガード。

**規則**: 未完了セッション（as-of ``now`` 時点で ``next_session_day_start(day) > now``）では、
観測・帰無ともセッション窓の「経過分」までしか評価しない。カラム上限（半開・排他）は

    elapsed = int((now - day_start) // 60) - SESSION_OPEN_MOD + 1
    col_hi  = max(1, min(G_MINUTES, elapsed))

**実測（ISSUE-179 着手時点・変更前ソース）**: 同一式が 4 箇所へ複製されていた。

  - ``compute/market_profile_zp.py:271-272``  ``_zp_day_rollup``（当日分岐）
  - ``compute/market_profile_zp.py:328-329``  ``_zp_partial_rollup``（境界日・窓上限と併用）
  - ``compute/tf_period_columns.py:93-94``    ``live_zp_day_roll``
  - ``compute/tf_period_columns.py:138-139``  ``day_columns_zp_compute``

（ISSUE.md 記載の ``controller/tf_period_columns.py`` は実パス ``compute/tf_period_columns.py``。
行番号は ISSUE-177/178/182/183 の適用でずれている。）

規則の所有層は **kernel**（:mod:`market_profile_zp_kernel`）とする。根拠: 本式が参照する
``SESSION_OPEN_MOD`` / ``G_MINUTES`` の単一定義が kernel にあり、式は I/O・キャッシュに一切
依存しない純関数（kernel の責務定義そのもの）だからである。``market_profile_zp`` は kernel の
全公開シンボルを再エクスポートする既存規約に従い、呼出面 ``zp.asof_col_hi`` を提供する。
"""
from __future__ import annotations

import re
from pathlib import Path

from market_profile_api.compute import market_profile_zp as zp
from market_profile_api.compute import market_profile_zp_kernel as zpk
from market_profile_api.compute import tf_period_columns as tfc

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"

#: 経過分（as-of）の素の式。単一情報源化後は kernel の定義 1 箇所のみに現れること。
_ELAPSED_EXPR = re.compile(r"//\s*60\)\s*-\s*(?:_zp\.)?SESSION_OPEN_MOD\s*\+\s*1")


# --------------------------------------------------------------------------- #
# 1. 規則の所在（kernel 所有・zp 再エクスポート）
# --------------------------------------------------------------------------- #
def test_clamp_rule_is_owned_by_the_kernel():
    """クランプ規則は kernel の純関数として 1 つだけ存在し、zp は同一オブジェクトを再エクスポートする。"""
    assert callable(zpk.asof_col_hi)
    assert zp.asof_col_hi is zpk.asof_col_hi


def test_clamp_rule_has_exactly_one_definition_in_the_package():
    """パッケージ全体で経過分の式が現れるのは kernel の定義 1 箇所のみ（複製の再発防止）。"""
    found = {
        str(p.relative_to(_PKG))
        for p in _PKG.rglob("*.py")
        if _ELAPSED_EXPR.search(p.read_text(encoding="utf-8"))
    }
    assert found == {"compute/market_profile_zp_kernel.py"}


# --------------------------------------------------------------------------- #
# 2. 規則の値（境界値・変更前の inline 式との同値）
# --------------------------------------------------------------------------- #
def _inline_before(now: float, day_start: int) -> int:
    """変更前の inline 実装（4 箇所の共通形）。本テストが固定する参照値。"""
    elapsed = int((now - int(day_start)) // 60) - zpk.SESSION_OPEN_MOD + 1
    return max(1, min(zpk.G_MINUTES, elapsed))


def test_clamp_boundaries():
    """下限 1・上限 G_MINUTES・開場前/開場丁度/終場後の境界。"""
    day = 1_700_000_000
    o = zpk.SESSION_OPEN_MOD
    g = zpk.G_MINUTES
    assert zpk.asof_col_hi(day, day) == 1                      # 開場前（elapsed<=0）→ 下限 1
    assert zpk.asof_col_hi(day - 3600, day) == 1               # 日始端より前 → 下限 1
    assert zpk.asof_col_hi(day + o * 60, day) == 1             # 開場丁度 → 1 分ぶん
    assert zpk.asof_col_hi(day + (o + 9) * 60, day) == 10      # 開場 9 分後 → 10
    assert zpk.asof_col_hi(day + (o + g - 2) * 60, day) == g - 1
    assert zpk.asof_col_hi(day + (o + g - 1) * 60, day) == g   # 終場丁度 → 上限
    assert zpk.asof_col_hi(day + (o + g + 500) * 60, day) == g  # 終場後 → 上限で飽和


def test_clamp_matches_the_inline_implementation_it_replaced():
    """変更前 inline 式と全域で一致する（秒粒度・端数秒・負の経過を含む）。"""
    day = 1_700_000_000
    for delta_min in range(-5, zpk.G_MINUTES + zpk.SESSION_OPEN_MOD + 5):
        for extra_sec in (0, 1, 30, 59):
            now = day + delta_min * 60 + extra_sec
            assert zpk.asof_col_hi(now, day) == _inline_before(now, day)
            assert zpk.asof_col_hi(float(now) + 0.5, day) == _inline_before(float(now) + 0.5, day)


# --------------------------------------------------------------------------- #
# 3. 部分窓（_zp_partial_rollup）での併用形の同値
# --------------------------------------------------------------------------- #
def test_partial_window_composition_is_equivalent():
    """``min(col_hi_t, max(1, elapsed))`` は ``min(col_hi_t, asof_col_hi(...))`` と一致する。

    ``_zp_partial_rollup`` の ``col_hi_t`` は直前に ``min(G_MINUTES, ...)`` で上限済み。
    ``max(1, min(G, e)) == min(G, max(1, e))``（G>=1）ゆえ、共有ヘルパへの置換は
    窓上限との合成後も値を変えない。全 col_hi_t × 全 elapsed で実証する。
    """
    day = 1_700_000_000
    g = zpk.G_MINUTES
    o = zpk.SESSION_OPEN_MOD
    for col_hi_t in (0, 1, 2, 37, g // 2, g - 1, g):
        for delta_min in range(-3, g + o + 3, 7):
            now = day + delta_min * 60
            elapsed = int((now - day) // 60) - o + 1
            assert min(col_hi_t, max(1, elapsed)) == min(col_hi_t, zpk.asof_col_hi(now, day))


# --------------------------------------------------------------------------- #
# 4. 呼出面（compute の 4 クライアントが共有ヘルパを参照する）
# --------------------------------------------------------------------------- #
def test_compute_clients_reference_the_shared_helper():
    """4 クライアントのソースが共有ヘルパ名を参照する（inline 再実装への回帰を検出）。"""
    zp_src = (_PKG / "compute" / "market_profile_zp.py").read_text(encoding="utf-8")
    tfc_src = (_PKG / "compute" / "tf_period_columns.py").read_text(encoding="utf-8")
    assert zp_src.count("asof_col_hi(") >= 2   # _zp_day_rollup / _zp_partial_rollup
    assert tfc_src.count("_zp.asof_col_hi(") == 2  # live_zp_day_roll / day_columns_zp_compute
    assert callable(tfc._zp.asof_col_hi)
