"""U-SizingPort: 発注量決定の境界（usecase・基本設計書 §6.1「新規・既存 Port は変更しない」）。

既存の `simulator.usecase.ports` の `StrategyPort` / `IndicatorPort` / `TickModelPort` は
**1 行も変更しない**（ISP: 既存クライアントに新しいメソッドを押し付けない）。本モジュールは
新しいクライアント（Decorator）専用の境界を独立に定義する。

依存方向（DIP）: Decorator（adapter）は本抽象にのみ依存し、`AccountMarginSizing`
（adapter/sizing）という具体には依存しない。
"""
from __future__ import annotations

import abc

from simulator.usecase.sizing_models import SizingContext, SizingDecision

# 推定建値に使う指標系列名を `entry_price_basis` から導く表。
# 実測（`simulator.usecase._execution.derive_quotes`）:
#     "close"        → bid=ask=bar.close   → 推定は registry の "close" 系列
#     "current_open" → bid=bar.open        → 推定は registry の "open" 系列
# エンジンの約定価格基準と推定の出所を同じ表で結び付けることで、
# 「どの系列で推定すべきか」の判断が 2 箇所に分かれないようにする。
_BASIS_TO_SERIES = {
    "close": "close",
    "current_open": "open",
}


def required_price_series(entry_price_basis: str) -> str:
    """``entry_price_basis`` に対応する指標系列名を返す。

    未知の基準は例外（無音で "close" へ倒すと誤った建値で量を決める）。
    """
    try:
        return _BASIS_TO_SERIES[entry_price_basis]
    except KeyError:
        raise ValueError(
            f"未知の entry_price_basis です: {entry_price_basis!r} "
            f"(既知: {sorted(_BASIS_TO_SERIES)})"
        ) from None


class SizingPort(abc.ABC):
    """口座状態と規則から発注量を決定する境界。

    実装は必要証拠金・ロスカット価格の式を**再実装してはならない**
    （`account_engine` の権威式のみを呼ぶ・§12.3-3 C-7）。
    """

    @abc.abstractmethod
    def decide_volume(self, context: SizingContext) -> SizingDecision:
        """1 発注ぶんの量を決める。決められない場合は volume=None を返す。"""
        raise NotImplementedError


class SizingRequiresStopLossError(Exception):
    """sizing ON なのに**リスク距離が確定できない**発注に遭遇した（依頼者裁定 2026-08-11）。

    裁定: この発注を黙って捨てると、バックテストは exit=0・取引 0 件で「正常終了」し、
    利用者からは「戦略が一度も発注しなかった」としか見えない（無音の誤動作）。
    そのため**ジョブごと明示失敗**させる（fail-stop）。

    `simulator.domain.exceptions.BacktestError` を**継承しない**のは意図的である。
    `simulator.adapter.controller.BacktestController.run` は BacktestError を捕捉して
    **終了コードだけ**に翻訳するため、継承すると理由の文言がそこで消える。
    運用者へ「なぜ落ちたか」を届けるには、この例外が翻訳層を素通りする必要がある
    （`run_job` が捕捉して失敗理由として記録する）。
    """


class SizingNotViableError(Exception):
    """この設定では 1 枚も建たない（破産確率制約 f <= 0）。ジョブ構築時に送出する。

    裁定（コードレビュー 🔴-4）: f<=0 を**発注時**に判定して落とすと、全発注が黙って
    消えて「exit=0・取引 0 件で正常終了」になる（無音の誤動作）。設定だけで決まる事柄
    なので、**実行前**（`AccountMarginSizing` の構築時）に確定させる。

    原因は 1 つではない（🔵-4）:
      * EV <= 0（エッジが無い）
      * EV > 0 でも α が厳しい／T が長く、最小格子点の RoR が α を超える
    したがってメッセージで原因を断定しない。

    `BacktestError` を継承しないのは `SizingRequiresStopLossError` と同じ理由
    （`adapter/controller.py:60-64` の終了コード翻訳で文言が消えるのを避ける）。
    """
