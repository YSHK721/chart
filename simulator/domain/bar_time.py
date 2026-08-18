"""`Bar.time` の時刻表現 → epoch 秒の正規化（domain 層・単一ソース）。

A-3（取得窓を全 `MarketDataPort` 実装へ効かせる）で新設。従来この正規化は
`simulator/main/tester_settings/window.py` にのみ存在したが、窓デコレータ
（`simulator/adapter/repository/windowed_market_data.py`）も同じ比較を要するため、
書き直せば手書き複製になる。正規化の対象は `Bar.time` の型契約
（`simulator/domain/bar.py`: ``numpy.datetime64`` または epoch int）そのものであり、
その所有者は domain 層である。よって実体を本モジュールへ置き、`window.py` と
窓デコレータの双方が**同一オブジェクト**を読む（複製 0）。

依存規律（`bar.py` と同じ）: 標準ライブラリ・domain 例外・`datawindow`（標準ライブラリ
のみで構成される中立共有パッケージ）に依存する。numpy / pandas は直接にも transitively
にも import しない（``numpy.datetime64`` は duck typing で判定する。``import
simulator.domain.bar_time`` 後に ``numpy`` が ``sys.modules`` へ載らないことを実測で
確認済み）。

実測に基づく確定事項（推測しない）:
    B-1: `bar.time` の実体は経路で異なる。comma 形式 CSV ローダ
         （`adapter/repository/ohlc_csv.py`）は CSV の値をそのまま採用し epoch 整数、
         MT5 タブ形式ローダ（`adapter/repository/ohlc_mt5_csv.py`）は
         ``np.datetime64`` を生成する（両実装の `_extract` 実読）。
    B-2: 窓境界は UTC aware datetime（`main/tester_settings/window.py`
         `resolve_data_window` が `_midnight_utc` で生成する）。
    B-3: naive datetime を `datetime.timestamp()` に掛けるとプロセスのローカル TZ で
         解釈される。naive を UTC とみなすことでこの環境依存という
         **原因そのものを除去**する（症状回避ではない）。
    B-4: その datetime → epoch 変換は**窓境界の正規化と同一の規則**である。実体は中立
         共有パッケージ `datawindow.half_open.epoch_seconds_of_datetime` が唯一所有し、
         本モジュールの `EPOCH_CONVERTERS` と Candle 段（`marketdata/csv_source.py`）が
         同じ関数オブジェクトを読む。分けて書いていた時期は解釈が食い違っていた（実測:
         `TZ=Asia/Tokyo`・naive `datetime(2025, 1, 10)` で 32400 秒差・ISSUE-401 🟡-2）。
         `marketdata` は `simulator` を import できない（依存方向）ため、共有点は両
         パッケージの外側へ置く。

拡張点（OCP）: 時刻表現の追加は ``EPOCH_CONVERTERS`` への 1 エントリ追加で済む。
判定関数（`Callable[[Any], bool]`）と変換関数（`Callable[[Any], int]`）の対を並べた
表であり、既存エントリ・利用側（`epoch_seconds`）は改変しない。
"""
from __future__ import annotations

import numbers
from datetime import datetime
from typing import Any, Callable

# B-4: datetime → epoch の実体は中立共有パッケージが唯一所有する（窓境界の正規化と同一
# 規則）。本モジュールは書き直さず、その**関数オブジェクトそのもの**を表へ載せる。
from datawindow.half_open import epoch_seconds_of_datetime
from simulator.domain.exceptions import ConfigError


def _is_integer(value: Any) -> bool:
    """整数（`numpy.int64` を含む）か。``bool`` は時刻ではないため除外する。"""
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _from_integer(value: Any) -> int:
    return int(value)


def _is_datetime(value: Any) -> bool:
    return isinstance(value, datetime)


def _is_numpy_datetime64(value: Any) -> bool:
    """``numpy.datetime64``（numpy を import せず duck typing で判定する）。"""
    return hasattr(value, "astype") and type(value).__name__ == "datetime64"


def _from_numpy_datetime64(value: Any) -> int:
    return int(value.astype("datetime64[s]").astype("int64"))


#: 時刻表現 → epoch 秒の変換器（判定順に評価する。表現の追加＝1 エントリ追加）。
EPOCH_CONVERTERS: "tuple[tuple[Callable[[Any], bool], Callable[[Any], int]], ...]" = (
    (_is_integer, _from_integer),
    # B-4: 窓境界と同じ関数オブジェクト（複製を持たない）。
    (_is_datetime, epoch_seconds_of_datetime),
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
