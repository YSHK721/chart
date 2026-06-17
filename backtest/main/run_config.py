"""RunConfig — main 層の run-config 用モデル（繰り延べ課題の解決）。

コミット済 ``BacktestConfig``（決定論 9 項目専用・属性アクセス）は変更しない。一方
コミット済 ``TC24051901`` は ``on_init`` で受けた config を **subscript**（``cfg["lot_size"]``
等）でアクセスする。Interactor は同じ config を ``config.sltp_tie``（属性）で読みつつ
``strategy.on_init(config, ...)`` へそのまま渡す（run_backtest.py:114/181 を Read で実証）。

両者を 1 オブジェクトで満たすため、RunConfig は決定論 9 項目を **属性委譲**（``__getattr__``）
で公開し、戦略パラメータを **subscript**（``__getitem__``）で公開する。これにより
コミット済コードを一切変更せずに Interactor／戦略の双方の config 契約を充足する。

main 層は全層を import 可（Composition Root）。
"""
from __future__ import annotations

from typing import Any

from backtest.usecase.models import BacktestConfig


class RunConfig:
    """決定論 9 項目（属性）＋戦略パラメータ（subscript）を 1 つで供給する run-config。

    2 つの契約を 1 オブジェクトで満たす（呼び分けはアクセス構文で決まる）:

    - 属性アクセス ``config.sltp_tie`` 等 → ``__getattr__`` が決定論 ``BacktestConfig``
      へ委譲（Interactor 用）。決定論 config に存在しない属性は ``AttributeError`` を
      送出する（欠落キーは loud に失敗する＝サイレント補完しない）。
    - subscript ``config["lot_size"]`` 等 → ``__getitem__`` が戦略パラメータ dict を
      参照（TC24051901 用）。dict に存在しないキーは ``KeyError`` を送出する（同上）。

    いずれの契約も欠落を黙殺せず例外で露見させる設計のため、結線ミスが run 実行前後で
    早期に検出される。committed ``BacktestConfig`` / ``TC24051901`` は変更しない。
    """

    def __init__(self, determinism: BacktestConfig, strategy_params: dict[str, Any]) -> None:
        # 委譲先は __getattr__ の再帰回避のため __dict__ に直接格納する。
        object.__setattr__(self, "_determinism", determinism)
        object.__setattr__(self, "_strategy_params", dict(strategy_params))

    @property
    def determinism(self) -> BacktestConfig:
        return self._determinism

    @property
    def strategy_params(self) -> dict[str, Any]:
        return dict(self._strategy_params)

    def __getattr__(self, name: str) -> Any:
        # 自身に無い属性は決定論 config へ委譲（sltp_tie 等）。
        determinism = object.__getattribute__(self, "_determinism")
        return getattr(determinism, name)

    def __getitem__(self, key: str) -> Any:
        return self._strategy_params[key]
