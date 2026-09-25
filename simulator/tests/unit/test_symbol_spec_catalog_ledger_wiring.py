"""カタログの data_path を **台帳の宣言**へ結んだことの検定（ISSUE-511 段階 8-D-2）。

何を固定するか:
    「何を提供するか」（ref 名）はカタログが持ち、「その実体はどこか」（パス）は台帳
    `marketdata/dataset_registry.py` が持つ。カタログは自分が提供する ref 名 1 つを持ち、
    **パス解決だけ**を台帳へ委ねる。台帳を列挙はしない——台帳には sim で走らない ref
    （日足・ティック由来の系列）も居るためである。

是正前に何が偽だったか（実測 2026-09-23・本作業ツリー・HEAD e3a9fd7f）:
    `simulator/sim_ui/tests/unit/test_symbol_spec_catalog.py` の docstring 2 が
    「data_path は dataset_registry.whitelist() の単一ソース由来（ハードコードしない）」と
    主張していたが、カタログの import に台帳は 1 件も無く（``grep -n "dataset_registry\\|whitelist"
    simulator/sim_ui/adapter/symbol_spec_catalog.py`` が 0 件）、実体は
    ``_REPO_ROOT / "data" / "marketdata" / "jp225_m1.csv"`` という自前のリテラルだった。
    主張を機械で確かめる検定も 0 件だった（``grep -rn "dataset_registry" simulator/`` の
    ヒットはすべて docstring 内の言及）。**宣言だけが在って検査が無い**形である。

Red と回帰ガードの別（成功テスト先行を Red と称さない）:
    * 真の Red … `test_the_data_path_follows_the_ledger_declaration_for_the_ref`。
      台帳側の宣言を差し替える変異を当て、カタログがそれに従うことを要求する。是正前は
      リテラルのままなので落ちる（実測値は本作業の報告に記す）。
    * 真の Red … `test_a_ref_missing_from_the_ledger_stops_with_an_actionable_message`
      （段階 8-D-2 工程 4）。台帳から ref が消えたときの案内を要求する。是正前は素の
      ``KeyError`` で ref の綴り 1 語しか出なかったので落ちる。
    * 回帰ガード … 列挙していないこと・台帳の解決が I/O を伴わないこと・読取がデータ量で
      増えないこと。いずれも**是正前後のどちらでも緑**であり Red ではない。検出力は
      変異で実測する（同上）。

変異をどう当てるか（台帳の差し替えが届く継ぎ目）:
    カタログは実体のパスを**モジュール読込の時点で** 1 度だけ確定する（既存の計算量検定
    `test_symbol_spec_catalog_spread_axis.py` が継ぎ目として使っているモジュール属性
    ``_JP225_DATA_CSV`` を保つため）。よって台帳を差し替えた効果を見るには、差し替えた状態で
    カタログを**読み直す**必要がある。`_reimport_catalog` は sys.modules に登録しない別個の
    モジュール写しを作って読み直すため、進行中のセッションの `symbol_spec_catalog` には
    触れない（他の検定へ影響しない）。

計算量（この段で増やしてはならないもの）:
    台帳経由にしても `datasets()` が開くファイルの数は増えない。継ぎ目は 3 つ——ヘッダ読取
    （`ohlc_marketdata_csv._header_line`）・範囲読取（_csv_date_range）・銘柄仕様スナップ
    ショット読取（load_snapshot）であり、いずれも 発行 − **相異なる実体の数** = 0
    （データ側はプロファイルの数、スナップショット側は読んだ組の相異なる数）。
    そのうえで「開いたファイルの総数 − 3 継ぎ目の発行の和 = 0」を表明する——台帳のパス解決が
    ファイルを開けば、この差が正になって落ちる。**回数そのものは焼き込まない**。

    **開いた回数だけでは足りない**（ISSUE-511 段階 8-D-4・工程 5 レビュー 🟡-2 の是正）:
    開いた回数は「1 回の open の中で読む量が O(n) になる退化」を 1 ビットも検出しない。
    実測 2026-09-25（本作業ツリー・HEAD 14fc9a13）: _csv_date_range の後読み 3 行を先頭からの
    全読みへ退化させると、本ファイルと `simulator/tests/unit/test_symbol_spec_catalog_spread_axis.py`
    は **20 passed のまま素通しした**（出力の日付トークンも開いた数も変わらないため。壁時計は
    0.46s → 10.91s だが**時間は表明しない**）。また規模 2 点の「開いた数が等しい」は先行する
    表明（各継ぎ目 発行 − 使用 = 0）から論理的に含意され、新しい情報を 1 ビットも足していない
    （実測: 当該行を撤去しても 20 passed のまま）。よって継ぎ目を**読取の発行と配られた量**へ
    移し、規模 2 点の表明はそちらへ置く（test_the_read_volume_does_not_grow_with_the_data）。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy（モジュール属性の継ぎ目） … `marketdata/tests/spread_series_fixture.py`
    Test Spy（開いた口と配られた量） … `simulator/tests/file_read_spy.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`
"""
from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from marketdata.dataset_registry import REGISTRY, DatasetDescriptor, whitelist
from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.sim_ui.adapter import symbol_spec_catalog
from simulator.tests.file_read_spy import spy_file_reads
from simulator.tests.ohlc_header_fixtures import MD6, MD9, body_rows, write_header

