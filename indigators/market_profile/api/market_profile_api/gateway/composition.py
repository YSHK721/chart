"""composition — gateway 層の既定結線（Composition Root・ISSUE-137）。

compute が所有する Output Boundary（:mod:`tick_store_port` / :mod:`store_port`）の**既定具象**を、
本モジュール（外側・結線層）が単独で名指し合成する。ポートは未注入時に本モジュールの ``default_*``
を遅延呼び出しして自己完結起動する（注入なしでも動く既存挙動の温存）。

これにより「どの具象がポートを実装するか」という composition root の責務を内側（ポート本体）から
本モジュールへ集約する。ポート本体には具象クラス名（``MarketdataTickStore`` / ``ZpStore`` /
``DwellRollupStore``）が現れず、DIP（依存は抽象へ・具象結線は最外へ）を構造で担保する。

依存方向: 本モジュール（gateway）→ gateway 具象 ＋ compute（設定 provider の読取）。外側 → 内側の
一方向のみ（compute は本モジュールを module-level import しない＝ポートの遅延 import が唯一の接点）。
既定 Store の設定 provider は compute の module 変数（``_ZP_CACHE_ROOT`` / ``NULL_HIST_DAYS`` /
``day_parquet_files`` 等）を **call-time** に読むクロージャで、テストの monkeypatch 経路を温存する。
"""
from __future__ import annotations

from typing import Any


def default_tick_store() -> Any:
    """既定の正準ティックストア（marketdata 結線）を合成する。"""
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

    return MarketdataTickStore()


def default_zp_store() -> Any:
    """既定の z(p) 永続キャッシュ Store を合成する（設定は compute から call-time に読む）。

    ``grid_w`` / ``hist_days`` / ``m_reps`` / 版数 / cache root / 正準ティック列挙は
    :mod:`market_profile_api.compute.market_profile_zp` の module 変数を provider で参照する
    （移設前の module-level ``ZpStore(...)`` と同一の結線＝byte 不変）。
    """
    from market_profile_api.gateway.zp_store import ZpStore
    from market_profile_api.compute import market_profile_zp as _zp
    from market_profile_api.compute.tick_store_port import data_root as _data_root

    return ZpStore(
        root_provider=lambda: _zp._ZP_CACHE_ROOT,
        default_root_provider=lambda: _data_root().data_dir() / "cache" / "market_profile_zp",
        grid_w=_zp.ZP_BP,  # ISSUE-079: znull 格子タグは bp 値。
        hist_days=_zp.NULL_HIST_DAYS,
        m_reps=_zp.M_REPS_DAY,
        cache_version_provider=lambda: _zp._ZP_CACHE_VERSION,
        day_parquet_files=lambda *a, **k: _zp.day_parquet_files(*a, **k),
    )


def default_dwell_store() -> Any:
    """既定の dwell 永続キャッシュ Store を合成する（設定は compute から call-time に読む）。

    ``grid_w`` / 版数 / cache root / 正準ティック列挙は
    :mod:`market_profile_api.compute.market_profile_dwell` の module 変数を provider で参照する
    （移設前の module-level ``DwellRollupStore(...)`` と同一の結線＝byte 不変）。
    """
    from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore
    from market_profile_api.compute import market_profile_dwell as _mpd
    from market_profile_api.compute.tick_store_port import data_root as _data_root

    return DwellRollupStore(
        root_provider=lambda: _mpd._CACHE_ROOT,
        default_root_provider=lambda: _data_root().data_dir() / "cache" / "market_profile_dwell",
        grid_w=_mpd.GRID_W,
        cache_version_provider=lambda: _mpd._CACHE_VERSION,
        day_parquet_files=lambda *a, **k: _mpd.day_parquet_files(*a, **k),
    )
