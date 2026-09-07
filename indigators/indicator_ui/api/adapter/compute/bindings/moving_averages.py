"""moving_averages の呼出規約フック（Latest 増分計算メタの宣言）。

本モジュールは call_binding から分離した協働子である（ISSUE-502 段階 4B・SRP/OCP）。
call_binding は ``_TABLE`` の ``latest_meta`` 宣言で本モジュールの ``latest_meta`` を参照する
だけで、moving_averages が増分計算可能である理由（漸化契約）を知らない。

ISSUE-233: moving_averages は 4 種すべて「保持した状態を 1 点進める」増分計算
  （archetype="incremental"・状態器 "moving_averages"）で計算する。full 再計算を行わない
  ため所要は窓長に依らず一定になる。値は full と bit 一致する（sma/ema/smma は
  ``*_on_buffer`` の prev_calculated 契約、lwma は走行和を授受する
  linear_weighted_ma_on_buffer_stateful（走行和を授受する版）が full の漸化を
  そのまま継続するため）。

  min_window は None（full）のままにする。増分器が扱えないパラメータ（平滑化あり等）で
  落ちる従来経路は、tail による短縮を行わない厳密一致設計を維持する必要があるため
  （sma/lwma は core がスライド和の再帰であり、tail で開始点を変えると末尾値に浮動小数
  ドリフト ~1e-15 が乗る）。この理由で従来 sma/lwma を "window" と分類していた。
"""

from __future__ import annotations

from typing import Any

from adapter.compute.latest_meta_spec import LatestMeta


def latest_meta(params: dict[str, Any]) -> LatestMeta:
    """params → Latest 増分計算メタ（archetype/min_window/trailing_k/増分器名）。"""
    del params  # 4 種・全パラメータで同一宣言（適用可否の判定は増分器 prepare が持つ）。
    return LatestMeta("incremental", None, 1, "moving_averages")
