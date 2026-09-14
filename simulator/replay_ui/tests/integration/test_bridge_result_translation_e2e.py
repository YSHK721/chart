"""実 bridge の応答が PortResult へ翻訳できることを実データで確かめる（ISSUE-502 段階 5B）。

## なぜ要るか（自己レビューで塞いだ穴）

段階 5B は 4 つの Port から HTTP ステータスを外し、gateway が bridge の ``(status, body)`` を
成否の分類つき結果へ翻訳する形にした。翻訳は「番号と分類が食い違ったら通さない」
（fail-closed）ため、**実 bridge が想定と違う組を返すと本番で例外になる**。

market_profile と market_profile_forming は実データ e2e
（``test_market_profile_gateway_e2e.py``）が実 bridge を通しているが、catalog と
tickvol_profile は fake しか通っていなかった。fake だけで「本番で発火しない」と言うのは
実証ではないので、実 bridge を通す経路をここに置く。

catalog は入力を持たない（計算も I/O も伴わない）ため実データ不要。tickvol_profile は
tick データを要するので、不在時は skip する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.replay_ui.adapter.catalog_gateway import CatalogGateway
from simulator.replay_ui.adapter.tickvol_profile_gateway import TickvolProfileGateway

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TICK_CSV = _REPO_ROOT / "data" / "marketdata" / "jp225_tick_m1.csv"


def test_the_real_catalog_bridge_translates_into_a_successful_port_result() -> None:
    """実 bridge（handle_catalog）の組がそのまま成功へ翻訳される（fail-closed が発火しない）。"""
    result = CatalogGateway().catalog()
    assert result.ok, result.error_type
    # ボディは bridge が組んだものを無改変で運ぶ（ライブと byte 一致の根拠）。
    assert result.payload["ok"] is True
    assert "catalog" in result.payload
    assert "paramScopes" in result.payload


def test_the_real_catalog_bridge_body_is_not_reshaped() -> None:
    """翻訳はボディに触らない: 2 回引いても同じ辞書が返る（整形が挟まっていない）。"""
    first = CatalogGateway().catalog()
    second = CatalogGateway().catalog()
    assert first.payload == second.payload


@pytest.mark.skipif(
    not _TICK_CSV.exists(), reason="実 tick データ（jp225_tick_m1.csv）が無いため skip"
)
def test_the_real_tickvol_bridge_translates_into_a_successful_port_result() -> None:
    """実 bridge（handle_tickvol_profile）の組がそのまま成功へ翻訳される。"""
    result = TickvolProfileGateway().profile("jp225_tick", sessions=20, pct=75, until=None)
    assert result.ok, result.error_type
    assert result.payload["ok"] is True
    assert "bands" in result.payload


def test_the_real_tickvol_bridge_unknown_ref_becomes_a_classified_failure() -> None:
    """未知 ref は分類つきの失敗になる（番号ではなく分類が Port の面に出る）。

    ref の whitelist 検証は計算に入る前に行われるため、実データが無くても通る。
    """
    result = TickvolProfileGateway().profile("no-such-dataset")
    assert not result.ok
    assert result.error_type == "validation", result.error_type
    assert result.payload["error"]["type"] == "validation"
