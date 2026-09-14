"""composition — gateway 層の既定結線（Composition Root・ISSUE-137）。

usecase が所有する Output Boundary（:mod:`usecase.dataset_port`）の**既定具象**を、本モジュール
（外側・結線層）が単独で名指し合成する。

ISSUE-183（DIP 是正）: 従来はポート側が未注入時に本モジュールを **pull**（関数スコープの
``from adapter.gateway.composition import ...``）していた＝内側 → 外側の逆流。本モジュールが
:func:`install_default_ports` でポートへ既定 factory を **push** する形へ反転し、ソースコード依存を
「外側（本モジュール）→ 内側（ポート）」の一方向に揃える。呼び出しはエントリポイント
（:mod:`framework.server` / ``api/tests/conftest.py``）が起動時に 1 回行う。

これにより「どの具象がポートを実装するか」という composition root の責務を内側（ポート本体）から
本モジュールへ集約する。ポート本体には具象クラス名（``MarketdataDatasetGateway``）が現れず、DIP
（依存は抽象へ・具象結線は最外へ）を構造で担保する。参照実装
:mod:`market_profile_api.gateway.composition` と同じ規律に従う。
"""
from __future__ import annotations

from typing import Any


def default_dataset_port() -> Any:
    """既定のデータセットゲートウェイ（marketdata.dataset 結線）を合成する。"""
    from adapter.gateway.marketdata_dataset import MarketdataDatasetGateway

    return MarketdataDatasetGateway()


def install_default_ports() -> None:
    """既定 factory を usecase のポートへ登録する（冪等・エントリポイントが 1 回呼ぶ）。

    ここで合成するのは factory（関数オブジェクト）だけで、具象 gateway の import は
    :func:`default_dataset_port` の呼出時まで遅延する（起動コスト・import 順序は従来どおり）。
    """
    from usecase.dataset_port import set_default_dataset_port_factory

    set_default_dataset_port_factory(default_dataset_port)
