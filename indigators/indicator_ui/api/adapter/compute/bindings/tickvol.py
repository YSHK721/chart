"""tickvol の呼出規約フック（Latest 増分計算メタの宣言）。

本モジュールは call_binding から分離した協働子である（ISSUE-502 段階 4B・SRP/OCP）。

tickvol は本体（点ごとの写像）と外れ値水準（因果ローリング＋イベント蓄積）の複合である。
  水準はバー t までに**確定したイベント観測**すべてに依存し、必要な履歴長は上限を持たない
  （イベント頻度はデータ依存。実測 5m で 1 件 / 35.7 バー＝直近 50 件に 1,800 バー必要）。
  よって有限 tail は取れず、full 再計算では足内更新のたびに全窓を走り直すことになる。
  ISSUE-233 と同じ真因なので同じ解を採る＝「保持した状態を 1 点進める」増分計算を宣言する。
  増分器が扱えないパラメータでは prepare が None を返し従来の full 経路へ落ちる。
"""

from __future__ import annotations

from typing import Any

from adapter.compute.latest_meta_spec import LatestMeta


def latest_meta(params: dict[str, Any]) -> LatestMeta:
    """params → Latest 増分計算メタ（archetype/min_window/trailing_k/増分器名）。"""
    del params  # 全パラメータで同一宣言（適用可否の判定は増分器 prepare が持つ）。
    return LatestMeta("incremental", None, 1, "tickvol")
