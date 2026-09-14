"""StopOutPolicy: 証拠金割れで何をするかの決定点（ISSUE-479 Wave2 4-4・O-3）。

何を解くか:
    証拠金維持率が stop-out 水準を割ったとき、エンジンは 2 つのうち一方を行う——
    run を捨てて証拠金割れ例外を送出するか（`fail_stop`）、全保有玉を強制決済して
    完走するか（`close_and_halt`）。移設前この選択は

        if config.stop_out_action != "close_and_halt":

    という比較として、実行経路の**3 箇所**（バー open 評価・バー close 評価・ティック
    評価）に書き写されていた。この形には 2 つの欠陥がある:

    1. 方針を 1 つ増やすには 3 箇所すべてを直す必要がある（OCP 違反）。
    2. 3 箇所のうち 1 箇所を直し忘れると、**どの評価点で割れたかによって違う方針で
       走る**。この食い違いは「その評価点で割れる fixture」を通らない限り現れないため、
       検定が緑のまま残りうる。

    本モジュールは名前から方針への対応を 1 つの表に閉じる。

なぜ方針が例外を投げないか:
    run を捨てるかどうかは実行の制御であり、方針オブジェクトの責務ではない。方針は
    「強制決済するか否か」の決定だけを返し、送出は Interactor が行う。こうすると方針を
    差し替えても例外の文言・診断値（外側の終了コード翻訳が読む契約）が動かない。

既定への落ち方:
    表に無い名前（綴り違い・未知の設定）は `fail_stop` へ落ちる。移設前の
    `!= "close_and_halt"` という 1 比較と同値であり、設定を書き間違えた run が黙って
    完走してしまわないための側でもある。

usecase 層は domain のみ依存可。本モジュールは同層の ports のみ参照する（純判定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulator.usecase.ports import StopOutPolicyPort


@dataclass(frozen=True)
class StopOutContext:
    """割れが起きたという事実（方針が決定を下すために見てよいものすべて）。

    価格・クォート・保有玉そのものは載せない——決済の実行は Interactor が担うため、
    方針が知る必要があるのは「どれくらい割れたか」「どこで割れたか」だけである。
    """

    #: 割れを判定した時点の証拠金維持率（hedged 相殺を含む実効値）。
    margin_level: float
    #: 口座契約が定める stop-out 水準。
    stop_out_level: float
    #: 割れたバーの位置（送出時の診断に載る）。
    bar_index: int
    #: 割れた時点の保有玉数。
    open_trade_count: int


@dataclass(frozen=True)
class StopOutDecision:
    """割れに対して何をするかの決定。

    `liquidate=False` は「強制決済しない」＝呼出側が run を捨てる（送出する）。
    `liquidate=True` は「全保有玉を現値で強制決済する」＝呼出側は halt して完走する。
    """

    liquidate: bool


class FailStopPolicy(StopOutPolicyPort):
    """run を捨てる（部分結果を残さない）。エンジンの既定。"""

    def on_breach(self, ctx: StopOutContext) -> StopOutDecision:
        return _DISCARD_THE_RUN


class CloseAndHaltPolicy(StopOutPolicyPort):
    """全保有玉を強制決済し、以降の新規発注を止めて最終統計まで完走する。"""

    def on_breach(self, ctx: StopOutContext) -> StopOutDecision:
        return _LIQUIDATE_AND_HALT


#: 決定は 2 つしかないので値として 1 度だけ作る（呼ぶたびに組み直さない）。
_DISCARD_THE_RUN = StopOutDecision(liquidate=False)
_LIQUIDATE_AND_HALT = StopOutDecision(liquidate=True)

_FAIL_STOP = FailStopPolicy()
_CLOSE_AND_HALT = CloseAndHaltPolicy()

#: 名前 → 方針の唯一の対応表（`BacktestConfig.stop_out_action` が取る名前と対称）。
STOP_OUT_POLICIES: "dict[str, StopOutPolicyPort]" = {
    "fail_stop": _FAIL_STOP,
    "close_and_halt": _CLOSE_AND_HALT,
}

#: 表に無い名前の落ち先（移設前の `!= "close_and_halt"` 比較と同値）。
#: 表の中の実体そのものを指す（既定用に別の実体を作らない）。
_DEFAULT_STOP_OUT_POLICY: StopOutPolicyPort = _FAIL_STOP


def resolve_stop_out_policy(action: Any) -> StopOutPolicyPort:
    """方針名から方針を引く（run につき 1 回）。

    事前条件: `action` は任意（None・未知の名前も可）。
    事後条件: 表に在る名前はその方針、無い名前は `fail_stop` の方針を返す。返る実体は
    表の中のものであり、呼ぶたびに組み直さない。
    """
    return STOP_OUT_POLICIES.get(action, _DEFAULT_STOP_OUT_POLICY)
