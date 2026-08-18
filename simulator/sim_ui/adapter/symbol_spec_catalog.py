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

    settlement_currency（決済通貨・A-2 で恒久化）: TESTER_SETTINGS の非対象判定 N-11
    （口座通貨 ≠ 銘柄の決済通貨を拒否）が突き合わせる**判定データ源の権威**。実測の出典は
    4 点で一致する（憶測禁止・値をここ以外に書かない）:
        1. ``simulator/tests/fixtures/mt5/ma_slope_jp225_202501/case.yaml`` L20
           ``symbol.currency: JPY``（L13 コメント「銘柄仕様 (実 MT5 由来の確定値)」の直下）。
        2. ``.../expected/report.json`` L19 ``"settings"."currency": "JPY"``
           （実 MT5 ストラテジーテスター出力＝数値の最終オラクル。テスター口座の通貨）。
        3. ``.../mt5_report/tester.log`` L13 ``initial deposit 10000 JPY, leverage 1:10``。
        4. ``.../expected/report.json`` L29 ``settings.derived.note``
           ``profit=(exit-entry)*lot*contract; 0.1lot*10=1 JPY per price unit``。
    2 と 3 は**口座通貨**、1 は銘柄仕様ブロックの記載、4 は**損益の建て通貨**（＝銘柄の
    決済/profit 通貨）である。決済通貨の直接証拠は 4 であり、1 がそれと同じ JPY を銘柄仕様
    として記録している。2・3 との一致は「本 fixture は口座通貨＝決済通貨のケース」（N-11 非
    該当）であることを示すにとどまり、MT5 側が通貨不一致を拒否するという主張はここでは
    していない（未検証・拡大解釈をしない）。
    一致は ``sim_ui/tests/integration/test_run_options_mt5_gate.py`` が fixture から
    直接引いて機械的に固定する（期待値をテスト側にリテラルで持たない）。

    決済通貨は**フォーム投入 body には載せない**: ``SymbolSpec``（``usecase/models.py`` の
    8 フィールド・実測）にも ``build_interactor`` の引数にも通貨は無い（実測: main/__init__.py
    に ``currency`` の出現 0 件）。載せると既存 backtest verbatim 契約の byte 等価が壊れる。
    front の投入キー許可リスト（``sim_execution_panel_view.js`` の ``PROFILE_KEYS``）は 11 キーの
    ままであり、``to_dict()`` にキーが 1 つ増えても投入 body は不変である。

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
                # N-11（口座通貨 ≠ 決済通貨）の判定データ源。出典はモジュール docstring の
                # 4 点（case.yaml L20 / report.json L19・L29 / tester.log L13）＝すべて JPY。
                settlement_currency="JPY",
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
