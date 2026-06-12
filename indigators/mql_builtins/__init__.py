"""mql_builtins を名前付き共有ライブラリとして公開する再エクスポート層。

``indicators/`` を ``sys.path`` に追加すれば、他指標パッケージから
``from mql_builtins import compute_rsi`` のように `moving_averages` /
`profit_system` 同様の名前付き import で再利用できる（実体は ``src/core.py``。
本層は薄い再公開のみ）。

依存: numpy のみ（``src`` の公開 API を素通しするだけ。指標パッケージ・
profit_system を引き込まない＝循環依存を作らない）。
"""

from __future__ import annotations

from .src import (  # noqa: F401
    compute_mfi,
    compute_rsi,
    compute_stochastic,
    compute_wpr,
)
from .src import __all__  # 公開 API を src と一致させる
