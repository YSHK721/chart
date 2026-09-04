"""A-SymbolSpecCatalog: run config の銘柄仕様・データセット単一ソース（RunOptionsPort 実装）。

Phase 6 拡張（依頼者承認 2026-08-12・ブラウザ実 UI 完成）: 実行指示フォームが集める投入
body の profile 由来キー（data_path/symbol/period＋銘柄仕様 8 定数）を **単一ソース**から供給する。
front にこれらのリテラルを持たせない（front リテラル 0）。

権威値の出所（憶測禁止・**2026-08-25 に権威を移した**: ISSUE-445 段階 2 / D2）:
    JP225（OANDA-Japan MT5）の銘柄仕様は **供給元スナップショット**
    ``marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`` を唯一のオラクルとする。
    これは ``tools/capture_mt5_symbol_spec.py`` が MT5 端末の ``mt5.symbol_info()`` /
    ``mt5.account_info()`` から機械取得した生成物であり、人が値を選ばない・書かない。
    本カタログは ``marketdata.symbol_spec_snapshot`` 経由で読むだけであり、**銘柄仕様の
    数値リテラルを 1 つも持たない**（contract_size / digits / point_size / leverage /
    stops_level / volume_min / volume_max / volume_step の 8 項目すべて）。

    **以前は case.yaml を「唯一のオラクル」に指定していた。これは誤りだった**（ISSUE-445）:
    ``case.yaml`` は自身の冒頭で「人が読むための**メタ要約**であり、数値の最終オラクルは
    report.json 側」と宣言している。にもかかわらずここがメタ要約を権威に昇格させたため、
    MT5 レポートに一度も現れない逆算値 ``contract_size: 10``（真値 1.0）が権威として
    流通し、fixture 作成（2026-06-18）からライブ実接続（2026-08-25）まで 2 か月以上
    検出されなかった。供給元と突き合わせる機構が無いことが根本原因（RC-1）である。

    **volume_min/volume_max/volume_step も供給元から引く**: 従来ここは承認済み設計値
    0.01/100/0.01 を保持し「結果に効かない（gate-neutral）」と注記していたが、その成立条件は
    **バックテスト内に限る**（ISSUE-445 影響 B）。供給元の実測は 1.0/10000.0/1.0 であり、
    ライブでは ``lot=0.1`` は発注不成立になる。値を人が選ぶ余地を残さないため供給元へ寄せる。

    **stops_level は 0 ではなく 5**（供給元 ``trade_stops_level``・実測）。0 は出所の無い値
    だった。この変更で結果が変わる戦略の実測は下記「stops_level の影響」を参照。

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

    stops_level の影響（実測 2026-08-25・0 → 5）:
        ``MA_Slope`` は SL/TP を持たず ``stops_level`` を参照しないため reconcile golden は
        不変。``MaSlopePending`` / ``StopEntryProbe`` は ``cfg["stops_level"] * point`` を
        建値オフセットと SL/TP 距離の**下限クランプ**に使う。実走で突き合わせた結果、
        両戦略とも 0 と 5 で **bit-exact 一致**した（confirmation fixture 実測:
        MA_Slope_Pending_EA trades=1770/net=-4610.0・StopEntryProbe_EA trades=10100/net=9990.0・
        trades 系列 sha256 一致）。理由はクランプの閾値 ``5 × 0.1 = 0.5`` 価格単位が、
        リポジトリ内の全呼出値（entry_offset 50〜100pts = 5.0〜10.0 / SL 200pts = 20.0 /
        TP 500pts = 50.0）を下回り、一度も効かないためである。
        この実測は空虚ではない（負の対照: stops_level=100 で pending は trades 1770 → 3288、
        stops_level=200 で probe は 10100 → 2127 へ変化する）。

    settlement_currency（決済通貨・A-2 で恒久化）: TESTER_SETTINGS の非対象判定 N-11
    （口座通貨 ≠ 銘柄の決済通貨を拒否）が突き合わせる**判定データ源の権威**。
    **供給元スナップショットの ``symbol.currency_profit``（＝銘柄の profit 通貨）を権威とする**
    （ISSUE-445 段階 2 で case.yaml から移管）。これは MT5 端末が銘柄の属性として出力する
    決済通貨そのものであり、従来のように「人が case.yaml に書いた値」ではない。
    独立な証拠が 3 点あり、すべて JPY で一致する（憶測禁止・値をここに書かない）:
        1. ``.../expected/report.json`` ``settings.currency``（実 MT5 テスターの**口座通貨**）。
        2. ``.../mt5_report/tester.log`` L13 ``initial deposit 10000 JPY, leverage 1:10``（同上）。
        3. ``.../expected/report.json`` ``settings.derived.note``（**損益の建て通貨**）。
    1・2 は口座通貨、3 は決済通貨の証拠である。両者の一致は「本 fixture は口座通貨＝決済通貨の
    ケース」（N-11 非該当）を示すにとどまり、MT5 側が通貨不一致を拒否するという主張はここでは
    していない（未検証・拡大解釈をしない）。
    一致は ``sim_ui/tests/integration/test_run_options_mt5_gate.py`` が供給元と fixture から
    直接引いて機械的に固定する（期待値をテスト側にリテラルで持たない）。

    決済通貨は**フォーム投入 body には載せない**: ``SymbolSpec``（``usecase/models.py`` の
    8 フィールド・実測）にも ``build_interactor`` の引数にも通貨は無い（実測: main/__init__.py
    に ``currency`` の出現 0 件）。載せると既存 backtest verbatim 契約の byte 等価が壊れる。
    front の投入キー許可リスト（``sim_execution_panel_view.js`` の ``PROFILE_KEYS``）は 11 キーの
    ままであり、``to_dict()`` にキーが 1 つ増えても投入 body は不変である。

    ea_name 一覧は**注入**で受ける（束縛は `simulator.main.known_ea_names`・§12.1 ハードコード
    禁止）。以前は ``from simulator.main import _EA_FACTORIES`` で私有な登録表を越境 import し、
    ``set(_EA_FACTORIES) | {"TC24051901"}`` という列挙と既定フォールバック名を**書き写して**
    いた（ISSUE-405）。列挙の所有者は表の所有者（`simulator.main`）であり、束ねるのは
    Composition Root である（R-4 と同型）。他銘柄は dataset 実体が確定するまで追加しない（YAGNI）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    load_snapshot,
    settlement_currency,
    spec_fields,
)
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile

# JP225 の dataset ref（セレクタのラベル・MT5 突合 fixture と同系譜）。
_JP225_REF = "jp225_m1"
# 銘柄仕様の供給元（機械生成スナップショット）。銘柄名・サーバ名は**同一性**の指定であって
# 仕様の値ではない（値は 1 つもここに書かない）。
_JP225_SYMBOL = "JP225"
_JP225_SERVER = OANDA_JAPAN_MT5_LIVE

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

    def __init__(self, known_ea_names: "Callable[[], tuple[str, ...]]") -> None:
        """``known_ea_names``: 実行可能な EA 名を返す関数（**必須**）。

        束縛の実体は `simulator.main.known_ea_names`（登録表のキー＋既定 TC 経路の名前）。
        既定値を置かないのは R-4 と同型（既定束縛があると adapter → main の外向き依存が
        復活する）。銘柄仕様（`datasets`）は本カタログが権威だが、**実行可能な EA 名は
        エンジンが権威**であり、ここは中継するだけである。
        """
        self._known_ea_names = known_ea_names

    def datasets(self) -> "list[RunProfile]":
        # 供給元スナップショットを 1 回読み、銘柄仕様 8 項目と決済通貨をそこから引く。
        # リテラルを持たない＝人が値を選べない（ISSUE-445 RC-1 の是正・D2）。
        snapshot = load_snapshot(_JP225_SERVER, _JP225_SYMBOL)
        return [
            RunProfile(
                dataset=_JP225_REF,
                data_path=str(_JP225_MT5_CSV),
                symbol=_JP225_SYMBOL,
                period="M1",
                # contract_size / digits / point_size / leverage / stops_level /
                # volume_min / volume_max / volume_step の 8 項目（供給元が唯一の権威）。
                **spec_fields(snapshot),
                # N-11（口座通貨 ≠ 決済通貨）の判定データ源。供給元の symbol.currency_profit。
                settlement_currency=settlement_currency(snapshot),
                # MT5 ローダ EA は建値系列に close を持たず open を持つ（実測）。既定 close では
                # 建値系列未登録で job 失敗するため、本データセットは current_open を権威供給する。
                config_overrides={"entry_price_basis": "current_open"},
            )
        ]

    def ea_names(self) -> "list[str]":
        """実行可能な EA 名（注入元が権威・ハードコード表を持たない＝登録追加に追随）。"""
        return list(self._known_ea_names())  # 注入元が決定的順（昇順・重複なし）で返す