#: カタログが提供すると名乗っている ref。**この綴りをここに書き写さない**——書き写すと、
#: カタログ側が別の ref を名乗るようになっても検定が古い綴りを測り続ける。
_REF = symbol_spec_catalog._JP225_REF

#: 台帳へ一時的に足す「sim では走らない」ref（列挙していないことの対照）。台帳の実物
#: （日足 jp225・ティック由来 jp225_tick / jp225_mt5）と同じ立場であり、カタログが台帳を
#: 列挙していればプロファイルが 1 件増える。
_UNRUNNABLE_REF = "not_runnable_in_sim"

#: 注入された基準値であることを示す番兵（エンジンの語彙ではない）。本ファイルは建値基準の
#: 値を測らないが、既定束縛の無い必須引数なので与える。
_INJECTED = "__injected_basis__"

#: 気配幅に依存しない EA（EA 名の注入元は本ファイルの関心外なので最小の束縛）。
_INDEPENDENT_EA = "TC24051901"


def _reimport_catalog():
    """いまの台帳の宣言でカタログを**読み直した**別個の写しを返す。

    sys.modules へ登録しないため、進行中のセッションが持つ
    `simulator.sim_ui.adapter.symbol_spec_catalog` は 1 ビットも変わらない
    （reload と違い、他の検定が掴んでいるクラス実体を差し替えない）。
    """
    spec = importlib.util.spec_from_file_location(
        "symbol_spec_catalog__under_ledger_mutation",
        Path(symbol_spec_catalog.__file__),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declare(monkeypatch, ref: str, descriptor: DatasetDescriptor) -> None:
    """台帳へ ``ref`` の宣言を一時的に置く（既存 ref なら差し替え・新規なら追加）。"""
    monkeypatch.setitem(REGISTRY, ref, descriptor)


def _relocated(path: str) -> DatasetDescriptor:
    """カタログの ref の宣言を、実体だけ ``path`` へ移した版（他の欄は台帳の実物のまま）。"""
    return replace(REGISTRY[_REF], path=Path(path))


def _datasets(module) -> list:
    """読み直したカタログ写しの `datasets()`（束縛は本ファイルの関心外なので最小）。"""
    return module.SymbolSpecCatalog(
        known_ea_names=lambda: (_INDEPENDENT_EA,), entry_price_basis=_INJECTED
    ).datasets()


# --- 1. 結線（真の Red）------------------------------------------------------------


def test_the_data_path_follows_the_ledger_declaration_for_the_ref(monkeypatch, tmp_path):
    """台帳が宣言する実体を差し替えると、カタログの data_path がそれに従う。

    カタログがパスを自前のリテラルで持っている限り、この差し替えは届かない＝落ちる。
    「何を提供するか」（ref 名）はカタログのまま変わらないことも同時に見る。
    """
    # Arrange
    declared = write_header(tmp_path, "relocated_jp225_m1.csv", MD6)
    _declare(monkeypatch, _REF, _relocated(declared))

    # Act
    profile = _datasets(_reimport_catalog())[0]

    # Assert
    assert profile.data_path == declared
    assert profile.dataset == _REF


def test_the_data_path_is_not_a_literal_of_the_catalog(monkeypatch, tmp_path):
    """2 つの別々の宣言へ順に差し替えると、data_path が 2 度とも追随する。

    1 点の一致は「たまたま同じ綴りのリテラルを持っている」でも満たせる。追随を 2 点で見ると、
    カタログ側にパスの値が無いことの実証になる。
    """
    # Arrange / Act
    measured = []
    for name in ("first_place.csv", "second_place.csv"):
        declared = write_header(tmp_path, name, MD6)
        _declare(monkeypatch, _REF, _relocated(declared))
        measured.append((_datasets(_reimport_catalog())[0].data_path, declared))
        monkeypatch.undo()

    # Assert
    assert [m[0] for m in measured] == [m[1] for m in measured]
    assert measured[0][0] != measured[1][0]   # 空振り防止（2 点は別の宣言である）


def test_a_ref_missing_from_the_ledger_stops_with_an_actionable_message(monkeypatch):
    """台帳から ref が消えたとき、止まるだけでなく**どこを直すか**が例外に載る。

    なぜ案内が要るか（実測 2026-09-23・本作業ツリー）: この解決は読込時に走るため、
    `simulator/sim_ui/main/run_job.py` の _build_engine_binding を包む except Exception の網
    （同 :429-439）の内側で送出される。網が出すのは :436 の
    "Tester Settings の解釈に失敗しました: {exc}" だけなので、素の ``KeyError`` だと
    **投入者が見るのは ref の綴り 1 語だけ**になり、台帳を直せばよいのかカタログを直せば
    よいのか判らない。台帳の同型の Fail-Stop（dataset_registry の tick_tree_token が送る
    TickTokenMissing）は案内を載せており、ここだけ非対称だった。

    固定するのは**案内に何が載っているか**（ref・台帳の所在・カタログのファイル）であって
    文面ではない。3 つとも綴りを書き写さず、import 済みの実体から導く。
    """
    # Arrange
    monkeypatch.delitem(REGISTRY, _REF)

    # Act
    with pytest.raises(KeyError) as caught:      # 型は従来どおり（握る側の契約を変えない）
        _reimport_catalog()

    # Assert
    message = str(caught.value)
    assert _REF not in whitelist()               # 空振り防止（宣言は実際に消えている）
    assert _REF in message                       # どの ref か
    assert whitelist.__module__ in message       # どの台帳を直すか
    assert Path(symbol_spec_catalog.__file__).name in message   # どの名乗りを直すか


# --- 2. 列挙していないこと（回帰ガード）--------------------------------------------


def test_the_catalog_does_not_enumerate_the_ledger(monkeypatch, tmp_path):
    """台帳に別の ref を足しても、カタログが提供するプロファイルは増えない。

    台帳には sim で走らない ref（日足・ティック由来）も居る。カタログが台帳を列挙すると
    走らない系列がセレクタへ現れる。ここが提供する ref はカタログ自身が名乗ったものだけである。

    符号化を改めた理由（依頼者承認 2026-09-25・ISSUE-511 段階 8-D-3）: 以前ここは命題を
    「提供 ref がちょうど 1 件」（``== [_REF]``）で符号化していた。件数が 1 だったのは
    **偶然**であり、命題の本体ではない。カタログが 2 本目（気配幅つき系列）を名乗ると、
    命題は真のままなのに符号化だけが偽になる。よって「足す前と後で提供内容が変わらないこと」
    ＝**列挙していないことそのもの**を測る形へ改めた。命題は保存されており、検出力も保たれる
    （カタログを台帳の列挙へ差し替える変異で落ちることを実測した）。
    """
    # Arrange: 足す前の提供内容を先に測る（比べる相手を検定内で作り、件数を焼き込まない）
    offered_before = [p.dataset for p in _datasets(_reimport_catalog())]
    _declare(
        monkeypatch,
        _UNRUNNABLE_REF,
        DatasetDescriptor(path=tmp_path / f"{_UNRUNNABLE_REF}_m1.csv", symbol="TSLA"),
    )

    # Act
    offered_after = [p.dataset for p in _datasets(_reimport_catalog())]

    # Assert
    assert offered_after == offered_before
    assert _UNRUNNABLE_REF not in offered_after
    assert _UNRUNNABLE_REF in whitelist()              # 空振り防止（足した ref は台帳に居る）
    assert REGISTRY[_UNRUNNABLE_REF].symbol == "TSLA"   # 空振り防止（足した ref は台帳に居る）


def test_the_ledger_holds_more_refs_than_the_catalog_offers(monkeypatch):
    """上の対照が空虚でないこと: 台帳は実際にカタログより多くの ref を持っている。

    台帳が 1 ref しか持たない状態になれば「列挙していない」は何も測らない恒真式へ退化する。
    数え方: 台帳の ref 数（`whitelist()` のキー数）と、カタログが提供するプロファイルの数を
    同一時点で数えて比べる。値は焼き込まない。
    """
    # Act
    offered = _datasets(_reimport_catalog())

    # Assert
    assert len(whitelist()) > len(offered)


# --- 3. 台帳の解決の費用（回帰ガード）----------------------------------------------


def test_resolving_the_data_path_from_the_ledger_opens_no_file(monkeypatch):
    """台帳からのパス解決はファイルを 1 つも開かず、1 バイトも読まない（結線が読取を増やさない前提）。"""
    # Arrange
    reads = spy_file_reads(monkeypatch)

    # Act
    resolved = whitelist()[_REF]

    # Assert
    assert len(reads.opened) == 0
    assert reads.delivered() == 0        # 開かずに読む経路（既に開いた口の使い回し）も塞ぐ
    assert resolved == Path(symbol_spec_catalog._JP225_DATA_CSV)   # 空振り防止


# --- 4. 計算量（CX）: 開いたファイルはすべて使われた継ぎ目に帰属する -----------------


def _measure(monkeypatch, tmp_path, rows: int) -> dict:
    """``rows`` 行の実体 1 つを台帳が宣言した状態で `datasets()` を 1 回呼び、発行と使用を数える。

    数えるのは開いた口だけではない（8-D-4）: `spy_file_reads` は開いた口に加えて**読取の
    発行・配られた量・位置付け**を実体ごとに数える。宣言した実体の分だけを問えるようにして
    あるのは、同時に読まれる他の実体（2 本目の系列・銘柄仕様スナップショット）の量に
    埋もれさせないためである。
    """
    declared = write_header(tmp_path, f"scale_{rows}.csv", MD9, body_rows(MD9, rows))
    _declare(monkeypatch, _REF, _relocated(declared))
    module = _reimport_catalog()          # 読み直しは spy を仕掛ける前に済ませる

    header_reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    range_reads = spy(monkeypatch, module, "_csv_date_range")
    snapshot_reads = spy(monkeypatch, module, "load_snapshot")
    reads = spy_file_reads(monkeypatch)

    profiles = _datasets(module)
    return {
        "header": len(header_reads),
        "range": len(range_reads),
        "snapshot": len(snapshot_reads),
        "opened": len(reads.opened),
        "used": len(profiles),
        # 配られた量（全体 / 宣言した実体の分）・位置付けの発行・実体の大きさ。
        "delivered": reads.delivered(),
        "delivered_declared": reads.delivered(declared),
        "seeks_declared": reads.seek_count(declared),
        "size_declared": Path(declared).stat().st_size,
        # スナップショットの「使用」はプロファイル数ではなく**相異なる (サーバ, 銘柄) の数**
        # である（依頼者承認 2026-09-25・ISSUE-511 段階 8-D-3）。複数の系列が同じ供給元を
        # 指すため、プロファイル数を分母にすると「同じファイルを 2 回読む」ことを要求して
        # しまう。読んだ組の集合の大きさで数える（回数は焼き込まない）。
        "snapshot_entities": len(set(snapshot_reads)),
        "data_path": profiles[0].data_path,
        "declared": declared,
    }


def test_the_file_opens_do_not_grow_with_the_ledger_or_the_data(monkeypatch, tmp_path):
    """`datasets()` 1 回あたりの読取が、台帳経由にしてもデータ行数でも増えない。

    台帳のパス解決がファイルを開けば「開いた総数 − 3 継ぎ目の発行の和」が正になって落ちる。
    各継ぎ目は 発行 − **相異なる実体の数** = 0 である: データ側（ヘッダ読取・範囲読取）は
    プロファイルの数（系列ごとに別の CSV）、スナップショット側は読んだ ``(サーバ, 銘柄)`` の
    相異なる数（複数系列が同じ供給元を指すため 1）。**回数そのものは焼き込まない**。

    末尾の「開いた数が規模 2 点で等しい」には検出力が無い（8-D-4 で実測・行は残す）: それは
    上の各継ぎ目の等式から論理的に含意される（使用 = 1 → 3 継ぎ目は各 1 → 開いた数 = 3）ため、
    新しい情報を 1 ビットも足さない。実測 2026-09-25（本作業ツリー・HEAD 14fc9a13）:
    当該行を撤去しても 20 passed のまま挙動不変だった。規模で増えてはならない量は開いた数では
    なく**配られた量**であり、検出力のある規模 2 点の表明は
    test_the_read_volume_does_not_grow_with_the_data が持つ（行を消すのではなく、力のある
    表明を足して穴を塞ぐ）。
    """
    # Act
    small = _measure(monkeypatch, tmp_path, 5)
    monkeypatch.undo()
    large = _measure(monkeypatch, tmp_path, 5_000)
    monkeypatch.undo()

    # Assert
    for measured in (small, large):
        assert measured["data_path"] == measured["declared"]   # 空振り防止（測った実体である）
        assert measured["header"] - measured["used"] == 0
        assert measured["range"] - measured["used"] == 0
        assert measured["snapshot"] >= 1            # 空振り防止（実際に読んでいる）
        assert measured["snapshot"] - measured["snapshot_entities"] == 0
        # 開いたファイルはすべて、出力に使われた 3 継ぎ目に帰属する（台帳の解決は開かない）
        seams = measured["header"] + measured["range"] + measured["snapshot"]
        assert measured["opened"] - seams == 0
    assert large["opened"] == small["opened"]   # 行数 1,000 倍でも開く数は増えない


def test_the_read_volume_does_not_grow_with_the_data(monkeypatch, tmp_path):
    """`datasets()` が実体から**配らせる量**が、データ行数で増えない（末尾は後読みで足りる）。

    なぜ開いた回数では足りないか（実測 2026-09-25・本作業ツリー・HEAD 14fc9a13）: 範囲読取の
    後読み（終端から定数窓だけ読む 3 行）を先頭からの全読みへ退化させても、開いた数は 3 のまま・
    出力の日付トークンも 1 ビットも変わらないため、上の検定を含む本ファイルと
    `simulator/tests/unit/test_symbol_spec_catalog_spread_axis.py` は **20 passed で素通しした**。
    _csv_date_range の宣言（「全走査しない・460 万行でも定数コスト」）を機械で確かめる検定は
    リポジトリ内に 0 件だった。ここがその 1 件目である。**時間は表明しない**（マシン負荷で
    揺れる閾値は緩んで浪費を通す）——数えるのは呼び手へ配られた量である。

    規模 2 点をどう選ぶか: どちらも**後読みの窓より大きい実体**にする。小さい方が窓に収まると
    後読みは実体の全部を返し、等号は「実体の大きさ」を測ってしまって全読みへの退化と区別
    できない。窓の大きさ（実装の定数）を書き写さないため、窓に収まっていないことは
    「配られた量 < 実体の大きさ」の表明そのもので確かめる。規模は 2 桁変える（500 行 / 50,000 行）。
    """
    # Act
    small = _measure(monkeypatch, tmp_path, 500)
    monkeypatch.undo()
    large = _measure(monkeypatch, tmp_path, 50_000)
    monkeypatch.undo()

    # Assert
    for measured in (small, large):
        assert measured["data_path"] == measured["declared"]   # 空振り防止（測った実体である）
        assert measured["delivered_declared"] > 0              # 生存確認（現に読んでいる）
        # 実体を走査していない＝配られた量が実体より小さい（＝後読みの窓に収まっていない）
        assert measured["delivered_declared"] < measured["size_declared"]
    assert large["size_declared"] > small["size_declared"]     # 空振り防止（2 点は別の規模）
    assert large["delivered_declared"] == small["delivered_declared"]
    assert large["delivered"] == small["delivered"]            # 他の実体を含めた総量も増えない
    assert large["seeks_declared"] == small["seeks_declared"]  # 行ごとに位置付け直さない
