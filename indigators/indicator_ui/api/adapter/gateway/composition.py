"""composition — gateway 層の既定結線（Composition Root・ISSUE-137）。

usecase が所有する Output Boundary（:mod:`usecase.dataset_port`）の**既定具象**を、本モジュール
（外側・結線層）が単独で名指し合成する。ポートは未注入時に本モジュールの ``default_*`` を遅延
呼び出しして自己完結起動する（注入なしでも動く既存挙動の温存）。

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
