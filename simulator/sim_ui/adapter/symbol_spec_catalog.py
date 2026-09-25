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

    提供するのは **JP225 の 2 系列**である（ISSUE-511 段階 8-D-3・依頼者承認 2026-09-25）:
    従来の ``_JP225_REF``（気配幅なし）と ``_JP225_SPREAD_REF``（気配幅つき）。同じ銘柄・同じ
    供給元の**別の実体**であり、2 本を分ける識別子は dataset ref ただ 1 つ、**並びは
    （従来, 気配幅つき）に固定**する（理由は ``_offered`` の注記）。投入 body は 1 バイトも
    増えない（PROFILE_KEYS は 11 キーで dataset を含まない）。なお **UI からは 2 本目を
    選べない**——front の ``symbolCandidatesOf`` が同一銘柄を 1 候補へ畳み、``resolveProfile``
    が銘柄一致の先頭を返すためである（選べるようにするのは段階 8-D-5 の担当であり、本段では
    ``web/`` を 1 バイトも変えていない）。

    data_path の所在は**台帳が持つ**（ISSUE-511 段階 8-D-2）: 本カタログは「何を提供するか」＝
    dataset ref 名を持ち、「その実体はどこか」は ``marketdata/dataset_registry.py``
    の同じ ref の宣言へ委ねる（``whitelist()`` で引く）。**台帳を列挙はしない**——台帳には
    sim で走らない ref（日足・ティック由来の系列）も居るためである。台帳に無い ref なら
    読込時に ``KeyError`` で止まる（既定パスへ落とさない）。その ``KeyError`` には
    **どこを直すか**を載せる（ref・台帳の所在・本ファイル名の 3 つ。理由は下の except 節の
    注記、機械的検査は `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` の
    test_a_ref_missing_from_the_ledger_stops_with_an_actionable_message）。

    実体は **marketdata 形式 6 列**（``date,open,high,low,close,volume``・**気配幅の列なし**・
    2026-09-19 実測）であり、形式の判定は読み手の所有者、形式ごとのリーダの選択は
    ``simulator/main/ea_bindings/sources.py`` が持つ。台帳の宣言はソースコードであって
    ユーザー供給でない（パストラバーサル無関係・``StaticFileServer`` の許可根判定を経由しない）。

    台帳経由にして何が変わったか（**実測した事実のみ**・2026-09-23・本作業ツリー）:
        * 既定環境（環境変数 ``MARKETDATA_DATA_DIR`` 未設定）では、台帳の宣言は是正前の
          リテラル（当時の ``_REPO_ROOT / "data" / "marketdata" / "jp225_m1.csv"``）と**文字列
          として同一**であり、``datasets()`` の出力は byte 等価（368 バイト・sha256
          ``33e9ce8d21a265de4ab8e37019722520622d244b6fc7528942785219106532f6``。数え方:
          是正前後それぞれのソースを同一の ``__file__`` で実行し、``[p.to_dict() for p in
          datasets()]`` を ``sort_keys=True`` の JSON として UTF-8 符号化して突合した）。
        * ``MARKETDATA_DATA_DIR`` が設定された環境では、台帳の宣言はその基点の下
          （``<MARKETDATA_DATA_DIR>/jp225_m1.csv``）を指す。是正前は環境変数に関わらず
          「このファイルの parents[3]」の下を指していた。基点の規則の所有者は
          ``marketdata/paths.py`` である（本カタログはその規則を持たない）。
        * 稼働中の常駐の環境を実測した（数え方: ``ps -eo pid,cmd`` の全行から serve.sh /
          Composition Root 直起動 / router / *_tick_watch に該当する **8 プロセス**を採り、
          各々の ``/proc/<pid>/environ`` の ``MARKETDATA_DATA_DIR`` を読んだ。時点
          2026-09-23・本チェックアウト）: 未設定 2（unified_ui/serve.sh・mt5_tick_watch）、
          設定 6。設定側の値は 6 つとも ``marketdata/paths.py`` が与える既定と同一文字列で
          あり、**稼働中の構成では指す実体が変わらない**。
          （8-D-2 の当初記述は「常駐 4 プロセス・未設定 1・設定 3」と書いていたが、走査
          範囲を全常駐へ広げて数え直した値が上記である。結論は変わらない。）
        * worktree では指す実体が変わる: tools/setup_worktree.sh は本チェックアウトの
          data/marketdata を ``MARKETDATA_DATA_DIR`` へ export する（同スクリプト 71 行目・
          docs/git-worktree-workflow.md 110 行目も同じ絶対パスを案内）。是正前はこの
          カタログだけが worktree 側の（実体の無い）置き場を指していた。
        * ``datasets()`` 1 回が開くファイルの数は増えていない（台帳のパス解決は I/O を
          伴わない）。実測は `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py`。

    かつてここは「data_path は MT5 形式の実 JP225 M1 CSV（tests/fixtures 配下）」「本番データ配置は
    未確定」と書いていたが、data_path が上記へ移った後も記述が残って偽になっていた（ISSUE-511
    段階 8-D-1 の工程 5 レビュー 🟡-1 で是正）。同じ型の欠陥がもう 1 件あった:
    ``sim_ui/tests/unit/test_symbol_spec_catalog.py`` の docstring 2 が「data_path は
    dataset_registry.whitelist() の単一ソース由来」と主張していたのに、本モジュールの import に
    台帳は 1 件も無く、主張は現に偽だった（段階 8-D-2 で結線し、検定で機械的に結んだ）。

    建値基準（「``entry_price_basis``」）は**供給しない**（ISSUE-533 段階 2）: 判定の瞬間を
    知っているのは戦略だけであり、値の出所は戦略の宣言ただ 1 つである
    （`simulator/usecase/entry_price_basis.py`）。かつてここは「その実体が気配幅を供給
    するか」で建値基準を載せていたが、それはデータ実体に判定の瞬間を決めさせる形であり、
    **終値で判定する EA を気配幅つきの実体へ投げると run が始まらなかった**（実測
    2026-09-25: 宣言 'close' と設定 'current_open' の食い違いで exit=2）。供給をやめた
    ことで、経路（settings の有無）も実体も約定価格を決めなくなる——ISSUE-525 の経路差は
    この撤去の帰結として消える（対症は要らない）。表明は
    `simulator/tests/integration/test_entry_price_basis_single_source.py`。

    ``config_overrides`` という受け口そのものは残る（「``tick_model``」 等を運ぶ任意項目）。
    本カタログはそこへ**何も載せない**＝常にキーごと不在である。

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
    禁止）。以前は Composition Root の私有な登録表を越境 import し、「表のキー集合に既定
    フォールバック名を足す」という列挙規則を**書き写して**いた（ISSUE-405）。列挙の所有者は
    表の所有者、すなわち simulator/main/ea_bindings（EA ごとの宣言モジュールを登録した宣言駆動
    の束縛表）であり、列挙は known_ea_names の 1 箇所にしか無い。束ねるのは Composition Root
    である（R-4 と同型）。他銘柄は dataset 実体が確定するまで追加しない（YAGNI）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from marketdata.dataset_registry import whitelist
