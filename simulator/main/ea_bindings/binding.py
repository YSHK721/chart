"""EA 束縛の型（`EaBinding` / `EaBuildContext`）。ISSUE-502 段階 4A。

本モジュールが型だけを持ち、登録表（ea_bindings/__init__.py）と各 EA モジュールの
**両方から** import されるのは、循環を作らないためである（登録表が EA モジュールを
読み、EA モジュールが型を要るため）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from simulator.domain.exceptions import ConfigError


@dataclass(frozen=True)
class EaBuildContext:
    """EA の構築入力（ジョブ仕様のうち、その EA が読む分）。

    **フィールドを EA ごとに生やさない**（ISSUE-502 段階 4A・OCP）。是正前は EA が私有
    パラメータを 1 つ増やすたびに本型へフィールドを 1 つ足す形であり、Composition Root の
    型が EA の都合で改変され続けていた（EA 追加の 8 編集点のうち 1 つ）。名前を持つのは
    EA 側の宣言であり、本型は値を運ぶだけである。

    ``data_path`` だけを独立させているのは、これが「読むものが在るか」という
    **選択規則の入力**でもあり、EA 私有パラメータとは別格だからである
    （データを読まない構成はこれを参照しないことが本体である）。
    """

    data_path: Any
    #: ジョブ仕様（build_interactor と EA 構築入口の引数束）。
    params: "Mapping[str, Any]" = field(default_factory=dict)

    def param(self, name: str) -> Any:
        """ジョブ仕様から値を引く（欠落は沈黙補完せず `ConfigError`）。

        事前条件: ``name`` がジョブ仕様に載っていること。
        事後条件: 載っていた値をそのまま返す（型変換も既定補完もしない）。
        例外: ``ConfigError``。既定値を捏造して実行を続けると、EA が「呼出側が渡した
            つもりの値」ではなく偶然の既定で走り、しかも数値が出るため気づけない。
        """
        try:
            return self.params[name]
        except KeyError:
            raise ConfigError(
                f"EA の構築に必要なジョブ仕様のキーがありません: {name}",
                context={"param": name, "available": sorted(self.params)},
            ) from None


@dataclass(frozen=True)
class EaBinding:
    """EA 1 本の宣言。**EA を足すのに書くのはこれ 1 つだけ**である。

    ``name``: `ea_name`（登録表のキー）。
    ``build``: `(strategy, registry, market_data)` を返すファクトリ。
    ``strategy_params``: その EA が戦略へ配らせたい build_interactor 引数名。
        Composition Root は本宣言の**和**を戦略パラメータとして組む——是正前は
        Composition Root 本体に 14 行の dict リテラルが在り、EA が参照する
        パラメータを増やすたびにそこを開いていた（EA 追加の 8 編集点のうち 1 つ）。
        他 EA が参照しないパラメータが配られても無害である（現状の契約を変えない）。
    """

    name: str
    build: "Callable[[EaBuildContext], tuple[Any, Any, Any]]"
    strategy_params: "tuple[str, ...]" = ()
