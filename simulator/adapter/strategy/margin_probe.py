"""MarginProbe 戦略（StrategyPort 実装・証拠金維持率の動作確認専用 EA）。

目的:
    証拠金維持率（margin_level = equity / margin × 100）を**決定論的に**狙い値
    ``margin_level_target`` 以下へ導く建玉を 1 回だけ建て、維持率が stop_out_level
    （例: 100%）を割り込んだときのエンジンの挙動（`fail_stop`＝run 破棄 /
    `close_and_halt`＝全玉強制決済して完走）を、データの向きに依らず確認できるようにする。
    EA は建てるだけで、自分では何も照合しない（照合の所有者は
    `simulator/tests/integration/test_margin_probe_stop_out.py`）。

    参照挙動は MT5 原本 fixture（`simulator/tests/fixtures/mt5/ma_slope_jp225_202501`・
    tester.log「position stop out triggered at 99.95%」→ 強制決済 deal を生成し完走）であり、
    その再現はエンジン側（「`MarginGuard`」 / 「`StopOutPolicy`」）が持つ。本 EA は割れの
    **条件を作る**だけで、割れ判定の規則には触れない。

売買規則（決定論・判定は足の始まり）:
    最初の足の始値クォート（buy の建値 ask = open + spread × point）で、建てた瞬間の
    維持率が ``margin_level_target`` **以下**になる最小の刻み量で買いを 1 玉建てる。
    以降は何も発注しない（決済はエンジンの stop-out か期末清算に委ねる）。

    狙い値を 100 未満（既定 95）にすれば、建てた時点で維持率が 100% を割り込むため、
    価格がどちらへ動くデータでも割れの挙動を確認できる。100 より上にすれば
    「価格の逆行で徐々に割れる」経路も作れる（割れるかはデータに依る）。

発注量の導出（式は設計書と同じ・METRICS §5.1 / 必要証拠金）:
    margin_level = equity / margin × 100、margin = volume × contract_size × entry ÷ leverage
    → volume_raw = equity × leverage × 100 ÷ (contract_size × entry × target)
    刻みの規則は domain の `floor_to_step` が唯一の所有者であり、ここで書き直さない。
    切り捨てで量が減ると維持率が狙いを**上回る**ため、その場合は 1 刻みだけ上げて
    狙い以下へ収める。収められない（volume_max 上限・raw が正でない）場合は
    `ConfigError`＝起動失敗とする（黙って別の維持率で走らせない）。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.domain.volume_step import floor_to_step
from simulator.usecase.ports import EntryPriceBasisPort, StrategyPort

#: 切り捨て判定の相対許容（floor_to_step の量子化誤差を跨いで「ちょうど raw」を見分ける）。
_RAW_TOL = 1e-9


class MarginProbe(StrategyPort, EntryPriceBasisPort):
    """建てた瞬間の証拠金維持率を狙い値以下にする買いを 1 回だけ建てる EA。"""

    #: 判定の瞬間（`EntryPriceBasisPort`）。読むのは当該足の始値と気配幅だけなので、
    #: 判定は足の**始まり**で成立する（取れる価格は始値の bid/ask）。
    entry_price_basis = "current_open"

    def __init__(self) -> None:
        self._target: float | None = None
        self._leverage: float | None = None
        self._contract: float | None = None
        self._vmin: float | None = None
        self._vmax: float | None = None
        self._step: float | None = None
        self._point: float | None = None
        self._entered = False

    def on_init(self, config: Any, indicators: Any) -> None:
        target = float(config["margin_level_target"])
        if target <= 0.0:
            raise ConfigError(
                f"MarginProbe の margin_level_target={config['margin_level_target']} は正でありません",
                context={"margin_level_target": config["margin_level_target"]},
            )
        self._target = target
        self._leverage = float(config["leverage"])
        self._contract = float(config["contract_size"])
        self._vmin = float(config["volume_min"])
        self._vmax = float(config["volume_max"])
        self._step = float(config["volume_step"])
        self._point = float(config["point_size"])

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list[Order]:
        if self._target is None:
            raise ValueError("MarginProbe requires on_init() before on_new_bar().")
        if self._entered or self._has_position(account):
            return []
        open_ = float(indicators.get("open").iloc[bar_index])
        spread_points = float(indicators.get("spread").iloc[bar_index])
        entry_ask = open_ + spread_points * self._point
        equity = float(account.equity)
        lot = self._volume_for(equity=equity, entry=entry_ask)
        self._entered = True
        return [
            Order(side="buy", kind="market", volume=lot, price=None, sl=None, tp=None)
        ]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"

    def _volume_for(self, *, equity: float, entry: float) -> float:
        """維持率が狙い値以下になる最小の刻み量（式は本モジュール docstring）。"""
        raw = equity * self._leverage * 100.0 / (self._contract * entry * self._target)
        lot = floor_to_step(
            max(raw, self._vmin), step=self._step, minimum=self._vmin, maximum=self._vmax
        )
        if lot is None:
            raise ConfigError(
                "MarginProbe は発注量を銘柄の刻みへ丸められません",
                context={"raw": raw, "volume_min": self._vmin, "volume_step": self._step},
            )
        if lot + _RAW_TOL * max(raw, 1.0) < raw:
            # 切り捨てで維持率が狙いを上回る形。1 刻み上げて狙い以下へ収める。
            bumped = floor_to_step(
                lot + self._step, step=self._step, minimum=self._vmin, maximum=self._vmax
            )
            if bumped is None or bumped <= lot:
                raise ConfigError(
                    "MarginProbe は狙いの維持率へ届く発注量を作れません（volume_max 上限）",
                    context={
                        "raw": raw,
                        "volume_max": self._vmax,
                        "margin_level_target": self._target,
                    },
                )
            lot = bumped
        return lot

    @staticmethod
    def _has_position(account: Any) -> bool:
        return bool(getattr(account, "open_positions", []))
