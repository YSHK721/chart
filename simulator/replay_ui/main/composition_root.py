"""Composition Root — replay_ui バックエンドの DI 結線（CLEAN_ARCH §8・main 層）。

全層を import して port 実装（adapter）を UC 結線を保持する ``ReplayApp``（framework）へ注入する。
データパスは引数で受け（既定は repo 根 ``data/marketdata`` = proto 慣行）、cwd 非依存の絶対パスで解決する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.replay_ui.adapter import _indicator_ui_bridge
from simulator.replay_ui.adapter.dataset_ports import RefValidationPort
from simulator.replay_ui.adapter.causal_candle_repository import CausalCandleRepository
from simulator.replay_ui.adapter.causal_compute_gateway import CausalComputeGateway
from simulator.replay_ui.adapter.intrabar_window_repository import (
    IntrabarWindowRepository,
)
from simulator.replay_ui.adapter.market_profile_forming_gateway import (
    MarketProfileFormingGateway,
)
from simulator.replay_ui.adapter.market_profile_gateway import MarketProfileGateway
from simulator.replay_ui.adapter.tickvol_profile_gateway import TickvolProfileGateway
from simulator.replay_ui.framework.serve_replay import ReplayApp

# repo 根 = simulator/replay_ui/main/composition_root.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def build_replay_app(
    *,
    data_dir: Any = None,
    api_path: Any = None,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
) -> ReplayApp:
    """port 実装を結線した ``ReplayApp`` を返す。

    ``data_dir``: tick 由来データ根（既定 ``<repo>/data/marketdata``）。リプレイ固有フィードの
    ``ticks/``（tick parquet）を含む（m1/candle は dataset 単一権威へ委譲済み・ISSUE-131/132）。``web_dir``: 静的フロント配信ディレクトリ（任意・None で静的配信無効）。
    ``shared_js_root``: 単一ソース共有のフォールバック根（既定 ``<repo>/indigators/indicator_ui/web``）。
    ただし配信を許可するのは本根の **``js/``・``css/``・``vendor/`` サブツリーのみ**（serve_replay で
    許可根を限定＝最小権限。build.mjs/package.json/data/tests/node_modules 等は露出しない）。replay
    web_dir で miss したフロント資産（js/css/vendor）をここから配信し、web_dir/{js,css,vendor} 配下の
    symlink が本根の該当サブツリーを指しても境界一致ガードで許可される。index.html は web_dir 実体が
    常に優先（per-app）。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _REPO_ROOT
    data = Path(data_dir).resolve() if data_dir is not None else root / "data" / "marketdata"
    shared_js = (
        Path(shared_js_root).resolve()
        if shared_js_root is not None
        else root / "indigators" / "indicator_ui" / "web"
    )
    # ISSUE-131/132: candle・m1 の供給は dataset（単一権威）へ完全委譲済み＝CSV パスの結線は
    #   リプレイ固有フィードの tick parquet 根のみ。
    from marketdata.tick_m1 import tick_root as _tick_root  # ISSUE-262: レイアウト単一権威

    tick_root = _tick_root(data)

    candle_port = CausalCandleRepository(api_path=api_path, repo_root=root)
    compute_port = CausalComputeGateway(api_path=api_path, repo_root=root)
    window_port = IntrabarWindowRepository(
        tick_root=tick_root, api_path=api_path, repo_root=root
    )

    # ISSUE-136 ISP: ref ホワイトリスト検証（is_known）だけを要するため dataset のみのアクセサを使い、
    #   dataset 具象を検証専用の狭いポート型で受ける。
    bridge = _indicator_ui_bridge.load_dataset(api_path, root)
    ref_validation: RefValidationPort = bridge.dataset

    # MP サブバー tick 逐次成長: forming gateway（bridge 委譲）を Port として注入する。
    forming_port = MarketProfileFormingGateway(api_path=api_path, repo_root=root)

    # MP normal/sessions/replay（as-seen-at-t）: market_profile gateway（bridge 委譲）を Port として注入する。
    market_profile_port = MarketProfileGateway(api_path=api_path, repo_root=root)

    # 取引密度ハイライト（時刻帯の背景色）: tickvol_profile gateway（bridge 委譲）を Port として注入する。
    #   帯の定義はライブ側 controller が単一実装＝ライブとリプレイで byte 一致する。
    tickvol_profile_port = TickvolProfileGateway(api_path=api_path, repo_root=root)

    return ReplayApp(
        candle_port=candle_port,
        compute_port=compute_port,
        window_port=window_port,
        is_known_ref=ref_validation.is_known,
        web_dir=web_dir,
        shared_js_root=shared_js,
        forming_port=forming_port,
        market_profile_port=market_profile_port,
        tickvol_profile_port=tickvol_profile_port,
        # カレンダー（再生開始日）の選択可能日。足の供給と同一実体＝同一配信路で日を数える。
        days_port=candle_port,
    )
