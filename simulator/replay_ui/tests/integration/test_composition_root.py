"""Composition Root の結線スモーク（実データ非依存: 構築のみ・IO は起こさない）。

build_replay_app が全 port を注入した ReplayApp を返し、is_known_ref が dataset.is_known に
結線されていることを確認する（データファイルは読まない）。
"""
from __future__ import annotations

from simulator.replay_ui.framework.serve_replay import ReplayApp
from simulator.replay_ui.main.composition_root import build_replay_app


def test_build_replay_app_wires_ports(tmp_path):
    # data_dir は存在しなくてよい（構築時に IO しない）。
    app = build_replay_app(data_dir=tmp_path / "missing", web_dir=None)
    assert isinstance(app, ReplayApp)
    # is_known_ref が dataset.is_known へ結線（既知 ref True / 未知 False）。
    assert app._is_known_ref("jp225_tick") is True
    assert app._is_known_ref("definitely_unknown_ref") is False


def test_build_replay_app_web_dir_optional(tmp_path):
    app = build_replay_app(data_dir=tmp_path, web_dir=tmp_path)
    assert app.web_dir == tmp_path.resolve()


def test_build_replay_app_injects_market_profile_forming_port(tmp_path):
    # MP サブバー tick 逐次成長: forming_port（gateway）が注入され /market_profile_forming が有効。
    from simulator.replay_ui.adapter.market_profile_forming_gateway import (
        MarketProfileFormingGateway,
    )
    app = build_replay_app(data_dir=tmp_path / "missing", web_dir=None)
    assert app.forming_enabled is True
    assert isinstance(app._forming_port, MarketProfileFormingGateway)


def test_build_replay_app_injects_market_profile_port(tmp_path):
    # MP normal/sessions/replay: market_profile_port（gateway）が注入され /market_profile が有効。
    from simulator.replay_ui.adapter.market_profile_gateway import MarketProfileGateway
    app = build_replay_app(data_dir=tmp_path / "missing", web_dir=None)
    assert app.market_profile_enabled is True
    assert isinstance(app._market_profile_port, MarketProfileGateway)
