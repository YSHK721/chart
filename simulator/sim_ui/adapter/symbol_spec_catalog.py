"""A-SymbolSpecCatalog: run config の銘柄仕様・データセット単一ソース（RunOptionsPort 実装）。

Phase 6 拡張（依頼者承認 2026-08-12・ブラウザ実 UI 完成）: 実行指示フォームが集める投入
body の profile 由来キー（data_path/symbol/period＋銘柄仕様 8 定数）を **単一ソース**から供給する。
front にこれらのリテラルを持たせない（front リテラル 0）。

権威値の出所（憶測禁止）:
    JP225（OANDA-Japan MT5）の**結果に効く定数**は MT5 突合 fixture の case.yaml
    （``simulator/tests/fixtures/mt5/ma_slope_jp225_202501/case.yaml``）を唯一のオラクルとする:
        contract_size=10 / digits=1 / point_size=0.1 / leverage=10 / symbol=JP225 / period=M1。
    stops_level=0 は reconcile（``tests/integration/test_ma_slope_reconcile.py:90``）と一致。
    これらが reconcile golden を bit-exact 再現することを ``test_run_options_mt5_gate.py`` が担保する。

    **volume_min/volume_max/volume_step は結果に効かない（gate-neutral）**: lot=0.1 は
    volume_step=0.01 でも 0.1 でも Order.validate を通る。case.yaml は volume を持たず、
    reconcile テストは 0.1/100/0.1 を使う。ここでは承認済み設計値 0.01/100/0.01 を保持する
    （どちらでも golden は不変）。MT5 ゲートは volume を突合対象にしない。

    data_path は **MT5 形式の実 JP225 M1 CSV**（カタログ authored 固定パス）を指す。理由（実測・
    憶測禁止）: run の実行ローダ（EA factory が選ぶ MarketDataPort）が**実際に読める形式**の実
    データでなければ、投入 job はデータ読込段で ``MissingBarError`` になり通過条件が成立しない。
    dataset_registry の ``jp225_m1.csv``（列 ``date,open,high,low,close,volume``・``time``/``spread``
    無し）は comma ローダ（``CsvOHLCRepository`` COMMA_SPEC の必須列 ``time,…,spread``）でも
    MT5 ローダ（TAB ``<DATE>``）でも読めない（実測）。よって MT5 ローダ EA（MA_Slope 系）が読める
    **MT5 突合 fixture と同系譜の実 OANDA-Japan MT5 JP225 M1**（``JP225_M1_202501.csv``・TAB
    ``<DATE> <TIME> … <SPREAD>``）を data_path とする。カタログが著したリテラル固定パスであり
    ユーザー供給でない（パストラバーサル無関係・``StaticFileServer`` の許可根判定を経由しない）。

    **本番データ配置は未確定**（tests/fixtures 配下の MT5 実データを参照している）。恒久的な
    本番 JP225 データの配置場所は別途 ISSUE 化する（本 Phase は通過条件成立を優先）。

    config_overrides（entry_price_basis=current_open）: MT5 ローダ EA（MA_Slope 系）の指標
    レジストリは建値基準系列に ``open`` を持ち ``close`` を持たない（実測）。GenericConditionStrategy
    の建値は ``required_price_series(entry_price_basis)`` で決まり、既定 ``close`` は当該 EA で
    系列未登録になる。よって本 MT5 データセットの profile は ``current_open``（→``open`` 系列）を
    権威値として供給する（front リテラル 0・UI フィールドを増やさない）。

    ea_name 一覧は ``simulator.main._EA_FACTORIES`` の keys＋既定 TC 経路から導出する（§12.1
    ハードコード禁止）。他銘柄は dataset 実体が確定するまで追加しない（YAGNI）。
"""
from __future__ import annotations

from pathlib import Path

from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile

# build_interactor 既定 TC 経路の ea_name（_EA_FACTORIES 未登録キーは _factory_tc24051901 へ
# フォールバックする・main/__init__.py）。reconcile / decorator 検定と同じ名前。
_DEFAULT_EA = "TC24051901"

# JP225 の dataset ref（セレクタのラベル・MT5 突合 fixture と同系譜）。
_JP225_REF = "jp225_m1"

# リポジトリ根 = simulator/sim_ui/adapter/symbol_spec_catalog.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]
# MT5 形式の実 JP225 M1 CSV（実 OANDA-Japan MT5・MT5 突合 fixture と同系譜・TAB <DATE> 形式）。
# 本番配置は未確定（別 ISSUE）。カタログ authored 固定パス（ユーザー供給でない）。
_JP225_MT5_CSV = (
    _REPO_ROOT
    / "simulator" / "tests" / "fixtures" / "mt5" / "ma_slope_jp225_202501"
    / "input" / "JP225_M1_202501.csv"
)


class SymbolSpecCatalog(RunOptionsPort):
    """JP225 の実行プロファイルと ea_name 一覧を供給する単一ソース。"""

    def datasets(self) -> "list[RunProfile]":
        return [
            RunProfile(
                dataset=_JP225_REF,
                data_path=str(_JP225_MT5_CSV),
                symbol="JP225",
                period="M1",
                # --- 結果に効く定数（case.yaml 権威値・MT5 ゲートで突合）---
                contract_size=10.0,
                digits=1,
                point_size=0.1,
                leverage=10.0,
                stops_level=0,
                # --- gate-neutral（結果に効かない・承認済み設計値）---
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
                # MT5 ローダ EA は建値系列に close を持たず open を持つ（実測）。既定 close では
                # 建値系列未登録で job 失敗するため、本データセットは current_open を権威供給する。
                config_overrides={"entry_price_basis": "current_open"},
            )
        ]

    def ea_names(self) -> "list[str]":
        from simulator.main import _EA_FACTORIES

        # _EA_FACTORIES の keys＋既定 TC 経路（ハードコード表を持たない＝登録追加に追随）。
        names = set(_EA_FACTORIES) | {_DEFAULT_EA}
        return sorted(names)  # 決定的順（重複なし）