from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    load_snapshot,
    settlement_currency,
    spec_fields,
)
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile

# JP225 の dataset ref（セレクタのラベル・MT5 突合 fixture と同系譜）。
_JP225_REF = "jp225_m1"
# JP225 の**気配幅つき**系列の dataset ref（ISSUE-511 段階 8-D-3・依頼者承認 2026-09-25）。
# 同じ銘柄・同じ供給元の**別の実体**であり、2 本を分ける識別子は dataset ref ただ 1 つである。
# 投入 body は 1 バイトも増えない——front の投入キー許可リスト（PROFILE_KEYS）は 11 キーで
# dataset を含まない（決済通貨・データ範囲と同じ扱い。機械的検査は
# simulator/tests/unit/test_symbol_spec_catalog_second_series.py が front の現物から読む）。
_JP225_SPREAD_REF = "jp225_mt5_spread"
# 銘柄仕様の供給元（機械生成スナップショット）。銘柄名・サーバ名は**同一性**の指定であって
# 仕様の値ではない（値は 1 つもここに書かない）。
_JP225_SYMBOL = "JP225"
_JP225_SERVER = OANDA_JAPAN_MT5_LIVE

# JP225 の実行データ実体（依頼者承認 2026-09-06: 2012 年からの全期間 marketdata 系列）。
# **実体の所在は台帳が持つ**（ISSUE-511 段階 8-D-2）: ここは上の 2 つの ref で「何を提供するか」
# を名乗るだけで、「その実体はどこか」は台帳へ問う（詳細は上の docstring「data_path の所在は
# 台帳が持つ」）。_JP225_REF の形式は `date,open,high,low,close,volume`（ISO 日時・UTC）であり
# spread 列を持たないため、spread 依存 EA（MA_Slope 系）は N-17 が実行前に弾く。
# _JP225_SPREAD_REF は spread 列を持つ系列であり、そちらでは N-17 は踏まない（判定の軸は
# 「その実体が気配幅を供給するか」であって形式ではない・段階 8-C / 8-D-1）。従来の MT5 突合
# fixture（2025-01 の 1 ヶ月・JP225_M1_202501.csv）はテスト用途に残る（本カタログからは外す）。
def _declared_entity(ref: str) -> Path:
    """名乗った ``ref`` の実体を台帳の宣言から引く（無ければ**どこを直すか**を載せて止める）。

    名乗った ref が台帳に無い＝**名乗りと宣言の食い違い**であり、その食い違いを知って
    いるのは名乗った側（ここ）である。**既定のパスへは落とさない**（落とすと別系列の
    データで無言に走り、出力は形式上正しいため状態検証では検出できない）。

    素の KeyError にしないのは実測に基づく（2026-09-23・本作業ツリー）: この解決は読込時に
    走るため、simulator/sim_ui/main/run_job.py の _build_engine_binding を包む
    except Exception の網の内側で送出される。網が出すのは
    "Tester Settings の解釈に失敗しました: {exc}" だけなので、素の KeyError だと投入者が
    見るのは ref の綴り 1 語になる。台帳側の同型の Fail-Stop（dataset_registry の
    tick_tree_token が送る TickTokenMissing）は案内を載せており、ここだけ非対称だった。
    **型は KeyError のまま**（握る側の契約を変えない）。綴りは 3 つとも実体から導く
    （書き写すと片方だけ動いたときに案内が嘘になる）。

    関数にしてあるのは、提供する ref が 2 本になったとき（段階 8-D-3）に同じ案内を
    **手書きで複製しない**ためである（複製は必ず取り残しを生む）。
    """
    try:
        return whitelist()[ref]
    except KeyError:
        raise KeyError(
            f"dataset ref {ref!r} の宣言が台帳 {whitelist.__module__}.REGISTRY に"
            f"ありません。{Path(__file__).name} の名乗り（提供すると名乗る ref）か、"
            f"台帳の宣言のどちらかを揃えてください。既定のパスへは落としません。"
        ) from None


