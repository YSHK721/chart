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

    提供するのは **JP225 の複数系列**である（ISSUE-511 段階 8-D-3 で 2 本・ISSUE-533 段階 3 で
    もう一方の供給の気配幅系列を加えた。依頼者承認 2026-09-26）。同じ銘柄・同じ供給元の
    **別の実体**であり、系列を分ける識別子は dataset ref ただ 1 つである。投入 body は 1 バイトも
    増えない（PROFILE_KEYS は 11 キーで dataset を含まない）。UI では系列は**銘柄の内側の
    第 2 の軸**として選べる（段階 8-D-5 で結線済み。front の ``symbolCandidatesOf`` が同一銘柄を
    1 候補へ畳み、``seriesCandidatesOf`` が同じ銘柄の ref を候補として配り、``resolveProfile`` が
    系列指定なしのときだけ銘柄一致の先頭を返す。候補が 2 以上でなければ軸は画面に出ない）。
    本段でも ``web/`` は 1 バイトも変えていない——提供が 1 本増えれば front の規則がそのまま
    候補を 1 つ増やす（読んで確かめた事実であり、**実 UI での確認は依頼者が行う**）。

    **何を提供するかも台帳が宣言する**（ISSUE-533 段階 3）: 本カタログは提供する ref の綴りを
    1 つも持たず、``marketdata/dataset_registry.py`` の ``sim_offered`` 欄を名乗った ref を
    宣言順に受け取る（`sim_offered_refs`）。以前ここは提供する ref を手書きの並びで名乗って
    いたため、台帳へ系列を足しても選択肢へ届かなかった（実測 2026-09-26: 実体つきの新系列が
    在るのに ``GET /run-options`` は 2 件だけ）。列挙は必ず取り残しを生む。台帳が ``sim_offered``
    を名乗らない ref を持つときは、選択肢を問う時点で **どこを直すか**を載せて止まる（宣言の
    欠落を「提供しない」へ倒さない・Fail-Stop の所有者は台帳側の `sim_offered_refs`）。

    data_path の所在も**台帳が持つ**（ISSUE-511 段階 8-D-2）: 「その実体はどこか」は同じ ref の
    宣言へ委ねる（``whitelist()`` で引く）。**台帳を丸ごと出すのではない**——台帳には sim で
    走らない ref（日足・同梱サンプル・気配幅なしのティック系列）も居り、それらは
    ``sim_offered=False`` を名乗っている。

    実体はいずれも **marketdata 形式**であり、従来の系列は 6 列（``date,open,high,low,close,volume``
    ・**気配幅の列なし**・2026-09-19 実測）、気配幅つきの系列は 9 列（``up,dn,spread`` を持つ・
    2026-09-26 実測）である。**本カタログは列形を判定しない**（形式の判定は読み手の所有者、
    気配幅を供給するかの判定は保証境界 N-17 の所有者）。形式ごとのリーダの選択は
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

from marketdata.dataset_registry import sim_offered_refs, whitelist
from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    load_snapshot,
    settlement_currency,
    spec_fields,
)
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile

# 銘柄仕様の供給元（機械生成スナップショット）。銘柄名・サーバ名は**同一性**の指定であって
# 仕様の値ではない（値は 1 つもここに書かない）。
_JP225_SYMBOL = "JP225"
_JP225_SERVER = OANDA_JAPAN_MT5_LIVE


def _offered() -> "tuple[tuple[str, Path], ...]":
    """提供する dataset ref と、その実体の対を**台帳の宣言順**で返す（ISSUE-533 段階 3）。

    **何を提供するかも台帳が宣言する**（`sim_offered_refs`）: 以前ここは提供する ref を手書きの
    並びで名乗っていた。そのため台帳へ系列を足しても選択肢へ届かず、実体つきの新系列が
    ``GET /run-options`` に現れなかった（実測 2026-09-26: 2 件だけ）。列挙は必ず取り残しを
    生むので、宣言の所有者を台帳ただ 1 つにする。ここに ref の綴りは 1 つも無い。

    **この並びが `datasets()` の並び**であり、run-options 応答とセレクタの並びである。並びの
    出所は台帳の宣言順であり、先頭は従来の系列（気配幅を宣言していない唯一の系列）である
    ——共有フィクスチャと既存検定は profile を「先頭」または「銘柄一致の先頭」で引いており、
    先頭が入れ替わると**赤にならずに別系列で走る**（実測 2026-09-25: 並びを入れ替えると、
    同じ 10 ファイルが 103.67 秒から 29 分超へ伸びたまま終わらない。失敗ではなく実行対象の
    入替としてのみ現れる）。表明は
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_offering.py` が持つ。

    台帳を**呼出ごとに読む**のは、宣言の差し替え（検定が継ぎ目として使う `whitelist` の実体）が
    そのまま届くようにするためである。名乗りが台帳由来になったので、「名乗った ref が台帳に
    無い」という食い違いは構造的に起こりえない（従来ここに在った読込時の Fail-Stop は、
    宣言の欠落を止める `sim_offered_refs` の Fail-Stop へ移った）。
    """
    entities = whitelist()
    return tuple((ref, entities[ref]) for ref in sim_offered_refs())


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
        # 系列ごとに読み直さないのは、提供する系列がすべて同じ ``(サーバ, 銘柄)`` を指すため
        # である（ISSUE-511 段階 8-D-3・依頼者承認 2026-09-25）。2 度目の読込は出力に何も足さない
        # 純粋な無駄であり、出力は 1 ビットも変わらないため状態検証では落ちない。無駄の不在は
        # 計算量検定が「発行 − 相異なる実体の数 = 0」で表明する。
        snapshot = load_snapshot(_JP225_SERVER, _JP225_SYMBOL)
        return [self._profile(ref, entity, snapshot) for ref, entity in _offered()]

    def _profile(self, ref: str, entity: Path, snapshot: "dict") -> RunProfile:
        """提供する 1 系列のプロファイルを組む（系列ごとに同じ組み立てを手書き複製しない）。"""
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
