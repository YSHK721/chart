"""§3.1 / §3.2 の役割判定表（水準 / 非水準・行ラベル・第 2 表のセル宣言）。

判定の規約（設計書 §3.1）:
    水準でない系列の除外は**名前ではなく実値の桁**で行う。現在値の 0.3〜3 倍に入るものを
    価格スケールの水準とみなす。名前で除外すると、指標が系列を増やしたとき（あるいは
    設定で系列名が変わったとき）に取り残しが起き、`btlm_trail_beta` / `btlm_trail_sigma` /
    `btlm_trail_band_hit_rate` の 21 本を水準として数えた §11 の誤りが再発する。

§8 OCP: 「積み上がる量か」（§5.3.3）も**性質の宣言**で切り替える。呼び出し側（usecase）に
指標名の分岐を作らない。宣言に無い指標は第 2 表のセルを持たない（キーが無い＝構造で表れる）。

水準パラメータ（上側分位・窓の本数・イベント件数）の既定は**指標カタログ**が唯一源であり、
本モジュールは既定値を発明しない（設定に無ければカタログの値を読む）。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from dashboard_ui.usecase.sheet_models import OscillatorSpec, SeriesRole, SheetInstance

#: 価格スケールとみなす倍率の下限・上限（§3.1・境界を含む）。
_LEVEL_LOW: float = 0.3
_LEVEL_HIGH: float = 3.0

#: 行ラベルに出さないパラメータ（見た目だけを決めるもの）。
_COSMETIC_PARAMS: "frozenset[str]" = frozenset({"color"})


def _rsi_headroom_excess(value: float, band_high: float) -> float:
    """RSI の超過分（`(v - u) / (100 - u)`）。上限は指標側 `levels.RSI_MAX` が唯一源。"""
    return (float(value) - float(band_high)) / (_rsi_max() - float(band_high))


def _plain_excess(value: float, band_high: float) -> float:
    """生スケールの超過分（`v - u`）。有界でない量（tick 数・乖離率）はこちら。"""
    return float(value) - float(band_high)


@dataclass(frozen=True)
class OscillatorDeclaration:
    """第 2 表のセルを作るために指標ごとに宣言する性質（§3.2 の表の機械可読版）。"""

    value_series: str
    level_prefix: str
    cumulative: bool = False
    excess: Callable[[float, float], float] = _plain_excess


#: 指標 → 宣言。ここに無い指標は第 2 表のセルを持たない（除外の列挙を書かない）。
_OSCILLATORS: "Mapping[str, OscillatorDeclaration]" = {
    "ma_marod": OscillatorDeclaration("ma_marod", "ma_marod"),
    "btlm_trail_marod": OscillatorDeclaration("btlm_trail_marod", "btlm_trail_marod"),
    "profit_rsi": OscillatorDeclaration("rsi", "rsi", excess=_rsi_headroom_excess),
    # tick 数は足の中で積み上がる量である（§5.3.3: 部分和は同じ経過の過去へ当てる）。
    "tickvol": OscillatorDeclaration("tickvol", "tickvol", cumulative=True),
}


def _indicator_module(indicator_id: str, submodule: str):
    """指標 src の公開モジュールを read-only で解決する（写しを持たない）。

    探索パスの用意は `simulator.replay_ui.adapter._indicator_ui_bridge` が唯一源
    （replay / sim と同形の read-only 再利用）。
    """
    from simulator.replay_ui.adapter import _indicator_ui_bridge  # 遅延: 技術隔離

    _indicator_ui_bridge.load_compute()
    from adapter.compute.call_binding import indicator_src  # 遅延: 技術隔離を本層に閉じる

    src = indicator_src(indicator_id)
    return importlib.import_module(f"{src.__name__}.{submodule}")


def _rsi_max() -> float:
    return float(_indicator_module("profit_rsi", "levels").RSI_MAX)


def _catalog_defaults() -> "Mapping[str, Mapping[str, object]]":
    """指標カタログの既定パラメータ（`GET /catalog` が配るものと同一の唯一源）。"""
    from simulator.replay_ui.adapter import _indicator_ui_bridge  # 遅延: 技術隔離

    _indicator_ui_bridge.load_compute()
    from adapter.compute.catalog_schema import catalog_defaults  # 遅延: 技術隔離

    return catalog_defaults()


class SeriesRoleTable:
    """役割宣言ポートの実装。判定表は本モジュールが所有する。"""

    def __init__(self, param_defaults: "Mapping[str, Mapping[str, object]] | None" = None) -> None:
        self._defaults = param_defaults

    # ------------------------------------------------------------------ 役割
    def role_of(
        self, *, instance: SheetInstance, series_name: str,
        values: "tuple[float, ...]", reference_price: float,
    ) -> SeriesRole:
        """その系列が価格スケールの水準か否か（実値の桁で決める）。"""
        finite = [
            float(value) for value in values if np.isfinite(np.float64(value))
        ]
        if not finite:
            return SeriesRole.NOT_LEVEL
        median = float(np.median(np.asarray(finite, dtype=np.float64)))
        price = float(reference_price)
        inside = _LEVEL_LOW * price <= median <= _LEVEL_HIGH * price
        return SeriesRole.PRICE_LEVEL if inside else SeriesRole.NOT_LEVEL

    # ---------------------------------------------------------------- ラベル
    def row_label(self, *, instance: SheetInstance, series_name: str) -> str:
        """ラダー行のラベル（§11-2: パラメータまで含めて一意にする）。

        既定と異なるパラメータだけを添える。同一足・同一指標で 2 本の instance が並ぶのは
        設定が違うときだけなので、違いを添えれば一意になり、かつ既定どおりの行は短く出る。
        """
        defaults = dict(self._param_defaults().get(instance.indicator_id) or {})
        marks = [
            f"{value}"
            for key, value in sorted(instance.params.items())
            if key not in _COSMETIC_PARAMS and value != defaults.get(key)
        ]
        return series_name if not marks else f"{series_name} {' '.join(marks)}"

    # ------------------------------------------------------------ セルの宣言
    def oscillator_spec(
        self, *, instance: SheetInstance, series_names: "frozenset[str]"
    ) -> "OscillatorSpec | None":
        """第 2 表のセル宣言（オシレータでなければ None）。"""
        declaration = _OSCILLATORS.get(instance.indicator_id)
        if declaration is None:
            return None
        settings = _Settings(
            indicator_id=instance.indicator_id,
            params=dict(instance.params),
            defaults=dict(self._param_defaults().get(instance.indicator_id) or {}),
        )
        q_high = float(settings.value("q_high"))
        return OscillatorSpec(
            value_series=declaration.value_series,
            band_high_series=f"{declaration.level_prefix}_q{_percent(q_high)}",
            q_high=q_high,
            window_n=int(settings.value("window_n")),
            k_events=int(settings.value("k_events")),
            cumulative=declaration.cumulative,
            excess=declaration.excess,
        )

    # ------------------------------------------------------------------ 内部
    def _param_defaults(self) -> "Mapping[str, Mapping[str, object]]":
        if self._defaults is None:
            self._defaults = _catalog_defaults()
        return self._defaults


@dataclass(frozen=True)
class _Settings:
    """設定 → カタログ既定 の順で水準パラメータを引く（既定を発明しない）。"""

    indicator_id: str
    params: "Mapping[str, object]"
    defaults: "Mapping[str, object]"

    def value(self, name: str) -> object:
        """`name` の値。設定にもカタログにも無ければ KeyError（無言の縮退を作らない）。"""
        if name in self.params:
            return self.params[name]
        if name in self.defaults:
            return self.defaults[name]
        raise KeyError(
            f"水準パラメータ {name!r} が設定にもカタログ既定にもありません"
            f"（indicatorId={self.indicator_id!r}）"
        )


def _percent(quantile: float) -> int:
    """分位を系列名の百分率へ（§7.1.1 の `_q{q_hi}` 展開と同一規約）。"""
    return int(round(float(quantile) * 100.0))
