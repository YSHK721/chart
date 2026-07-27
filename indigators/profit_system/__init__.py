"""profit_system を名前付き共有ライブラリとして公開する再エクスポート層。

``indicators/`` を ``sys.path`` に追加すれば、他指標パッケージから
``from profit_system import ps_level_count`` のように `moving_averages` 同様の
名前付き import で再利用できる（実体は ``src/core.py``。本層は薄い再公開のみ）。

依存: numpy のみ（``src`` の公開 API を素通しするだけ。指標パッケージを引き込まない）。
"""

from __future__ import annotations

from .src import (  # noqa: F401
    SIGMA_LEVELS,
    compute_marod,
    compute_sigma_levels,
    level_count_score,
    ps_average,
    ps_level_count,
    ps_normalize,
    ps_std_ema,
    ps_unit_conversion,
)
from .src import __all__  # 公開 API を src と一致させる
