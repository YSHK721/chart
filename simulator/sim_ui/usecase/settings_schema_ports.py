"""U-SettingsSchemaPort: Tester Settings フォームの選択肢・制約の境界（usecase・Phase 8）。

MT5 Tester Settings 準拠の設定パネルが「どのキーを・どの順で・何を選べるか」と、
「選ぶと投入前に止まる値はどれか（非対象の理由）」を供給する境界。`RunOptionsPort`
（データセット／EA の選択肢）とは**別の境界**である——供給元も変更理由も異なる
（アクター分離・SRP。基本設計 §18.3）。投入経路（SubmitJobInteractor）には足さない。

DIP: usecase は本抽象にのみ依存し、`.ini` の語彙表・非対象の宣言表・EA 名の権威という
具体を知らない（束縛は adapter=`TesterSettingsSchemaCatalog` と Composition Root が持つ）。
プレーン DTO（`SchemaOption` / `UnsupportedNotice`）で境界を跨ぐ。

ラベルについて: 選択肢の ``label`` は**列挙メンバ名**である。MT5 の UI 文言は本リポジトリ
内に根拠が無く、発明しない（基本設計 §18.3・`.claude/CLAUDE.md`「実証的証拠のない仮定で
実装しない」）。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaOption:
    """選択肢 1 件（`.ini` の生トークンと表示ラベル）。

    ``token``: `.ini` に書かれるそのままの値（`Period` はラベル文字列・`Model` は生 int の
        文字列表記）。front はこの値をそのまま投入し、字形を組み立て直さない。
    ``label``: 画面表示名（＝列挙メンバ名）。意味の翻訳はしない（上の「ラベルについて」）。
    """

    token: str
    label: str

    def to_dict(self) -> "dict":
        """JSON 直列化用のプレーン dict（API 応答が使う）。"""
        return {"token": self.token, "label": self.label}


@dataclass(frozen=True)
class UnsupportedNotice:
    """非対象（保証境界）1 件の告知。

    実行時に Fail-Stop する設定を**投入前に**理由付きで示すための DTO（裁定 T-5）。
    フィールド名 ``unsupported_id`` は宣言側（`main/tester_settings/unsupported.py` の
    `UnsupportedRule.unsupported_id`）と同一語彙にする。同じ概念に 2 つの呼び名
    （``id`` と ``unsupported_id``）を作らないためである（当該 docstring の明示規約）。

    ``keys`` / ``trigger`` / ``tokens`` は**UI 束縛の宣言**（R-9）である。front は
    「どの選択がこの告知に当たるか」をこの 3 つだけで判定し、キー名からフィールド名を
    再導出したり、既定値との差分を該当の代理にしたりしない。推測で結ぶと、宣言と
    食い違ったときに静かに 0 件（または過剰発火）になる。語彙（``trigger`` の値）は
    宣言側の ``UI_TRIGGER_*`` と同一である。
    """

    unsupported_id: str
    field: str
    reason: str
    tbd: "str | None" = None
    keys: "tuple[str, ...]" = ()
    trigger: str = ""
    tokens: "tuple[str, ...]" = ()

    def to_dict(self) -> "dict":
        """JSON 直列化用のプレーン dict。``tbd`` / ``tokens`` は無いときキーを載せない。"""
        payload = {
            "unsupported_id": self.unsupported_id,
            "field": self.field,
            "reason": self.reason,
            "keys": list(self.keys),
            "trigger": self.trigger,
        }
        if self.tbd is not None:
            payload["tbd"] = self.tbd
        if self.tokens:
            payload["tokens"] = list(self.tokens)
        return payload


class SettingsSchemaPort(abc.ABC):
    """Tester Settings フォームの schema を供給する境界（6 面）。"""

    @abc.abstractmethod
    def key_order(self) -> "tuple[str, ...]":
        """`[Tester]` のキーを標準キー順で返す（表示順の権威）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def required_keys(self) -> "tuple[str, ...]":
        """他の選択に関わらず**常に**必要なキーを返す。"""
        raise NotImplementedError

    @abc.abstractmethod
    def enum_options(self) -> "dict[str, list[SchemaOption]]":
        """列挙キー → 選択肢一覧（決定的順）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def scalar_specs(self) -> "dict[str, dict]":
        """列挙でないキー → そのキーの仕様（Expert 専用か・実証状態など）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def expert_options(self) -> "list[SchemaOption]":
        """`Expert` に指定できる対象一覧（実行可能 EA 名＋対象接尾辞）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def unsupported(self) -> "list[UnsupportedNotice]":
        """非対象の告知一覧（投入前に理由を示すため）。"""
        raise NotImplementedError