_JP225_DATA_CSV = _declared_entity(_JP225_REF)
_JP225_SPREAD_DATA_CSV = _declared_entity(_JP225_SPREAD_REF)


def _offered() -> "tuple[tuple[str, Path], ...]":
    """提供すると名乗る ref と、その実体（台帳の宣言）の対を**宣言順**で返す。

    **この並びが `datasets()` の並び**であり、run-options 応答とセレクタの並びである。
    先頭は従来の系列に固定する——共有フィクスチャと既存検定は profile を「先頭」または
    「銘柄一致の先頭」で引いており、2 本目を先頭へ入れると**赤にならずに別系列で走る**
    （実測 2026-09-25: 並びを入れ替えると、同じ 10 ファイルが 103.67 秒から 29 分超へ
    伸びたまま終わらない。失敗ではなく実行対象の入替としてのみ現れる）。並びの表明は
    `simulator/tests/unit/test_symbol_spec_catalog_second_series.py` が持つ。

    モジュール属性を**呼出ごとに読む**のは、実体の差し替え（既存検定が継ぎ目として使う
    ``_JP225_DATA_CSV`` / ``_JP225_SPREAD_DATA_CSV``）がそのまま届くようにするためである。
    """
    return (
        (_JP225_REF, _JP225_DATA_CSV),
        (_JP225_SPREAD_REF, _JP225_SPREAD_DATA_CSV),
    )


