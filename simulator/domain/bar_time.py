"""`Bar.time` の時刻表現 → epoch 秒の正規化（domain 層・単一ソース）。

A-3（取得窓を全 `MarketDataPort` 実装へ効かせる）で新設。従来この正規化は
`simulator/main/tester_settings/window.py` にのみ存在したが、窓デコレータ
（`simulator/adapter/repository/windowed_market_data.py`）も同じ比較を要するため、
書き直せば手書き複製になる。正規化の対象は `Bar.time` の型契約
（`simulator/domain/bar.py`: ``numpy.datetime64`` または epoch int）そのものであり、
その所有者は domain 層である。よって実体を本モジュールへ置き、`window.py` と
窓デコレータの双方が**同一オブジェクト**を読む（複製 0）。

依存規律（`bar.py` と同じ）: 標準ライブラリと domain 例外のみに依存する。numpy /
pandas を import しない（``numpy.datetime64`` は duck typing で判定する）。

実測に基づく確定事項（推測しない）:
    B-1: `bar.time` の実体は経路で異なる。comma 形式 CSV ローダ
         （`adapter/repository/ohlc_csv.py`）は CSV の値をそのまま採用し epoch 整数、
         MT5 タブ形式ローダ（`adapter/repository/ohlc_mt5_csv.py`）は
         ``np.datetime64`` を生成する（両実装の `_extract` 実読）。
    B-2: 窓境界は UTC aware datetime（`main/tester_settings/window.py`
         `resolve_data_window` が `_midnight_utc` で生成する）。
    B-3: naive datetime を `datetime.timestamp()` に掛けるとプロセスのローカル TZ で
         解釈される。本モジュールは naive を UTC とみなすことでこの環境依存という
         **原因そのものを除去**する（症状回避ではない）。

拡張点（OCP）: 時刻表現の追加は ``EPOCH_CONVERTERS`` への 1 エントリ追加で済む。
判定関数（`Callable[[Any], bool]`）と変換関数（`Callable[[Any], int]`）の対を並べた
表であり、既存エントリ・利用側（`epoch_seconds`）は改変しない。
"""
from __future__ import annotations

import numbers
from datetime import datetime, timezone
from typing import Any, Callable

from simulator.domain.exceptions import ConfigError


def _is_integer(value: Any) -> bool:
    """整数（`numpy.int64` を含む）か。``bool`` は時刻ではないため除外する。"""
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _from_integer(value: Any) -> int:
    return int(value)


def _is_datetime(value: Any) -> bool:
    return isinstance(value, datetime)


def _from_datetime(value: datetime) -> int:
    """aware は自身の TZ、naive は UTC とみなして epoch 秒へ（B-3 の除去）。"""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(aware.timestamp())


def _is_numpy_datetime64(value: Any) -> bool:
    """``numpy.datetime64``（numpy を import せず duck typing で判定する）。"""
    return hasattr(value, "astype") and type(value).__name__ == "datetime64"


def _from_numpy_datetime64(value: Any) -> int:
    return int(value.astype("datetime64[s]").astype("int64"))


#: 時刻表現 → epoch 秒の変換器（判定順に評価する。表現の追加＝1 エントリ追加）。
EPOCH_CONVERTERS: "tuple[tuple[Callable[[Any], bool], Callable[[Any], int]], ...]" = (
    (_is_integer, _from_integer),
    (_is_datetime, _from_datetime),
    (_is_numpy_datetime64, _from_numpy_datetime64),
)


def epoch_seconds(value: Any) -> int:
    """`bar.time` / 窓境界を epoch 秒（int）へ正規化する。

    事前条件: ``value`` は ``EPOCH_CONVERTERS`` が扱える時刻表現。
    事後条件: UTC 基準の epoch 秒を返す。
    例外: 未対応の表現は ``ConfigError``（推測で解釈しない）。
    """
    for matches, convert in EPOCH_CONVERTERS:
        if matches(value):
            return convert(value)
    raise ConfigError(
        f"epoch 秒へ正規化できない時刻表現です: {type(value).__name__}",
        context={"value_type": type(value).__name__, "value": str(value)},
    )
