"""tick_model 単一レジストリ（ISSUE-097 🟡-5・OCP・CLEAN_ARCH §6.3）。

従来 tick_model の許容値・構築知識は 3 箇所に分散していた:

  1. ``framework/config_loader.py`` の ``Literal[...]``（許容 4 値）
  2. ``main/__init__.py`` の ``_TICK_MODELS`` dict（synthetic 3 モデルの id→クラス）
  3. ``main/__init__.py`` の ``real_ticks`` 別分岐（実ティック I/O 経路）

本モジュールはこの三分散を 1 つの登録表 ``TICK_MODEL_REGISTRY`` へ集約する。
config_loader の Literal 許容値は ``TICK_MODEL_IDS`` から導出し、main の synthetic
生成／real_ticks 分岐は各 spec から導出する。新 tick_model 追加は本表への 1 エントリ
追加のみで済む（config_loader/main のクロスファイル同期編集を撤廃）。

**既存 4 モデルの挙動・分岐先は完全不変**:
    every_tick  → EveryTickModel()            （synthetic・order 無視）
    ohlc_expand → OhlcExpandTickModel(order=…) （synthetic・order を反映）
    open_only   → OpenOnlyTickModel()          （synthetic・order 無視）
    real_ticks  → 実ティック I/O 経路           （synthetic_builder=None・別分岐）

A-1（ISSUE-397）で 5 件目 ``math_calculations`` を**末尾へ**追加した。MT5 の
`Math calculations`（`Model=3`）は価格系列を供給せずティックを生成しない
（基本設計 §4.5.2）。この「バー系列を消費しない」という性質を
``requires_market_data`` として宣言し、Composition Root（`main`）はこの宣言だけを見て
データ供給の有無を決める（`if math` を持たない＝OCP）。既定 ``True`` により既存 4
エントリは 1 文字も変わらない。

adapter 層に置く根拠: TickModelPort 実装（tick_model.py）と同一パッケージであり、
tick_model の id と実装の対応は adapter の関心事である。config_loader（framework）・
main（Composition Root）はいずれも本 adapter へ内向き依存でき（Dependency Rule 遵守）、
双方が本表を単一情報源として参照する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from simulator.adapter.execution.null_tick_model import NullTickModel
from simulator.adapter.execution.tick_model import (
    EveryTickModel,
    OhlcExpandTickModel,
    OpenOnlyTickModel,
)
from simulator.usecase.ports import TickModelPort


@dataclass(frozen=True)
class TickModelSpec:
    """1 つの tick_model の許容値・構築方法・分岐種別を束ねる登録エントリ。

    Attributes:
        id: config の tick_model キー（許容値の単一情報源）。
        synthetic_builder: ``ohlc_order`` を受けて合成 TickModelPort を生成する
            ファクトリ。real_ticks（実ティック I/O 経路）は ``None``。
        requires_real_ticks: True のとき main は合成生成でなく実ティック構築経路
            （``_build_real_tick_model``）へ分岐する。
        requires_market_data: この modelling がバー系列を消費するか。``False`` の
            とき Composition Root はデータを読まない構成（Null 実装）を選ぶ。
            既定 ``True``＝従来どおりバー系列を要求する（既存エントリは無改変）。
    """

    id: str
    synthetic_builder: Optional[Callable[[str], TickModelPort]]
    requires_real_ticks: bool
    requires_market_data: bool = True


# 登録順は従来 config_loader の Literal 記載順を保つ（許容値の順序を byte 不変に）。
TICK_MODEL_REGISTRY: dict[str, TickModelSpec] = {
    "every_tick": TickModelSpec(
        id="every_tick",
        synthetic_builder=lambda ohlc_order: EveryTickModel(),
        requires_real_ticks=False,
    ),
    "ohlc_expand": TickModelSpec(
        id="ohlc_expand",
        synthetic_builder=lambda ohlc_order: OhlcExpandTickModel(order=ohlc_order),
        requires_real_ticks=False,
    ),
    "open_only": TickModelSpec(
        id="open_only",
        synthetic_builder=lambda ohlc_order: OpenOnlyTickModel(),
        requires_real_ticks=False,
    ),
    "real_ticks": TickModelSpec(
        id="real_ticks",
        synthetic_builder=None,
        requires_real_ticks=True,
    ),
    # A-1（ISSUE-397）: `Math calculations`（`Model=3`）。ティックを生成せず（NullTickModel）
    # バー系列も消費しない（requires_market_data=False）。合成側の既存 else がそのまま
    # 構築するため、`_make_tick_model` にも `build_interactor` にも新しい分岐は要らない。
    "math_calculations": TickModelSpec(
        id="math_calculations",
        synthetic_builder=lambda ohlc_order: NullTickModel(),
        requires_real_ticks=False,
        requires_market_data=False,
    ),
}

# config_loader の Literal 許容値はこの id タプルから導出する（単一情報源）。
TICK_MODEL_IDS: tuple[str, ...] = tuple(TICK_MODEL_REGISTRY)


def consumes_market_data(tick_model_id: str) -> bool:
    """``tick_model`` がバー系列を消費するか（本表の宣言を読むだけ）。

    宣言は ``TickModelSpec.requires_market_data`` の 1 箇所にしかない。読む側
    （Composition Root のファクトリ選択・Settings 層の規則 S）は本関数を通す——
    ``TICK_MODEL_REGISTRY[...].requires_market_data`` を各所で書き写すと、未登録キーの
    既定（「消費する」）の扱いが読む側ごとに食い違う。

    未登録キーは「消費する」とみなす（`_make_tick_model` の既定フォールバックと同じ扱い
    ＝既存挙動不変）。列挙外の値そのものは config_loader の pydantic 検証が拒否する。
    """
    spec = TICK_MODEL_REGISTRY.get(tick_model_id)
    return True if spec is None else spec.requires_market_data
