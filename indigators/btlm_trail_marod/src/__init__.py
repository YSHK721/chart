"""btlm_trail_marod パッケージ公開 API。

新インジケーター btlm_trail_marod（MAROD＝移動平均乖離率）。btlm_trail core の OLS 窓末尾
トレンド（基準線）と 8 択ソース合成価格を参照実装として再利用し、基準線からの相対偏差（%）
を別 pane のオシレータとして供給する。確定バー不変（非リペイント・btlm_trail core 由来）。

正本計画: /root/.claude/plans/btlm-trail-marod-concurrent-aho.md
"""

from __future__ import annotations

from .core import DEFAULT_MAXBARS, DEFAULT_SOURCE, marod_series
from .lwc_chart import add_btlm_trail_marod

__all__ = [
    "DEFAULT_MAXBARS",
    "DEFAULT_SOURCE",
    "add_btlm_trail_marod",
    "marod_series",
]
