"""tickvol_updown — 上昇／下落ティック数の n 区間累積の差を 1 本のバーで描く指標。

⚠ **本パッケージは UI 未結線のアーカイブ**（ISSUE-244 で `indicator_ui` の結線から外した）。
外した理由と復活手順は同梱の ``ARCHIVE.md`` を参照。計算コードと単体テストはそのまま残してある。

ティックボリューム（`tickvol`）は方向を持たない合計本数だけを描くため、スパイクが買い側か
売り側かを読めない。本指標は各足の **方向内訳**（up=上昇ティック数 / dn=下落ティック数・
供給側 `marketdata.csv_schema.UPDOWN_COLUMNS`）を直近 N 本ぶん合計し、その差をゼロ起点の
1 本のバーとして描く（依頼者指定 2026-08-02・ISSUE-241/242）。符号で色を分ける。

⚠ 実測上の注意（採用の可否は依頼者裁定）: 上昇と下落は互いにほぼ同じ大きさで動くため
（移動累積の相関 0.9993〜0.9999）、差だけが情報として残る。ただしその差はコイン投げ以下の
ばらつきしか持たない（分散比 0.83〜0.97）。実測の詳細は ISSUE-241 を参照。

公開 API:
    net_updown              : 直近 N 本の「上昇 − 下落」（1 本のバーの値・純関数）。
    cumulative_updown       : 直近 N 本の上昇/下落ティック数の合計（純関数）。
    resolve_updown_columns  : up/dn 列の実列名を大小不問で解決する。
    add_tickvol_updown      : 出力アダプタ（ヒストグラム 1 本を追加・符号で色分け）。
    NET_SERIES              : 出力系列名（front の SeriesDef.seriesName と一致）。
    DEFAULT_WINDOW_N        : 累積本数の既定（UI 初期値）。
"""

from __future__ import annotations

from .core import (
    DEFAULT_WINDOW_N,
    NET_SERIES,
    cumulative_updown,
    net_updown,
    resolve_updown_columns,
)
from .lwc_chart import add_tickvol_updown

__all__ = [
    "NET_SERIES",
    "DEFAULT_WINDOW_N",
    "net_updown",
    "cumulative_updown",
    "resolve_updown_columns",
    "add_tickvol_updown",
]