def _date_token_of_row(row: bytes) -> "str | None":
    """データ 1 行の先頭フィールドから `.ini` 日付トークン（`YYYY.MM.DD`）を取り出す。

    形式差はここに閉じる（実測 2 形式）: MT5 TAB 形式は `2025.01.02\t...`、marketdata
    comma 形式は `2012-06-14 10:35:00,...`。どちらでもなければ None。
    """
    import re

    head = row.split(b"\t", 1)[0].split(b",", 1)[0].decode("ascii", "replace").strip()
    date_part = head.split(" ", 1)[0]
    if re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", date_part):
        return date_part
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_part):
        return date_part.replace("-", ".")
    return None


def _csv_date_range(path: Path) -> "tuple[str | None, str | None]":
    """価格 CSV のデータ先頭/末尾の日付トークン（`YYYY.MM.DD`）を実測する。

    先頭はヘッダ直後の 1 行・末尾はファイル終端からの後読み（全走査しない・460 万行でも
    定数コスト）。読めない場合は (None, None)（表示なしへ縮退・投入には関与しない）。
    """
    try:
        with path.open("rb") as f:
            f.readline()                      # ヘッダ行
            first_row = f.readline()
            f.seek(0, 2)
            tail = min(f.tell(), 4096)
            f.seek(-tail, 2)
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
            last_row = lines[-1] if lines else b""
        return (_date_token_of_row(first_row), _date_token_of_row(last_row))
    except OSError:
        return (None, None)


class SymbolSpecCatalog(RunOptionsPort):
    """JP225 の実行プロファイルと ea_name 一覧を供給する単一ソース。"""

    def __init__(
        self,
        known_ea_names: "Callable[[], tuple[str, ...]]",
    ) -> None:
        """``known_ea_names``: 実行可能な EA 名を返す関数（**必須**）。

        束縛の実体は `simulator.main.known_ea_names`（登録表のキー＋既定 TC 経路の名前）。
        既定値を置かないのは R-4 と同型（既定束縛があると adapter → main の外向き依存が
        復活する）。銘柄仕様（`datasets`）は本カタログが権威だが、**実行可能な EA 名は
        エンジンが権威**であり、ここは中継するだけである。
        """
        self._known_ea_names = known_ea_names

    def datasets(self) -> "list[RunProfile]":
        # 供給元スナップショットを **1 回だけ**読み、銘柄仕様 8 項目と決済通貨をそこから引く。
        # リテラルを持たない＝人が値を選べない（ISSUE-445 RC-1 の是正・D2）。
        #
        # 系列ごとに読み直さないのは、提供する 2 本が同じ ``(サーバ, 銘柄)`` を指すためである
        # （ISSUE-511 段階 8-D-3・依頼者承認 2026-09-25）。2 度目の読込は出力に何も足さない
        # 純粋な無駄であり、出力は 1 ビットも変わらないため状態検証では落ちない。無駄の不在は
        # 計算量検定が「発行 − 相異なる実体の数 = 0」で表明する。
        snapshot = load_snapshot(_JP225_SERVER, _JP225_SYMBOL)
        return [self._profile(ref, entity, snapshot) for ref, entity in _offered()]

    def _profile(self, ref: str, entity: Path, snapshot: "dict") -> RunProfile:
        """提供する 1 系列のプロファイルを組む（2 本で同じ組み立てを手書き複製しない）。"""
        data_first, data_last = _csv_date_range(entity)
        return RunProfile(
            dataset=ref,
            data_path=str(entity),
            # 日付行の表示用データ範囲（実測読取・表示専用。読めなければ None）
            data_first_date=data_first,
            data_last_date=data_last,
            symbol=_JP225_SYMBOL,
            period="M1",
            # contract_size / digits / point_size / leverage / stops_level /
            # volume_min / volume_max / volume_step の 8 項目（供給元が唯一の権威）。
            **spec_fields(snapshot),
            # N-11（口座通貨 ≠ 決済通貨）の判定データ源。供給元の symbol.currency_profit。
            settlement_currency=settlement_currency(snapshot),
            # 決定論設定は 1 項目も供給しない（ISSUE-533 段階 2・上記 docstring）。
        )

    def ea_names(self) -> "list[str]":
        """実行可能な EA 名（注入元が権威・ハードコード表を持たない＝登録追加に追随）。"""
        return list(self._known_ea_names())  # 注入元が決定的順（昇順・重複なし）で返す
