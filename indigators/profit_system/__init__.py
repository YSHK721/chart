"""profit_system を名前付き共有ライブラリとして公開する再エクスポート層。

``indicators/`` を ``sys.path`` に追加すれば、他指標パッケージから
``from profit_system import ps_level_count`` のように `moving_averages` 同様の
名前付き import で再利用できる（実体は ``src/core.py`` と ``src/span_stats.py``。
本層は薄い再公開のみ）。

``span_stats``（σ スパン統計）は module としても再公開する。関数名だけでなく module
参照面が要るのは、計算量テストが module の ``np`` を差し替えて発行回数を数えるため
（``from profit_system import span_stats``）。

依存: numpy のみ（``src`` の公開 API を素通しするだけ。指標パッケージを引き込まない）。
"""

from __future__ import annotations

from .src import (  # noqa: F401
    SIGMA_LEVELS,
    compute_marod,
    compute_sigma_levels,
    level_count_score,
    oscillator_span,
    ps_average,
    ps_ema,
    ps_level_count,
    ps_normalize,
    ps_std_ema,
    ps_unit_conversion,
    rolling_span,
    series_avg,
    series_std,
)
from .src import span_stats  # noqa: F401  # 単一情報源そのもの（module 参照面）
from .src import __all__  # 公開 API を src と一致させる
