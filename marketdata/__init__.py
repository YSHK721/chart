"""marketdata — 市場データ供給の境界（ポート）と差し替え可能な取得 adapter。

「チャート/指標へ OHLC を取り込む」関心事を、指標（計算）・指標UI（配信）から分離して
ここへ集約する。利用側は :class:`CandleSource` ポート（``fetch_candles(start, end) ->
list[Candle]``）にのみ依存し、ベンダ固有（Dukascopy 等）は背後の adapter に隔離する。

- :data:`DATA_DIR`              : 時系列データの単一基点（`marketdata.paths`・ベンダ非依存）。
- :class:`Candle`               : 供給する OHLC の形（``{time(UNIX秒), open, high, low, close, volume}``）。
- :class:`CandleSource`         : 取得ポート（抽象・DIP の依存先）。
- :class:`DukascopyCandleSource`: Dukascopy 実装（``dukascopy-python`` を隔離）。
- :func:`repair_ohlc_outliers`  : 足内 OHLC 外れ値の純粋な補正（ベンダ非依存のクリーニング）。

依存方向: 利用側 → CandleSource（抽象）← DukascopyCandleSource（具象）。

ベンダ隔離（重要）: ``dukascopy_python`` に依存するのは ``DukascopyCandleSource`` /
``INTERVALS`` / ``JP225`` のみ。これらは PEP 562 の遅延 ``__getattr__`` で**アクセス時に初めて**
import する。よって ``from marketdata.paths import DATA_DIR`` 等のベンダ非依存な利用は
``dukascopy_python`` 不在環境でも成立する（DATA_DIR を多数の利用側が import するため必須）。
"""

from __future__ import annotations

from typing import Any

# ベンダ非依存なものは eager に公開（dukascopy_python を要求しない）。
from marketdata.cleaning import repair_ohlc_outliers
from marketdata.paths import DATA_DIR
from marketdata.port import Candle, CandleSource, TickSource

__all__ = [
    "DATA_DIR",
    "Candle",
    "CandleSource",
    "TickSource",
    "DukascopyCandleSource",
    "DukascopyTickSource",
    "INTERVALS",
    "JP225",
    "repair_ohlc_outliers",
]

# ベンダ依存（dukascopy_python）を要する名前は遅延 import（PEP 562）。
# `from marketdata import DukascopyCandleSource` 等のアクセス時にのみ dukascopy_source を読む。
_LAZY = {"DukascopyCandleSource", "DukascopyTickSource", "INTERVALS", "JP225"}


def __getattr__(name: str) -> Any:  # noqa: D401
    if name in _LAZY:
        from marketdata import dukascopy_source  # 遅延: ここで初めて dukascopy_python を要求
        return getattr(dukascopy_source, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
