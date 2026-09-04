"""composition — gateway 層の既定結線（Composition Root・ISSUE-137）。

compute が所有する Output Boundary（:mod:`tick_store_port` / :mod:`store_port`）の**既定具象**を、
本モジュール（外側・結線層）が単独で名指し合成する。

ISSUE-183（DIP 是正・遅延 import 循環の解消）: 従来はポート側が未注入時に本モジュールを
**pull**（関数スコープ import）していたため ``store_port → composition → market_profile_zp →
store_port`` の循環（Service Locator 化）が生じていた。:func:`install_default_stores` で本モジュール
から各ポートへ既定 factory を **push** する形へ反転し、compute → gateway の辺を除去して循環を断つ。
呼び出しはパッケージの Composition Root（:mod:`market_profile_api` の ``__init__``）が 1 回行う。

これにより「どの具象がポートを実装するか」という composition root の責務を内側（ポート本体）から
本モジュールへ集約する。ポート本体には具象クラス名（``MarketdataTickStore`` / ``ZpStore`` /
``DwellRollupStore``）が現れず、DIP（依存は抽象へ・具象結線は最外へ）を構造で担保する。

依存方向: 本モジュール（gateway）→ gateway 具象 ＋ compute（本質パラメータ provider の読取）。
外側 → 内側の一方向のみ（compute は本モジュールを一切 import しない）。既定 Store の設定 provider は
**call-time** に読むクロージャで、テストの monkeypatch 経路を温存する。ISSUE-183 item5: 永続化設定
（cache root / 形式版数＝偶有的性質）は compute の module private ではなく gateway 側
:mod:`market_profile_api.gateway.cache_settings` を単一情報源として読む。
"""
from __future__ import annotations

from typing import Any


def default_tick_store() -> Any:
    """既定の正準ティックストア（marketdata 結線）を合成する。"""
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

    return MarketdataTickStore()


def default_zp_store() -> Any:
    """既定の z(p) 永続キャッシュ Store を合成する（設定は compute から call-time に読む）。

    ``grid_w`` / ``hist_days`` / ``m_reps`` / 正準ティック列挙は
    :mod:`market_profile_api.compute.market_profile_zp` の module 変数（本質パラメータ）を provider で
    参照し、cache root / 版数（偶有的性質）は :mod:`market_profile_api.gateway.cache_settings` から読む
    （ISSUE-183 item5）。値は移設前と同一のため保存形式・パスは byte 不変。
    """
    from market_profile_api.gateway.zp_store import ZpStore
    from market_profile_api.gateway import cache_settings as _cfg
    from market_profile_api.compute import market_profile_zp as _zp
    from market_profile_api.compute.tick_store_port import data_root as _data_root

    return ZpStore(
        # ISSUE-183 item5: 永続化設定（偶有的性質）は gateway 側 cache_settings が単一情報源。
        root_provider=lambda: _cfg.ZP_CACHE_ROOT,
        default_root_provider=lambda: _data_root().data_dir() / "cache" / "market_profile_zp",
        grid_w=_zp.ZP_BP,  # ISSUE-079: znull 格子タグは bp 値（本質パラメータ＝compute 所有）。
        hist_days=_zp.NULL_HIST_DAYS,
        m_reps=_zp.M_REPS_DAY,
        cache_version_provider=lambda: _cfg.ZP_CACHE_VERSION,
        day_parquet_files=lambda *a, **k: _zp.day_parquet_files(*a, **k),
    )


def default_dwell_store() -> Any:
    """既定の dwell 永続キャッシュ Store を合成する（設定は compute から call-time に読む）。

    ``grid_w`` / 正準ティック列挙は :mod:`market_profile_api.compute.market_profile_dwell` の
    module 変数（本質パラメータ）を provider で参照し、cache root / 版数（偶有的性質）は
    :mod:`market_profile_api.gateway.cache_settings` から読む（ISSUE-183 item5）。
    値は移設前と同一のため保存形式・パスは byte 不変。
    """
    from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore
    from market_profile_api.gateway import cache_settings as _cfg
    from market_profile_api.compute import market_profile_dwell as _mpd
    from market_profile_api.compute.tick_store_port import data_root as _data_root

    return DwellRollupStore(
        # ISSUE-183 item5: 永続化設定（偶有的性質）は gateway 側 cache_settings が単一情報源。
        root_provider=lambda: _cfg.DWELL_CACHE_ROOT,
        default_root_provider=lambda: _data_root().data_dir() / "cache" / "market_profile_dwell",
        grid_w=_mpd.GRID_W,  # 内部格子（本質パラメータ＝compute 所有）。
        cache_version_provider=lambda: _cfg.DWELL_CACHE_VERSION,
        day_parquet_files=lambda *a, **k: _mpd.day_parquet_files(*a, **k),
    )


def install_default_stores() -> None:
    """既定 factory を compute のポートへ登録する（冪等・Composition Root が 1 回呼ぶ・ISSUE-183）。

    ここで渡すのは factory（関数オブジェクト）だけで、具象 gateway・compute 設定の import は
    ``default_*`` の呼出時まで遅延する（起動コスト・import 順序は従来どおり）。
    """
    from market_profile_api.compute.store_port import (
        set_default_dwell_store_factory,
        set_default_zp_store_factory,
    )
    from market_profile_api.compute.tick_store_port import set_default_tick_store_factory

    set_default_tick_store_factory(default_tick_store)
    set_default_zp_store_factory(default_zp_store)
    set_default_dwell_store_factory(default_dwell_store)
