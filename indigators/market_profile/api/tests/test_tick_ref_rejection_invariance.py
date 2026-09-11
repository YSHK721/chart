"""ティック非対応 ref の 400 判定が **動かない** ことを固定する（ISSUE-512 段階 1）。

性格: 特性化検定（characterization test）。新しい振る舞いではなく、ISSUE-512 の配管工事
（ref→ティック木トークンの台帳化）が既存の受理境界を動かさないことの壁である。

とくに ``jp225_mt5`` は段階 3 まで **400 のまま** でなければならない。トークン解決を
既定値へフォールバックさせる実装にすると、``jp225_mt5`` が黙って Dukascopy の木を読み始め、
出力は形式上正しいため状態検証では検出できない。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from market_profile_api.controller.market_profile_controller import handle_market_profile
from market_profile_api.controller.market_profile_forming_controller import (
    handle_market_profile_forming,
)

#: ティック木を持たない台帳 ref（``jp225_mt5`` は段階 3 まで非 tick）。
_NON_TICK_REFS = ["sample", "jp225", "jp225_m1", "jp225_mt5"]


@pytest.mark.parametrize("ref", _NON_TICK_REFS)
@pytest.mark.parametrize("src", ["dwell", "zp"])
def test_market_profile_rejects_refs_without_a_tick_tree(ref, src):
    """``src=dwell`` / ``src=zp`` はティック木を持たない ref を 400 で拒否する。"""
    # Arrange / Act
    status, payload = handle_market_profile(ref, timeframe="1D", src=src)

    # Assert
    assert status == 400, f"{src}/{ref} が {status} で通った（400 でなければならない）"
    assert payload["error"]["type"] == "validation"


@pytest.mark.parametrize("ref", _NON_TICK_REFS)
def test_market_profile_forming_rejects_refs_without_a_tick_tree(ref):
    """形成中プロファイルの入口は、ティック木を持たない ref を 400 で拒否する。"""
    # Arrange / Act
    status, payload = handle_market_profile_forming(ref, timeframe="5m")

    # Assert
    assert status == 400, f"forming/{ref} が {status} で通った（400 でなければならない）"
    assert payload["error"]["type"] == "validation"
