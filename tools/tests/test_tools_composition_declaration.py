"""``tools`` パッケージの「ロジックの重複を持たない合成点」宣言を **検定で強制する**（ISSUE-262）。

``tools/__init__.py`` は「各サブモジュールは既存ライブラリ・ツールの合成点として振る舞い、
ロジックの重複を持たない」と宣言している。しかし実際には
  - ``_rollup_timeframes`` が 2 本（一方の docstring が「他方と同規則」と人手同期を宣言）
  - tick tree レイアウト（``/ticks``・``YYYY/MM/DD``・ファイル名）が独自実装
  - 生 tick 列定義が独自実装
が存在し、宣言は施行されていなかった。

本テストは「同じ規則の第 2 定義が tools 配下に無い」ことを、規則ごとに固定する。
規則を tools に置きたくなったら、それは合成点ではなくライブラリの仕事である
（marketdata / simulator へ置き、tools は呼ぶだけにする）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1]


def _sources() -> "list[Path]":
    """``tools`` 配下の本番コード（テスト・パッケージ初期化子を除く）を**再帰的に**列挙する。

    非再帰（``glob``）だと ``tools/measure/**``・``tools/mt5/**`` 等のサブパッケージへ
    潜れば検査を免れる穴になる。実際 ``tools/measure/issue449/probe_forming_long.py`` は
    keep-last 規則を自前で書いていたが、走査が直下だけだったため検出されなかった
    （ISSUE-479 F-7c）。射程は「宣言の対象範囲」と一致していなければ宣言ではない。
    """
    return sorted(
        p for p in _TOOLS.rglob("*.py")
        if p.name != "__init__.py"
        and "tests" not in p.parts
        and "__pycache__" not in p.parts
    )


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


#: keep-last 規則を「自前で書いている」と判定する印。``marketdata.keep_last`` への委譲は
#: これらの語を含まないため、委譲だけが残る形に自然に収束する。
_KEEP_LAST_MARKS = ('keep="last"', "keep='last'", "drop_duplicates(")


def _keep_last_hits(line: str) -> "list[str]":
    """1 行に keep-last 規則の実装が現れるか（コメント行は対象外＝説明を禁じない）。"""
    code = line.strip()
    if code.startswith("#"):
        return []
    return [mark for mark in _KEEP_LAST_MARKS if mark in code]


def _keep_last_offenders_over(files, read=None) -> "list[str]":
    """与えられたファイル群を走査する。**1 ファイルにつき読込 1 回**（免除リストを持たない）。"""
    reader = read or _read_source
    offenders: "list[str]" = []
    for path in files:
        for i, line in enumerate(reader(path).splitlines(), 1):
            for mark in _keep_last_hits(line):
                offenders.append(f"{path.name}:{i}: {mark} → {line.strip()[:80]}")
    return offenders


def _keep_last_offenders(read=None) -> "list[str]":
    """``tools`` 配下の本番コードに keep-last 規則の第 2 実装が無いかを走査する。"""
    return _keep_last_offenders_over(_sources(), read=read)


def _function_bodies(path: Path) -> "dict[str, str]":
    """トップレベル関数名 → 本体ソース（docstring を除く）。"""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    out: "dict[str, str]" = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = [n for n in node.body if not (isinstance(n, ast.Expr)
                                                 and isinstance(n.value, ast.Constant))]
            if not body:
                continue
            src = "\n".join(lines[body[0].lineno - 1: node.end_lineno])
            out[node.name] = src
    return out


def test_rollup_timeframe_rule_is_not_reimplemented_in_tools():
    """ロールアップ対象 tf の規則が tools に第 2 定義として存在しない。

    唯一源は ``marketdata.rollup.rollup_timeframes``。tools 側は委譲だけを持つ。
    """
    offenders = []
    for path in _sources():
        for name, src in _function_bodies(path).items():
            if "TIMEFRAME_RULES" in src and "!=" in src and '"1m"' in src:
                offenders.append(f"{path.name}:{name}")
    assert not offenders, (
        f"ロールアップ対象 tf の規則を tools が再実装しています: {offenders}。"
        " marketdata.rollup.rollup_timeframes への委譲へ置換してください。"
    )


def test_raw_tick_columns_are_not_redefined_in_tools():
    """生ティックの正準列が tools に第 2 定義として存在しない。

    唯一源は ``simulator.tools.ingest_ticks.RAW_COLUMNS``。
    """
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.strip()
            if code.startswith("#"):
                continue
            if "bidPrice" in code and "askPrice" in code and ("=" in code and "[" in code or "(" in code):
                if "RAW_COLUMNS" not in code:
                    offenders.append(f"{path.name}:{i}: {code[:80]}")
    assert not offenders, (
        f"生ティック列を tools が再定義しています:\n  " + "\n  ".join(offenders)
        + "\n  simulator.tools.ingest_ticks.RAW_COLUMNS を import してください。"
    )


def test_tools_declaration_matches_this_test_suite():
    """``tools/__init__.py`` の宣言が、施行されている内容を指している。

    宣言だけを残して施行を持たない状態（今回の再発源）を作らないための固定点。
    """
    text = (_TOOLS / "__init__.py").read_text(encoding="utf-8")
    assert "ロジックの重複を持たない" in text, "宣言文が変わりました。本テストも更新してください。"
    assert "test_tools_composition_declaration" in text, (
        "tools/__init__.py の宣言が、それを強制するテストを指していません。"
        " 宣言と施行を結び付けてください（宣言だけを残さない）。"
    )


# =====================================================================
# keep-last 規則の再実装禁止（ISSUE-479 F-7c・加法のみ）
# =====================================================================

def test_keep_last_rule_is_not_reimplemented_in_tools():
    """「同一キーの最終出現を採る」規則が tools に第 2 定義として存在しない。

    唯一源は marketdata/keep_last.py（依存ゼロの中立核）。tools 側は委譲だけを持つ。
    かつては ``tools/measure/issue449/probe_forming_long.py`` が
    ``drop_duplicates(subset=["date"], keep="last")`` を自前で書いており、
    走査が ``tools`` 直下**非再帰**だったため永久に検出されなかった（ISSUE-479 F-7c）。
    """
    offenders = _keep_last_offenders()
    assert offenders == [], (
        "keep-last 規則を tools が再実装しています:\n  " + "\n  ".join(offenders)
        + "\n  marketdata.keep_last の dedupe_index_keep_last / dedupe_column_keep_last /"
        " keep_last_by_key への委譲へ置換してください。"
    )


@pytest.mark.parametrize(
    "line",
    [
        'df = df.drop_duplicates(subset=["date"], keep="last")',
        "m1 = m1[~m1.index.duplicated(keep='last')]",
        "out = frame.drop_duplicates()",
    ],
    ids=["drop_duplicates_kw", "duplicated_single_quote", "drop_duplicates_bare"],
)
def test_the_keep_last_scan_has_detection_power(line):
    """走査が恒真式に退化していないこと（合成行で検出できる）。"""
    assert _keep_last_hits(line), line


def test_the_keep_last_scan_ignores_comments():
    """コメント内の言及は offender にしない（説明を禁じない）。"""
    assert _keep_last_hits('# keep="last" の規則は marketdata.keep_last へ委譲する') == []


# =====================================================================
# 走査の射程（tools 配下を再帰的に見ていること）
# =====================================================================

def test_the_scan_reaches_into_subpackages():
    """``tools`` 直下だけでなくサブディレクトリの本番コードも走査対象である。

    非再帰だと ``tools/measure/**`` ・``tools/mt5/**`` 等へ潜れば検査を免れる穴になる。
    """
    scanned = _sources()
    nested = [p for p in scanned if p.parent != _TOOLS]
    assert nested, "サブディレクトリの本番コードが 1 件も走査されていません（非再帰に退化）。"


def test_the_scan_excludes_tests_and_package_initialisers():
    scanned = _sources()
    assert [p for p in scanned if "tests" in p.parts] == []
    assert [p for p in scanned if p.name == "__init__.py"] == []
    assert [p for p in scanned if "__pycache__" in p.parts] == []


# =====================================================================
# 計算量検定（Test Spy・発行 − 使用 = 0）
# =====================================================================

def test_every_source_is_read_exactly_once_by_the_keep_last_scan():
    """読込集合 == ``_sources()`` の集合。読み捨ても二度読みも無い（発行 − 使用 = 0）。"""
    reads = []
    _keep_last_offenders(read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1])
    used = _sources()
    assert len(reads) - len(used) == 0
    assert set(reads) == set(used)
    assert len(set(reads)) - len(reads) == 0


def test_the_read_count_is_determined_by_the_file_count_alone(tmp_path):
    """走査対象 4 件 / 8 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
    measured = {}
    for count in (4, 8):
        files = []
        for i in range(count):
            path = tmp_path / f"s{count}_{i}.py"
            path.write_text("from marketdata import keep_last\n", encoding="utf-8")
            files.append(path)
        reads = []
        _keep_last_offenders_over(
            files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
        )
        measured[count] = (len(reads), count)
    for count, (reads_done, files_given) in measured.items():
        assert reads_done - files_given == 0, (count, measured)
