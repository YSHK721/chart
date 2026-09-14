"""試作（prototype_*）から共有資産への書き込みを禁じるゲート（ISSUE-479 Wave2 フェーズ 0-3）。

固定する仕様:
    `prototype_*` 配下の Python は、**保護領域**——本番データ `data/marketdata` と、
    回帰ゲートの固定値 `simulator/tests/fixtures`——へ書き込んではならない。
    試作は自分のディレクトリの中だけで完結する。

なぜ禁じるか（構造的理由）:
    試作は「使い捨ての実験」であり、レビューも回帰ゲートも通っていない。その試作が
    本番データや fixture を上書きできる状態は、実験の失敗がそのまま共有資産の破壊に
    なるということである。実際 prototype_260626-01 の M1 ロールアップ試作（ISSUE-479
    Wave2b で削除済み）は本番 M1 CSV を絶対パスで無条件に上書きし、
    `prototype_260811-01/make_regression_fixture.py` は
    回帰ゲートが読む fixture の生成器そのものだった（＝ゲートの期待値を試作が
    作っていた）。

    「気をつける」では防げない。試作は定義上その場限りの実行であり、実行前に
    誰かがレビューする工程を持たない。だから機械的検査で塞ぐ。

なぜ構文木で測るか:
    実行して観測する方法（書き込みを監視する）は、その実行が本番データを壊す。
    書き込みの意図は構文木にしか現れないので、構文木を読む。

検出する形（`TestTheWriteGateHasDetectionPower` が実コードで固定する）:
    1. 保護領域を指す名前への書込メソッド（write_text / write_bytes / mkdir / touch /
       unlink / rename / replace）
    2. 保護領域を指す名前に対する書込モードの open
    3. 保護領域を指す名前を引数に取る書出関数（to_csv / to_parquet / to_json /
       savefig 等）
    4. 保護領域から**派生した**名前（`p = FIXTURE_DIR / "x.json"`）への 1.〜3.
    5. 組込 open の第 1 引数が保護領域を指す名前である形

fail-stop による無効化:
    モジュール本体の先頭側に**無条件の** SystemExit（`raise SystemExit(...)` または
    `sys.exit(...)`）が置かれている場合、それ以降のモジュール本体は実行されず、
    定義された関数も呼び出せない（import も実行も同じ例外で止まる）。したがって
    その位置より後ろの書き込みは到達不能であり、違反として数えない。
    数えるのは SystemExit より**前**にある本体直下の書き込みだけである。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

#: リポジトリ根（このファイル: <repo>/tools/tests/ → parents[2]）。
_REPO = Path(__file__).resolve().parents[2]

#: 試作ディレクトリの探索パターン。
_PROTOTYPE_GLOB = "prototype_*"

#: 保護領域（POSIX 表記の部分列で判定する）。
_PROTECTED_ROOTS = (
    "data/marketdata",
    "simulator/tests/fixtures",
)

#: 受け手（Path 相当）を破壊しうるメソッド。
_WRITE_METHODS = frozenset({
    "write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir",
    "rename", "replace", "symlink_to", "hardlink_to", "chmod",
})

#: 第 1 引数に出力先を取る書出関数（pandas / matplotlib / json 系の慣用）。
_WRITER_FUNCTIONS = frozenset({
    "to_csv", "to_parquet", "to_json", "to_pickle", "to_hdf", "to_feather",
    "savefig", "savez", "save", "dump",
})

#: 書込モードを表す open の文字列（"r" のみは読取）。
_WRITE_MODE_CHARS = frozenset({"w", "a", "x", "+"})

#: 解消待ちの違反（単調減少のみ）。**現在 0 件**——X-3 は解消済みである。
#: 新規に 1 件でも増えたら赤になる。ここへ追加するのは「解消の期日と手段が決まって
#: いる」場合だけであり、恒久的な免除リストではない。
#:
#: 経緯（本ゲート新設時の実測: 試作 2 本・書き込み 5 点）:
#:   prototype_260626-01 の M1 ロールアップ試作:49,50       → 1-E で fail-stop、
#:                                                            Wave2b でファイルごと削除（解消）
#:   prototype_260811-01/make_regression_fixture.py:69,73,93 → 1-D で本体へ移設（解消）
_FROZEN_OFFENDERS: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class Write:
    """試作から保護領域への書き込み 1 件。"""

    path: str
    line: int
    detail: str

    def ident(self) -> str:
        """凍結台帳の鍵。行番号は含めない（上の行を足すだけで鍵が変わるのを避ける）。"""
        return f"{self.path}::{self.detail}"


def _literals_of(node: ast.AST) -> "list[str]":
    """式に現れる文字列リテラルを**ソース上の出現順**に集める（Path 合成の断片を拾う）。

    `ast.walk` は幅優先なので、左結合の `a / "x" / "y"` では外側（後ろの断片）から
    先に現れる。位置で並べ直さないと断片の順序が逆転し、`"simulator"/"tests"/…` の
    ような合成を取り逃す（実測済みの罠）。
    """
    constants = [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    constants.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    return [n.value for n in constants]


def _points_into_protected(node: ast.AST) -> bool:
    """式が保護領域を指すか（文字列断片を「/」で連結して部分列判定する）。"""
    joined = "/".join(part.strip("/") for part in _literals_of(node) if part)
    return any(root in joined for root in _PROTECTED_ROOTS)


def _protected_names(tree: ast.Module) -> "set[str]":
    """保護領域を指す名前を集める（派生した名前も伝播させる）。

    事前条件: `tree` はモジュールの構文木。
    事後条件: 直接リテラルで保護領域を組み立てた名前と、その名前から派生した名前を返す。
    """
    protected: "set[str]" = set()
    for _ in range(3):          # 代入の連鎖（派生の深さ）を高々 3 段まで畳む
        grew = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names:
                continue
            derived = _points_into_protected(value) or any(
                isinstance(n, ast.Name) and n.id in protected for n in ast.walk(value)
            )
            if derived and not set(names) <= protected:
                protected |= set(names)
                grew = True
        if not grew:
            break
    return protected


def _is_protected_expr(node: ast.AST, protected: "set[str]") -> bool:
    """式が保護領域を指すか（リテラル合成 or 保護名からの派生）。"""
    if _points_into_protected(node):
        return True
    return any(isinstance(n, ast.Name) and n.id in protected for n in ast.walk(node))


def _is_write_mode(node: ast.Call) -> bool:
    """open 呼出が書込モードか（mode 未指定は読取）。"""
    modes = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [
        k.value.value
        for k in node.keywords
        if k.arg == "mode" and isinstance(k.value, ast.Constant)
        and isinstance(k.value.value, str)
    ]
    text = "".join(m.value if isinstance(m, ast.Constant) else m for m in modes)
    return any(ch in _WRITE_MODE_CHARS for ch in text)


def _writes_in(node: ast.AST, protected: "set[str]", filename: str) -> "list[Write]":
    """1 つの構文木片に含まれる「保護領域への書き込み」を列挙する。"""
    out: "list[Write]" = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            if func.attr in _WRITE_METHODS and _is_protected_expr(func.value, protected):
                out.append(Write(filename, sub.lineno, f"{func.attr} → 保護領域"))
            elif func.attr == "open" and _is_protected_expr(func.value, protected) \
                    and _is_write_mode(sub):
                out.append(Write(filename, sub.lineno, "open(書込モード) → 保護領域"))
            elif func.attr in _WRITER_FUNCTIONS and sub.args \
                    and _is_protected_expr(sub.args[0], protected):
                out.append(Write(filename, sub.lineno, f"{func.attr}() → 保護領域"))
        elif isinstance(func, ast.Name):
            if func.id == "open" and sub.args and _is_protected_expr(sub.args[0], protected) \
                    and _is_write_mode(sub):
                out.append(Write(filename, sub.lineno, "open(書込モード) → 保護領域"))
            elif func.id in _WRITER_FUNCTIONS and sub.args \
                    and _is_protected_expr(sub.args[0], protected):
                out.append(Write(filename, sub.lineno, f"{func.id}() → 保護領域"))
    return out


def _fail_stop_index(tree: ast.Module) -> "int | None":
    """モジュール本体の無条件 SystemExit の位置（本体直下の何番目の文か）を返す。

    事後条件: 見つからなければ None。見つかればその添字（それ以降は到達不能）。
    """
    for index, stmt in enumerate(tree.body):
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc
            name = exc.func if isinstance(exc, ast.Call) else exc
            if isinstance(name, ast.Name) and name.id == "SystemExit":
                return index
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            if isinstance(func, ast.Attribute) and func.attr == "exit":
                return index
            if isinstance(func, ast.Name) and func.id == "exit":
                return index
    return None


def _writes_in_source(source: str, filename: str) -> "list[Write]":
    """ソース 1 本の「到達可能な」保護領域書き込みを列挙する。

    事前条件: `source` は構文的に妥当な Python。
    事後条件: 無条件 SystemExit がある場合、それより後ろの文と全関数定義は到達不能
             として除外する。
    """
    tree = ast.parse(source, filename=filename)
    protected = _protected_names(tree)
    stop = _fail_stop_index(tree)
    body = tree.body if stop is None else tree.body[:stop]
    out: "list[Write]" = []
    for stmt in body:
        out.extend(_writes_in(stmt, protected, filename))
    return out


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _prototype_files() -> "list[Path]":
    """試作の全 `.py`（`__pycache__` を除く）。"""
    return sorted(
        path
        for proto in _REPO.glob(_PROTOTYPE_GLOB)
        if proto.is_dir()
        for path in proto.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _write_scan_over(files, read=None) -> "tuple[list[Path], list[Write]]":
    """`files` を走査して違反を返す。**1 ファイルにつき読込 1 回**。

    読込点を差し替えられるのは、計算量検定が「読み捨てが無い」ことを数えるためである。
    """
    reader = read or _read_source
    scanned: "list[Path]" = []
    offenders: "list[Write]" = []
    for path in files:
        scanned.append(path)
        rel = str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path)
        offenders.extend(_writes_in_source(reader(path), rel))
    return scanned, offenders


def _write_scan() -> "tuple[list[Path], list[Write]]":
    return _write_scan_over(_prototype_files())


class TestPrototypesDoNotWriteIntoSharedAssets:
    """試作が本番データ・fixture を書き換えないこと。"""

    def test_no_new_write_into_a_protected_root(self):
        _, offenders = _write_scan()
        new = [o for o in offenders if o.ident() not in _FROZEN_OFFENDERS]
        assert new == [], (
            "試作から保護領域への書き込み:\n  "
            + "\n  ".join(f"{o.path}:{o.line} {o.detail}" for o in new)
            + f"\n  保護領域: {list(_PROTECTED_ROOTS)}"
            + "\n  生成器は本体（simulator/tools 等）へ置き、試作からは書かないでください。"
        )

    def test_no_frozen_offender_is_stale(self):
        """解消済み違反の凍結残留を禁じる（凍結は単調減少しかできない）。"""
        _, offenders = _write_scan()
        live = {o.ident() for o in offenders}
        stale = sorted(set(_FROZEN_OFFENDERS) - live)
        assert stale == [], (
            "解消済みなのに凍結に残っています（_FROZEN_OFFENDERS から外してください）:\n  "
            + "\n  ".join(stale)
        )


class TestTheWriteGateHasDetectionPower:
    """ゲートが空振りしていないこと（恒真式に退化していないこと）。"""

    def test_the_gate_actually_scans_files(self):
        scanned, _ = _write_scan()
        assert len(scanned) > 0

    @pytest.mark.parametrize(
        "form,source",
        [
            ("絶対パス literal + to_csv",
             'from pathlib import Path\n'
             'OUT = Path("/workspaces/app/data/marketdata/x.csv")\n'
             'df.to_csv(OUT)\n'),
            ("Path 合成 + write_text",
             'D = ROOT / "simulator" / "tests" / "fixtures" / "acc"\n'
             'D.write_text("{}")\n'),
            ("派生した名前 + open(w)",
             'D = ROOT / "simulator" / "tests" / "fixtures" / "acc"\n'
             'p = D / "a.csv"\n'
             'p.open("w")\n'),
            ("mkdir",
             'D = ROOT / "data" / "marketdata"\n'
             'D.mkdir(parents=True, exist_ok=True)\n'),
            ("組込 open の第 1 引数",
             'D = ROOT / "data" / "marketdata" / "a.csv"\n'
             'open(D, "w")\n'),
        ],
        ids=["to_csv", "write_text", "derived_open", "mkdir", "builtin_open"],
    )
    def test_every_write_form_is_detected(self, form, source):
        assert _writes_in_source(source, "p.py"), (form, source)

    @pytest.mark.parametrize(
        "source",
        [
            'D = ROOT / "data" / "marketdata" / "a.csv"\nD.read_text()\n',
            'D = ROOT / "data" / "marketdata" / "a.csv"\nD.open("r")\n',
            'D = ROOT / "data" / "marketdata" / "a.csv"\nopen(D)\n',
            'OUT = HERE / "out" / "results.json"\nOUT.write_text("{}")\n',
        ],
        ids=["read_text", "open_r", "builtin_open_default", "own_directory"],
    )
    def test_reads_and_own_directory_writes_are_not_flagged(self, source):
        assert _writes_in_source(source, "p.py") == []

    def test_a_fail_stop_makes_later_writes_unreachable(self):
        source = (
            'from pathlib import Path\n'
            'OUT = Path("/workspaces/app/data/marketdata/x.csv")\n'
            'raise SystemExit("後継 tools/build_tick_rollup.py を使ってください")\n'
            'def main():\n'
            '    OUT.write_text("x")\n'
            'main()\n'
        )
        assert _writes_in_source(source, "p.py") == []

    def test_a_write_before_the_fail_stop_is_still_flagged(self):
        """ガードの位置が甘い（書き込みの後ろ）場合は無効化と認めない。"""
        source = (
            'from pathlib import Path\n'
            'OUT = Path("/workspaces/app/data/marketdata/x.csv")\n'
            'OUT.write_text("x")\n'
            'raise SystemExit("too late")\n'
        )
        assert _writes_in_source(source, "p.py") != []

    def test_a_conditional_exit_does_not_count_as_a_fail_stop(self):
        source = (
            'from pathlib import Path\n'
            'OUT = Path("/workspaces/app/data/marketdata/x.csv")\n'
            'if flag:\n'
            '    raise SystemExit("only sometimes")\n'
            'OUT.write_text("x")\n'
        )
        assert _writes_in_source(source, "p.py") != []

    def test_the_frozen_ledger_key_ignores_line_numbers(self):
        assert Write("p.py", 1, "mkdir → 保護領域").ident() == \
            Write("p.py", 99, "mkdir → 保護領域").ident()


class TestTheWriteGateDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _write_scan_over(
            _prototype_files(), read=lambda p: (reads.append(p), _read_source(p))[1]
        )
        # 発行（読込）− 使用（判定に用いたファイル）= 0。読み捨てが 1 件も無い。
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0  # 同じファイルを二度走査しない

    def test_the_read_count_is_determined_by_the_file_count_alone(self, tmp_path):
        """走査対象 3 件 / 6 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
        measured = {}
        for count in (3, 6):
            files = []
            for i in range(count):
                path = tmp_path / f"m{count}_{i}.py"
                path.write_text('x = 1\n', encoding="utf-8")
                files.append(path)
            reads: "list[Path]" = []
            scanned, _ = _write_scan_over(
                files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
            )
            measured[count] = (len(reads), len(scanned), count)
        for count, (reads_done, scanned_done, given) in measured.items():
            assert reads_done - scanned_done == 0, (count, measured)
            assert scanned_done - given == 0, (count, measured)
