"""moving_averages を名前付き共有ライブラリとして公開する再エクスポート層。

``indicators/`` を ``sys.path`` に追加すれば、他指標パッケージから
``from moving_averages import simple_ma`` のように `common` 同様の名前付き
import で再利用できる（実体は ``src/core.py``。本層は薄い再公開のみ）。

依存: なし（``src`` の公開 API を素通しするだけ。numpy 以外を引き込まない）。
"""

from __future__ import annotations

from .src import (  # noqa: F401
    MA_TYPES,
    exponential_ma,
    exponential_ma_on_buffer,
    linear_weighted_ma,
    linear_weighted_ma_on_buffer,
    linear_weighted_ma_on_buffer_fast,
    ma,
    simple_ma,
    simple_ma_on_buffer,
    smoothed_ma,
    smoothed_ma_on_buffer,
)
from .src import __all__  # 公開 API を src と一致させる
