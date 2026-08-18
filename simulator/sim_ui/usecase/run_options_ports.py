"""U-RunOptionsPort: 実行指示フォームの選択肢（データセット＋EA）の境界（usecase・Phase 6 拡張）。

実行指示パネル（run config フォーム）が「どのデータセット／どの EA を選べるか」と、選んだ
データセットに紐づく **銘柄仕様の権威値**（build_interactor の default 無し 11 キー）を供給する
境界。投入経路（SubmitJobInteractor）には足さない（既存 backtest verbatim 契約 byte 不変）。

DIP: usecase は本抽象にのみ依存し、dataset_registry / _EA_FACTORIES という具体を知らない
（供給の束縛は adapter=SymbolSpecCatalog が持つ）。プレーン DTO（RunProfile）で境界を跨ぐ
（pydantic 型・Path を usecase へ入れない）。
"""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RunProfile:
    """1 データセットの実行プロファイル（build_interactor の銘柄仕様 11 キー＋dataset ラベル
    ＋決済通貨）。

    ``dataset`` はセレクタのラベル/値（dataset_registry の ref 名）。うち 11 キーは
    フォーム投入 body の profile 由来キー（front はこれらのリテラルを持たない）。

    ``config_overrides``（任意）: build_interactor の ``config_overrides`` へ渡す決定論設定。
    データセットの CSV 形式・EA ローダの組合せで既定の建値基準（``entry_price_basis``）が
    成立しない場合に、profile が権威値として供給する（front リテラル 0・UI フィールドを増やさない）。
    ``None`` のときフォーム投入 body に載せない（既存挙動と byte 等価）。

    ``settlement_currency``（必須・既定値なし）: 銘柄の決済通貨。TESTER_SETTINGS の非対象
    判定 N-11（口座通貨 ≠ 銘柄の決済通貨を拒否）が突き合わせる**判定データ源の権威**であり、
    値は `SymbolSpecCatalog`（データセット単一ソース）が供給する。既定値を置かないのは
    `EngineBinding.settlement_currency`（D-10）と同型の Fail-Stop である——「たぶん JPY」を
    DTO の既定値に置くと、通貨を持たないデータセットが沈黙で「通貨一致」扱いになり、
    通貨不一致の run が N-11 を素通りする。省略した構築は `TypeError` で即座に落ちる。

    ``settlement_currency`` は **フォーム投入 body の profile 由来 11 キーには含めない**
    （front の投入キー許可リストは 11 キーのまま）。銘柄仕様 8 キーは `SymbolSpec` に写るが
    決済通貨は `SymbolSpec` のフィールドではなく（実測）、`build_interactor` の投入契約にも
    無いため、載せると既存 backtest verbatim 契約の byte 等価が壊れるからである。
    """

    dataset: str
    data_path: str
    symbol: str
    period: str
    contract_size: float
    digits: int
    point_size: float
    leverage: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    settlement_currency: str
    config_overrides: "dict | None" = None

    def to_dict(self) -> "dict":
        """JSON 直列化用のプレーン dict（API 応答が使う）。``config_overrides=None`` は載せない。"""
        d = asdict(self)
        if d.get("config_overrides") is None:
            d.pop("config_overrides", None)
        return d


class RunOptionsPort(abc.ABC):
    """実行指示フォームの選択肢を供給する境界。"""

    @abc.abstractmethod
    def datasets(self) -> "list[RunProfile]":
        """選択可能なデータセットのプロファイル一覧を返す。"""
        raise NotImplementedError

    @abc.abstractmethod
    def ea_names(self) -> "list[str]":
        """選択可能な ea_name（指標セット）一覧を返す（決定的順）。"""
        raise NotImplementedError
