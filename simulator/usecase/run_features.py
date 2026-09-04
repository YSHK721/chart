"""RunFeatures: 1 run の config 由来スイッチを 1 度だけ読む（ISSUE-479 Wave2 4-3・O-2）。

何を解くか:
    バックテストの実行経路は config から 12 個のスイッチを読む。移設前はその読み取りが
    2 つのエンジン（bar 経路 / tick 経路）へ 12 箇所に散り、しかも**既定値が読み取り側の
    リテラルとして書かれていた**（`getattr(config, "floating_pnl_basis", "close")`）。

    この形には 2 つの欠陥がある:

    1. `models.py` の宣言を変えても読み取り側のリテラルは追随しない。両方を同時に
       直さない限り、宣言と実挙動が食い違う。
    2. 片方のエンジンのリテラルだけを直すと、2 つのエンジンが違う既定で走る。この
       食い違いは、両経路が同じ fixture を通らない限り数値に現れない（＝検定で
       落ちないまま残る）。

    本モジュールは読み取りを 1 点へ集め、既定値を `BacktestConfig` の**宣言から導出**
    する。宣言を変えれば、その瞬間に両エンジンへ同時に効く。

なぜ属性の直参照ではなく getattr 形か:
    実行経路には main/run_config.RunConfig のような duck-typed config も流れる
    （検定側にも属性委譲だけを持つ config 相当物が在る）。宣言に無い属性を持つ／
    持たない config を受け付ける
    ため、読み取りは getattr 形を保つ。宣言に既定を持たない必須項目（`tick_model` 等）は
    「未設定」＝ None として扱う（移設前の `getattr(config, "tick_model", None)` と同じ）。

usecase 層は domain のみ依存可。本モジュールは同層の models のみ参照する。
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from typing import Any

from simulator.usecase.models import BacktestConfig


#: run 中に効く config 由来のスイッチ（読み取り点はここが唯一）。
#: 並びは `BacktestConfig` の宣言順に合わせてある（宣言と読み取りの対応を目で追えるように）。
_FEATURE_NAMES: "tuple[str, ...]" = (
    "tick_model",
    "sltp_tie",
    "entry_price_basis",
    "stop_out_action",
    "prime_first_trading_bar",
    "floating_pnl_basis",
    "profit_round_digits",
    "stop_out_at_open",
    "pending_lifecycle",
    "pending_oco",
    "pending_persistent",
    "hedged_margin",
)


def _declared_defaults() -> "dict[str, Any]":
    """`BacktestConfig` の宣言から各スイッチの既定値を引く（既定の単一ソース）。

    宣言が既定を持たない必須項目は「未設定」＝ None とする。表は import 時に 1 度だけ
    組み、run ごとには組み直さない（宣言は run の間に変わらないため）。
    """
    declared = {f.name: f.default for f in fields(BacktestConfig)}
    return {
        name: (None if declared.get(name, MISSING) is MISSING else declared[name])
        for name in _FEATURE_NAMES
    }


#: 既定値表（import 時に 1 度だけ導出する）。
_DECLARED_DEFAULTS: "dict[str, Any]" = _declared_defaults()


@dataclass(frozen=True)
class RunFeatures:
    """1 run のあいだ変わらない config 由来のスイッチ束（値オブジェクト）。

    run の途中で書き換わらないことを型で表明する（frozen）。実行経路はこの値だけを見て
    分岐し、config を直接読まない。
    """

    tick_model: Any
    sltp_tie: Any
    entry_price_basis: Any
    stop_out_action: Any
    prime_first_trading_bar: Any
    floating_pnl_basis: Any
    profit_round_digits: Any
    stop_out_at_open: Any
    pending_lifecycle: Any
    pending_oco: Any
    pending_persistent: Any
    hedged_margin: Any

    @classmethod
    def feature_names(cls) -> "tuple[str, ...]":
        """読み取り対象のスイッチ名（宣言順）。"""
        return _FEATURE_NAMES

    @classmethod
    def declared_defaults(cls) -> "dict[str, Any]":
        """`BacktestConfig` の宣言から導出した既定値表（読み取り専用の同一実体）。"""
        return _DECLARED_DEFAULTS

    @classmethod
    def of(cls, config: Any) -> "RunFeatures":
        """config を **1 スイッチにつき 1 回だけ**読んでスイッチ束を作る。

        事前条件: `config` は属性アクセス可能な任意の対象（None も可）。
        事後条件: 各スイッチは config の値、無ければ `BacktestConfig` の宣言既定を持つ。
        """
        return cls(
            **{
                name: getattr(config, name, _DECLARED_DEFAULTS[name])
                for name in _FEATURE_NAMES
            }
        )
