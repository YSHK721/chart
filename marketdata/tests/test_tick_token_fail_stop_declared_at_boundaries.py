"""台帳の記入漏れの Fail-Stop が、**包括的な握りの網に掛からない**ことを走査で強制する。

ISSUE-512 段階 1。`TickTokenMissing` は「tick=True なのに tick_token が未記入」のときだけ
台帳が送出する専用型であり、その契約は「握ってはならない」である
（marketdata/dataset_registry.py の同型 docstring）。

ところが本番のティック読取経路には、素材（tick parquet）の torn-read / IO 失敗を握って
継続するための包括的な except が複数在る。記入漏れはその網に掛かると WARNING 1 行と
歯抜けへ化ける。**落ちないぶん出力は形式上正しく、状態検証では原理的に検出できない**
（ISSUE-450 と同型）。

なぜ走査で強制するか（既存の前例と同じ理由）:
    marketdata/tests/test_tick_tree_token_single_source.py の
    test_every_tick_read_names_the_tree_it_reads は「ティック読取は必ず symbol= で木を
    名指しする」を AST で強制している。**枝名を名指しする**規約には機械強制が在り、
    **Fail-Stop を握り潰さない**規約には無かった——この非対称が本検定の穴である。
    現行 4 境界は個別の振る舞い検定で守られているが、それは列挙であって構造ではない。
    5 つ目の境界が増えたとき、宣言を書き忘れても何も落ちない。

固定する不変量:
    ティック読取の窓口（下記 `_TICK_ENTRIES`）を呼ぶ try のうち、`TickTokenMissing` を
    捕らえてしまう except を**先に**持つものが、リポジトリ全体で 0 件であること
    （`_DEFERRED` に理由つきで登録したものを除く）。

2 つの仕組みの役割（重複させない）:
    **全体走査**（`scan_repository`）が本番の検定ただ 1 つを担う。走査したファイルのうち
    違反するものの集合が `_DEFERRED` に収まることを見る。台帳のファイルもこの走査に
    含まれるため、「台帳ファイルだけを個別に見る検定」は置かない（同一概念に 2 つの
    仕組みを作らない）。
    **台帳**（`_LEDGER`）は既知の 4 境界を名指しする**錨**であり、役割は空振り防止に
    限る。窓口の名前が変わって走査が静かに 0 件になる事故を、既知のファイルに境界が
    1 つ以上在ることで落とす。
    **接ぎ目**は `test_the_repository_walk_reaches_every_ledger_file` が持つ。全体走査の
    除外規則を広げすぎて台帳のファイルを走査対象から落とすと、全体走査は既知の境界を
    見ないまま緑になる。錨と全体走査はこの 1 件で結び付いている。

走査範囲（除外の根拠は実測であり、推測ではない）:
    テストと `prototype_*` を除く py。実測（2026-09-12）: 除外の有無で**違反ファイル
    集合は変わらない**。除外なし 1,683 件走査 → 違反 1 件、除外あり 785 件走査 →
    違反 1 件。いずれも同じ 1 件（`_DEFERRED` の唯一の要素）である。つまりこの除外は
    何も隠していない。除外する理由は別にあり、テストは偽物（fake / double）を包む
    ために包括的な except を正当に書く場所であり、試作は本番と同型のコードが意図的に
    複製されている場所である（後者はプロジェクト規約: tools/codescan_scope.txt も
    `- prototype_*/**` を持つ）。
    走査範囲の台帳（tools/codescan_scope.txt）を読む形は採らない。あちらは重複測定の
    ための範囲であり所有者が違う（テストを含む）。あちらの変更で本検定の射程が黙って
    変わる結合を作らない。

「捕らえてしまう except」は手で列挙しない:
    `TickTokenMissing` の継承鎖（`_CATCHES_FAIL_STOP`）から導く。手書きの型名表を持つと、
    型の親を変えたとき（例: 包括的な except に掛からない階層へ移す判断）に検定だけが
    古い前提のまま緑になる。裸の except も同じ扱いにする（何でも握るため）。

繰延（`_DEFERRED`）:
    いま直さないと決めた面を、**理由と解消条件つきで 1 件 1 行**明示する。パターンによる
    暗黙のスキップや空の除外は置かない（増えたら diff で誰でも気づく形にする）。
    現在の唯一の要素は indigators/indicator_ui/api/usecase/serve_candles.py の
    serve_forming_bar（try は 170 行）である。
    繰延は片側だけでは陳腐化する。載せたものが**実際にまだ違反していること**も検定する
    （`test_every_deferred_entry_is_still_violating`）。解消済みの要素が残ると「除外した
    つもりが実は何も除外していない」状態になる。双方向に見る考え方は既存の
    marketdata/tests/test_module_dependency_declarations.py の
    test_declared_dependency_set_has_no_stale_entries と同じである。

本走査が取り逃す形（実測値つき・宣言ではなく限界の記録）:
    窓口を **関数ごと別名で import** して素の名前で呼ぶ形
    （from ... import apply_forming_bar as apply → apply(...)）は、`_TICK_ENTRIES` の
    名前と一致しないため見えない。実測（同日・リポジトリ全体）: ティック窓口の別名
    import は 16 件あるが、**すべてモジュールの別名**（import forming_bar as
    forming_bar_mod 等）であり、属性呼び出しとして正しく数えられる。関数を別名にした
    箇所は 0 件。
    走査対象から外した場所（テスト・試作）に境界ができた場合も見えない。除外が判定を
    変えないことは上記のとおり実測済みだが、将来その場所へ本番相当の面が移ると見えなく
    なる（`test_the_repository_walk_skips_the_places_that_are_not_production` が、
    どこを外しているかを 1 件ずつ可視にしている）。実測 2026-09-12: テスト除外で落ちる py の
    うち `test_` 始まり・conftest.py・__init__.py 以外は 38 件（_fake_ports.py・verify_*.py・
    reconcile*.py 等の検証ハーネス）で、そのいずれも違反していない（除外なしで 1,683 件を
    走査した結果が違反 1 件であることから従う）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

from marketdata.dataset_registry import TickTokenMissing

#: リポジトリ根（本ファイル: marketdata/tests/ → parents[2]）。
_ROOT = Path(__file__).resolve().parents[2]

#: 走査対象の台帳（1 行 1 ファイル）。ティック読取を包括的な except で包む本番の面。
#: 加除はここだけで行う（検定本体は台帳を読むだけで、ファイル名を知らない）。
_LEDGER = {
    "forming_bar": (
        _ROOT / "indigators" / "indicator_ui" / "api" / "adapter" / "compute"
        / "forming_bar.py"
    ),
    "live_tick_tails_controller": (
        _ROOT / "indigators" / "indicator_ui" / "api" / "adapter" / "controller"
        / "live_tick_tails_controller.py"
    ),
    "indicator_ui_compute_gateway": (
        _ROOT / "dashboard_ui" / "adapter" / "gateway" / "indicator_ui_compute_gateway.py"
    ),
}

#: ティック木の枝名を解決しうる窓口。これを try の中で呼ぶ面が本検定の対象である。
#: 名前で見るのは、呼び出し側が素の名前（forming_bar(...)）でも属性
#: （mod.forming_bar(...)）でも同じ窓口を指すためである。
_TICK_ENTRIES = frozenset(
    {"forming_bar", "apply_forming_bar", "closed_gap_bars", "window_with_forming"}
)

class Deferral(NamedTuple):
    """繰延の記録（空の除外・暗黙のスキップを作らないための必須 2 項目）。

    Attributes:
        reason: **なぜ**いま直さないか。
        resolution: **何が起きたら**繰延を解くか。
    """

    reason: str
    resolution: str


#: 全体走査で違反になるが、いま直さない面の明示リスト（理由と解消条件つき）。
#: 鍵はリポジトリ根からの相対パス（POSIX）。増えたら diff で誰でも気づく形にしてある。
#: 空の除外・パターンによる暗黙のスキップは置かない（1 件 1 行で名指しする）。
_DEFERRED = {
    "indigators/indicator_ui/api/usecase/serve_candles.py": Deferral(
        reason=(
            "落とし先が error_type=\"internal\" ＝**可視の停止**であり、沈黙の歯抜けに"
            "ならない。本検定が防ぎたい「落ちないぶん出力は形式上正しく見える」状態には"
            "当たらないため、緊急度が台帳の 4 境界と異なる（ISSUE-512 段階 1 のスコープ外）"
        ),
        resolution=(
            "可視の停止のままで良いかの判断が付いた時点。直すなら当該 try の包括的な except"
            " より前へ Fail-Stop の素通しを置き、本リストから外して `_LEDGER` へ移す"
        ),
    ),
}

#: Fail-Stop を素通しする唯一の書き方（この型を名指しする except）。
_FAIL_STOP = TickTokenMissing.__name__

#: この名前を名指しする except は `TickTokenMissing` を捕らえる。継承鎖から導く
#: （手書きの表を持たない＝型の親を変えれば検定も自動で追随する）。
_CATCHES_FAIL_STOP = frozenset(
    base.__name__ for base in TickTokenMissing.__mro__ if base is not object
)


class Boundary(NamedTuple):
    """ティック読取を包む try 1 つぶんの所見。

    Attributes:
        lineno: try の行。
        entries: その try の本体が呼ぶティック読取の窓口（名前の昇順）。
        swallows: Fail-Stop を握り潰す except の行。素通ししているなら None。
    """

    lineno: int
    entries: tuple
    swallows: "int | None"


def _called_names(nodes) -> "frozenset[str]":
    """`nodes` の内側で呼ばれている関数名を、素の名前と属性の両形で集める。

    両形を見るのは、同じ窓口が呼び出し側によって素の名前（forming_bar(...)）にも
    属性（mod.forming_bar(...)）にもなるためである。

    数える範囲は node の内側**全部**である。lambda の中（gap_bars=lambda last: ...）も、
    入れ子の try の中も数える。いずれもその try の中で実行される＝同じ網に掛かるため、
    外側へ数え上げる向きが安全側である（見落とすより過検出のほうがまだ直せる）。
    """
    names: "set[str]" = set()
    for node in nodes:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return frozenset(names)


def _handler_names(handler: ast.ExceptHandler) -> "frozenset[str]":
    """`except X:` / `except (X, Y):` が名指しする型名。裸の except は空集合。"""
    node = handler.type
    if node is None:
        return frozenset()
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    return frozenset(
        part.id if isinstance(part, ast.Name) else part.attr
        for part in parts
        if isinstance(part, (ast.Name, ast.Attribute))
    )


def _swallow_line(handlers) -> "int | None":
    """Fail-Stop を握り潰す except の行。素通ししているなら None。

    handler は**書かれた順**に評価される（Python 仕様）。よって順に見て、
      - 先に Fail-Stop を名指しする except が在れば素通し（None）
      - それより前に Fail-Stop を捕らえる except が在れば、その行が握り潰しの場所
      - どれも掛からなければ貫通する（None）
    裸の except（名指し 0 件）は何でも握るため握り潰しに数える。
    """
    for handler in handlers:
        names = _handler_names(handler)
        if _FAIL_STOP in names:
            return None
        if (not names) or (names & _CATCHES_FAIL_STOP):
            return handler.lineno
    return None


def tick_read_boundaries(source: str) -> "list[Boundary]":
    """ティック読取の窓口を呼ぶ try を全部返す（握り潰しているかの所見つき）。"""
    return [
        Boundary(node.lineno, tuple(sorted(entries)), _swallow_line(node.handlers))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Try)
        for entries in [_called_names(node.body) & _TICK_ENTRIES]
        if entries
    ]


def swallowing_boundaries(source: str) -> "list[Boundary]":
    """そのうち、Fail-Stop を握り潰しているものだけ。"""
    return [b for b in tick_read_boundaries(source) if b.swallows is not None]


# --------------------------------------------------------------------------- #
# リポジトリ全体の走査（未知の境界を数え上げる側）
# --------------------------------------------------------------------------- #
#: 走査から外すディレクトリ名。理由は module docstring の「走査範囲」に記す。
_WALK_EXCLUDE = frozenset({
    ".git", ".claude", ".pytest_cache", ".playwright-mcp", "__pycache__",
    ".venv", "venv", "node_modules", "lightweight-charts-python-main",
    "data", "design", ".doc",
})


class Scan(NamedTuple):
    """1 回の全体走査の結果。

    Attributes:
        files: 解析できたファイル（リポジトリ根からの相対 POSIX・昇順）。
        violations: 相対 POSIX → 握り潰している境界の列（違反が在るファイルだけ）。
        unreadable: 走査対象だが読めなかったファイル（相対 POSIX と理由）。**黙って落とさない**
            ために結果へ持つ。読めないファイルは `files` にも `violations` にも現れないため、
            記録しないと走査が静かに縮む（暗黙のスキップ）。実測 2026-09-12: 0 件。
    """

    files: tuple
    violations: dict
    unreadable: tuple


def scanned_files(root: Path) -> "list[Path]":
    """`root` 配下の走査対象 py を返す（テストと試作は対象外）。"""
    out = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        excluded = (
            bool(set(parts) & _WALK_EXCLUDE)
            or any(part.startswith("prototype_") for part in parts)
            or "tests" in parts
            or path.name.startswith("test_")
        )
        if not excluded:
            out.append(path)
    return out


def scan_repository(root: Path) -> Scan:
    """`root` 配下を 1 回だけ走査し、解析できたファイルと握り潰しの所見を返す。

    ソースの解析は 1 ファイルにつき 1 回である（同じ素材を作り直さない）。読めなかった
    ファイル（構文エラー・非 UTF-8）は **`unreadable` に記録して返す**。黙って捨てると
    走査が静かに縮み、そこに境界があっても「違反 0」で緑になる。
    """
    files: "list[str]" = []
    violations: "dict[str, list[Boundary]]" = {}
    unreadable: "list[tuple[str, str]]" = []
    for path in scanned_files(root):
        name = path.relative_to(root).as_posix()
        try:
            found = swallowing_boundaries(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as error:
            unreadable.append((name, type(error).__name__))
            continue
        files.append(name)
        if found:
            violations[name] = found
    return Scan(tuple(files), violations, tuple(unreadable))


# --------------------------------------------------------------------------- #
# 1. 検出力の自己検査（節を消した合成ソースで落ちること）
# --------------------------------------------------------------------------- #
#: 握り潰しの全形態。1 形態でも取り逃せば検出の穴である（本番ツリーへは触れない）。
_SWALLOWING_FORMS = {
    "節を消した": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except Exception as exc:\n"
        "        return df\n"
    ),
    "順序が逆（網が先）": (
        "def _as_of_display(self, frame, mod, ref, tf, cutoff):\n"
        "    try:\n"
        "        return mod.apply_forming_bar(frame, ref, tf, cutoff)\n"
        "    except Exception:\n"
        "        return frame\n"
        "    except TickTokenMissing:\n"
        "        raise\n"
    ),
    "属性形の窓口": (
        "def _as_of_display(self, frame, mod, ref, tf, cutoff):\n"
        "    try:\n"
        "        forming = mod.forming_bar(ref, tf, cutoff)\n"
        "    except Exception:\n"
        "        return frame\n"
    ),
    "lambda の中の窓口": (
        "def handle_live_tick_tails(query, ticks):\n"
        "    try:\n"
        "        df = window_with_forming(\n"
        "            df, bar, gap_bars=lambda last: closed_gap_bars(ref, tf, last, 0)\n"
        "        )\n"
        "    except Exception:\n"
        "        logger.exception('落とす')\n"
    ),
    "裸の except": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except:\n"
        "        return df\n"
    ),
    "ValueError の網": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except ValueError:\n"
        "        return df\n"
    ),
    "組で名指しした網": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except (TypeError, ValueError):\n"
        "        return df\n"
    ),
}


@pytest.mark.parametrize("form", sorted(_SWALLOWING_FORMS))
def test_the_scan_sees_every_form_of_swallowing(form: str) -> None:
    """握り潰しの全形態を検出できること（この検定が実際に欠陥を落とすことの実証）。

    とくに「順序が逆」は重要である。Fail-Stop を名指しする except は在るのに網より
    後ろに書かれており、到達しない。名指しの有無だけを見る検出器はこれを素通りする。
    """
    # Arrange / Act
    found = swallowing_boundaries(_SWALLOWING_FORMS[form])

    # Assert
    assert len(found) == 1, f"握り潰しを検出できていない: {form}"
    assert found[0].swallows is not None


#: 握り潰しではない形。誤検出すると正常な面を直させることになる。
_CLEAN_FORMS = {
    "守りが在る（現状の形）": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except TickTokenMissing:\n"
        "        raise\n"
        "    except Exception as exc:\n"
        "        return df\n"
    ),
    "狭い網だけ（Fail-Stop は貫通する）": (
        "def apply_forming_bar(df, ref, tf, now_unix):\n"
        "    try:\n"
        "        bar = forming_bar(ref, tf, now_unix)\n"
        "    except OSError:\n"
        "        return df\n"
    ),
    "窓口を呼ばない try": (
        "def _load_window(port, ref, tf):\n"
        "    try:\n"
        "        df = port.load_dataframe(ref, tf)\n"
        "    except Exception:\n"
        "        return None\n"
    ),
    "枝名を引数で受け取る読取": (
        "def _tick_source_fingerprint(start_unix, end_unix, tree):\n"
        "    try:\n"
        "        return tuple(day_parquet_files(start_unix, end_unix, symbol=tree))\n"
        "    except Exception:\n"
        "        return None\n"
    ),
}


@pytest.mark.parametrize("form", sorted(_CLEAN_FORMS))
def test_the_scan_does_not_flag_the_forms_that_are_not_swallowing(form: str) -> None:
    """誤検出しないこと。守りが在る形・狭い網・窓口を呼ばない try は違反ではない。

    「枝名を引数で受け取る読取」を含めるのは、そこが**構造で解いた**面だからである
    （解決を網の外へ出したので、その try に宣言は要らない）。これを違反にすると、
    より良い形を検定が禁じることになる。
    """
    # Arrange / Act
    found = swallowing_boundaries(_CLEAN_FORMS[form])

    # Assert
    assert found == [], f"握り潰しでない形を違反と判定した: {form} — {found}"


def test_the_catch_set_is_derived_from_the_type_hierarchy() -> None:
    """握り潰す型名の集合が、手書きの表ではなく継承鎖から導かれていること。

    手書きにすると、型の親を変えたとき検定だけが古い前提のまま緑になる。
    """
    # Arrange / Act / Assert
    assert _FAIL_STOP in _CATCHES_FAIL_STOP
    assert {base.__name__ for base in TickTokenMissing.__mro__} - {"object"} == (
        _CATCHES_FAIL_STOP
    )


# --------------------------------------------------------------------------- #
# 2. 空振り防止（走査が本番の境界へ到達していること）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(_LEDGER))
def test_the_scan_reaches_a_tick_read_boundary_in_every_ledger_file(name: str) -> None:
    """台帳の各ファイルに、走査が見つけた境界が 1 つ以上在ること。

    窓口の名前が変わると走査は静かに 0 件になり、本番検定は「違反 0」で緑のまま
    素通りする。下限で押さえて空振りを落とす（件数そのものは固定しない——境界が
    増減すること自体は欠陥ではない）。
    """
    # Arrange
    source = _LEDGER[name].read_text(encoding="utf-8")

    # Act
    found = tick_read_boundaries(source)

    # Assert
    assert found, (
        f"{_LEDGER[name].name} にティック読取を包む try が 1 つも見つからない。"
        f" 窓口の名前（{sorted(_TICK_ENTRIES)}）が変わった可能性がある＝走査が空振りしている。"
    )


def test_the_ledger_points_at_files_that_exist() -> None:
    """台帳の各行が実在のファイルを指すこと（移設で走査対象が消えていないこと）。"""
    # Arrange / Act
    missing = sorted(name for name, path in _LEDGER.items() if not path.is_file())

    # Assert
    assert missing == [], f"台帳が実在しないファイルを指している: {missing}"


# --------------------------------------------------------------------------- #
# 3. 全体走査の検出力と誤検出（一時ディレクトリ・本番ツリーへは触れない）
# --------------------------------------------------------------------------- #
#: 一時ディレクトリへ置く合成ソース。全体走査が「拾えること」と「拾わないこと」を分ける。
_WALK_SWALLOWING = (
    "def apply_forming_bar(df, ref, tf, now_unix):\n"
    "    try:\n"
    "        bar = forming_bar(ref, tf, now_unix)\n"
    "    except Exception as exc:\n"
    "        return df\n"
)
_WALK_GUARDED = (
    "def apply_forming_bar(df, ref, tf, now_unix):\n"
    "    try:\n"
    "        bar = forming_bar(ref, tf, now_unix)\n"
    "    except TickTokenMissing:\n"
    "        raise\n"
    "    except Exception as exc:\n"
    "        return df\n"
)


def files_outside_the_deferred_list(scan: Scan) -> "list[str]":
    """判定基準そのもの: 違反ファイル集合から繰延リストを引いた残り。

    残りが空であることが本検定の不変量である。繰延リストに載っていないファイルが
    Fail-Stop を握り潰していたら、その差分がここに出る。
    """
    return sorted(set(scan.violations) - set(_DEFERRED))


def test_the_repository_walk_catches_a_swallowing_file_placed_under_it(tmp_path) -> None:
    """全体走査が、置かれた握り潰しファイルを拾って判定基準を破ること（検出力）。

    台帳に無い**新規ファイル**へ境界ができた場合を模す。これが落ちなければ、本検定は
    「未知の境界を数え上げる」という役目を果たしていない。
    """
    # Arrange
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "new_boundary.py").write_text(_WALK_SWALLOWING, encoding="utf-8")

    # Act
    scan = scan_repository(tmp_path)

    # Assert
    assert files_outside_the_deferred_list(scan) == ["pkg/new_boundary.py"]


def test_the_repository_walk_does_not_flag_a_guarded_file_placed_under_it(tmp_path) -> None:
    """守りが在るファイルは、全体走査でも違反にならないこと（誤検出しない）。"""
    # Arrange
    (tmp_path / "guarded.py").write_text(_WALK_GUARDED, encoding="utf-8")

    # Act
    scan = scan_repository(tmp_path)

    # Assert
    assert files_outside_the_deferred_list(scan) == []
    assert scan.files == ("guarded.py",)


#: 走査対象から外れる置き場所。除外が効いていることを 1 件ずつ確かめる。
_EXCLUDED_PLACES = {
    "tests ディレクトリの中": "tests/helper.py",
    "test_ で始まるファイル": "test_thing.py",
    "試作ディレクトリの中": "prototype_x/proto.py",
}


@pytest.mark.parametrize("place", sorted(_EXCLUDED_PLACES))
def test_the_repository_walk_skips_the_places_that_are_not_production(
    place: str, tmp_path
) -> None:
    """テストと試作は走査対象外であること。

    除外の根拠は「実測して判定が変わらないこと」である（module docstring の走査範囲）。
    ここで固定するのは**除外が効いていること**であり、除外してよい理由ではない。
    """
    # Arrange
    target = tmp_path / _EXCLUDED_PLACES[place]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_WALK_SWALLOWING, encoding="utf-8")

    # Act
    scan = scan_repository(tmp_path)

    # Assert
    assert scan.files == (), f"{place} を走査対象にしている: {list(scan.files)}"


def test_the_repository_walk_issues_one_parse_per_scanned_file(monkeypatch, tmp_path) -> None:
    """計算量: ソースの解析発行数が、走査ファイル数と一致すること（2 点で固定）。

    測るのは時間ではなく回数である。固定するのは「解析を 1 ファイルにつき 1 回しか
    発行しない」＝**捨てる解析が無いこと**であり、回数そのものを期待値へ焼き込まない
    （ファイル数が増減すること自体は欠陥ではない。両者が一致し続けることを見る）。
    """
    # Arrange — 1 本と 3 本の 2 点。中身は走査対象になる形（解析が必ず発行される）。
    issued = []
    real_parse = ast.parse
    monkeypatch.setattr(
        ast, "parse", lambda src, *a, **k: (issued.append(1), real_parse(src, *a, **k))[1]
    )
    counted = {}
    for total in (1, 3):
        root = tmp_path / f"root{total}"
        root.mkdir()
        for index in range(total):
            (root / f"module{index}.py").write_text(_WALK_GUARDED, encoding="utf-8")

        # Act
        issued.clear()
        scan = scan_repository(root)
        counted[total] = (len(scan.files), len(issued))

    # Assert — 2 点とも「走査ファイル数 == 解析発行数」。
    assert counted[1] == (1, 1), f"1 本のとき {counted[1]}（走査数, 発行数）"
    assert counted[3] == (3, 3), f"3 本のとき {counted[3]}（走査数, 発行数）"


# --------------------------------------------------------------------------- #
# 4. 繰延リストの健全性（空の除外・陳腐化した除外を作らない）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def repository_scan() -> Scan:
    """本番ツリーの全体走査（module 内で 1 回だけ発行し、以降は使い回す）。"""
    return scan_repository(_ROOT)


@pytest.mark.parametrize("target", sorted(_DEFERRED))
def test_every_deferred_entry_records_a_reason_and_a_resolution(target: str) -> None:
    """繰延の各要素が、理由と解消条件を両方持つこと（空の除外を作らない）。"""
    # Arrange / Act
    entry = _DEFERRED[target]

    # Assert
    assert entry.reason.strip(), f"{target} の繰延に理由が無い"
    assert entry.resolution.strip(), f"{target} の繰延に解消条件が無い"


@pytest.mark.parametrize("target", sorted(_DEFERRED))
def test_every_deferred_entry_is_still_violating(target: str, repository_scan: Scan) -> None:
    """繰延の各要素が、**実際にまだ違反していること**（陳腐化した除外を残さない）。

    解消済みの要素が残り続けると「除外したつもりが実は何も除外していない」状態になり、
    リストが事実と食い違う。双方向に見る考え方は既存の
    marketdata/tests/test_module_dependency_declarations.py の
    test_declared_dependency_set_has_no_stale_entries と同じである。
    """
    # Arrange / Act
    found = repository_scan.violations.get(target, [])

    # Assert
    assert found, (
        f"{target} は繰延リストに載っていますが、もう違反していません。"
        " 繰延の記録を撤去してください（解消条件: "
        f"{_DEFERRED[target].resolution}）。"
    )


@pytest.mark.parametrize("target", sorted(_DEFERRED))
def test_every_deferred_entry_is_inside_the_walk(target: str, repository_scan: Scan) -> None:
    """繰延の各要素が走査対象に入っていること（除外規則の陰に隠れていないこと）。

    走査対象の外に在るものを繰延リストへ載せても、何も繰延していない（上の陳腐化検定が
    「違反していない」で落ちるだけになり、理由が取り違えられる）。
    """
    # Arrange / Act / Assert
    assert target in repository_scan.files, (
        f"{target} は繰延リストに載っていますが走査対象に入っていません"
    )


# --------------------------------------------------------------------------- #
# 5. 本番の検定（全体走査ただ 1 つ。台帳ファイルもこの走査に含まれる）
# --------------------------------------------------------------------------- #
def test_no_file_outside_the_deferred_list_swallows_the_fail_stop(
    repository_scan: Scan,
) -> None:
    """ティック読取を包む網は、必ず Fail-Stop を先に素通しすること（全ファイル）。

    落ちた場合の直し方: 当該 try の包括的な except の**前**へ
    `except TickTokenMissing: raise` を置く。`except ValueError: raise` にしてはならない
    ——注入バーの破損（time 欄が非数値）も ValueError であり、そこまで貫通すると
    1 つの素材破損で応答全体が落ちる。直さない判断をするなら `_DEFERRED` へ理由と
    解消条件を添えて登録する（登録すれば上の陳腐化検定が以後その事実を見張る）。
    """
    # Arrange / Act
    over = files_outside_the_deferred_list(repository_scan)

    # Assert
    assert over == [], "繰延リストに無いファイルが記入漏れの Fail-Stop を握り潰しています: " + (
        "; ".join(
            f"{name} の try {b.lineno} 行（{', '.join(b.entries)} を呼ぶ）を"
            f" {b.swallows} 行の except が握っている"
            for name in over
            for b in repository_scan.violations[name]
        )
    )


def test_the_repository_walk_reaches_every_ledger_file(repository_scan: Scan) -> None:
    """接ぎ目: 全体走査が台帳の 3 ファイルへ到達していること。

    これが切れると、全体走査は既知の 4 境界を**見ないまま**「違反 0」で緑になる
    （走査の除外規則を広げすぎた場合がこれに当たる）。台帳は既知の境界を名指しする
    錨であり、この検定が錨と全体走査を結び付ける。
    """
    # Arrange
    expected = sorted(path.relative_to(_ROOT).as_posix() for path in _LEDGER.values())

    # Act
    missing = sorted(name for name in expected if name not in repository_scan.files)

    # Assert
    assert missing == [], f"全体走査が台帳のファイルへ到達していない: {missing}"


def test_the_repository_walk_reads_every_file_it_scans(repository_scan: Scan) -> None:
    """暗黙のスキップを作らない: 走査対象に読めなかったファイルが無いこと。

    読めないファイルは解析されず、`files` にも `violations` にも現れない。黙って捨てると
    そこに境界があっても「違反 0」で緑になる（走査が静かに縮む）。実測 2026-09-12: 0 件。
    落ちた場合は、読めない理由を解消するか、読めないまま走査対象から外す判断を
    `_WALK_EXCLUDE` へ明示的に書く（暗黙に落とさない）。
    """
    # Arrange / Act / Assert
    assert repository_scan.unreadable == (), (
        "走査対象のうち読めなかったファイルがあります: "
        + "; ".join(f"{name}（{reason}）" for name, reason in repository_scan.unreadable)
    )


def test_the_repository_walk_is_not_empty(repository_scan: Scan) -> None:
    """空振り防止: 走査対象が 0 件でないこと。

    件数そのものは固定しない（ファイルの増減は欠陥ではない）。0 でないことと、
    既知の面が入っていることの 2 点で押さえる。
    """
    # Arrange / Act / Assert
    assert repository_scan.files
    assert "marketdata/dataset_registry.py" in repository_scan.files
