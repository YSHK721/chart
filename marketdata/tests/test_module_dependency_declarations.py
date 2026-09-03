"""docstring が宣言した依存範囲を **検定で強制する**（ISSUE-262）。

なぜ必要か:
    本 repo の是正は繰り返し「コメントに正しいことを書く」で終わっていた。宣言は施行されている
    ように読めるが、施行する仕組みが無ければ次の編集で静かに破れる。実際 ``resample`` は
    「pandas のみに依存」と宣言しながら ``csv_schema`` を、``tick_m1`` は「pandas + paths のみ」と
    宣言しながら ``outlier_policy`` / ``csv_schema`` / ``tail_reader`` を import していた。

本テストの規約:
    各モジュールの **許可 import 集合を明示列挙**し、AST 走査で実 import と突き合わせる。
    関数内の遅延 import も対象にする（宣言を迂回する抜け穴にしないため）。
    依存を増やすときは、docstring と本表の両方を同時に更新することを強制する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]

#: モジュール → 許可する外部 import（stdlib と ``__future__`` は常に許可）。
#:
#: 値は **docstring の宣言と一致していなければならない**。宣言を広げるなら、その理由を
#: 当該モジュールの docstring へ書いたうえで本表も広げる（片方だけの更新を許さない）。
_ALLOWED: "dict[str, set[str]]" = {
    # 時間足台帳（唯一源）。**依存ゼロ**＝stdlib すら型注釈用の typing のみ。pandas を持ち込むと
    # 「pandas を使えない純層も同じ台帳から導出する」という分離目的（ISSUE-261）が崩れる。
    "tf_ledger.py": set(),
    # 純規則層。csv_schema / tf_ledger はいずれも依存ゼロの定数モジュール
    # （前者は集約対象列の唯一源・後者は時間足台帳の唯一源）。
    "resample.py": {"pandas", "marketdata.csv_schema", "marketdata.tf_ledger"},
    # comma 形式 CSV → Candle の adapter。``datawindow.half_open`` は取得窓 `[start, end)` の
    # 境界正規化と半開判定の唯一の実体（ISSUE-401 🟡-2）。本 adapter が自前で
    # ``int(start.timestamp())`` を持つと naive datetime をローカル TZ で解釈し、同じ窓を受ける
    # Bar 段（UTC 解釈）と食い違う（実測 32400 秒差）。**この 1 エントリを消すと複製が復活する**
    # ため、依存として明示し検定で固定する。
    "csv_source.py": {"pandas", "datawindow.half_open", "marketdata.port"},
    # M1 素材化。外れ値方針・CSV スキーマ・末尾読取は marketdata 内の下位部品。
    # ``marketdata.keep_last`` は「同一キーの最終出現を採る」規則の唯一の実体（依存ゼロの中立核・
    # ISSUE-479 F-6）。この 1 エントリを消すと _dedupe_minutes に同じ式の複製が復活する。
    "tick_m1.py": {
        "pandas",
        "marketdata.paths",
        "marketdata.outlier_policy",
        "marketdata.csv_schema",
        "marketdata.tail_reader",
        "marketdata.keep_last",
    },
}

_STDLIB_PREFIXES = {
    "__future__", "typing", "pathlib", "datetime", "os", "sys", "re", "json", "csv",
    "time", "math", "logging", "tempfile", "collections", "dataclasses", "functools",
    "itertools", "hashlib", "zlib", "queue", "threading", "urllib", "shutil", "glob",
}


def _external_imports(path: Path) -> "set[str]":
    """モジュール内の全 import（関数内の遅延 import を含む）から外部依存名を集める。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相対 import は自パッケージ内＝対象外
                continue
            module = node.module or ""
            if module == "marketdata":
                # `from marketdata import X` は marketdata.X として数える（粒度を揃える）。
                for alias in node.names:
                    out.add(f"marketdata.{alias.name}")
            else:
                out.add(module)
    return {n for n in out if n.split(".")[0] not in _STDLIB_PREFIXES}


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_module_imports_match_the_declared_dependency_set(filename):
    """実 import が宣言（本表）を超えていない。

    超えていたら、docstring の依存宣言が事実と食い違っている。依存を足すのが正しいなら
    docstring と本表を同時に更新する。足すべきでないなら import を消す。
    """
    got = _external_imports(_PKG / filename)
    extra = got - _ALLOWED[filename]
    assert not extra, (
        f"{filename} が宣言外の依存を持っています: {sorted(extra)}。"
        " docstring の依存宣言と本表を同時に更新するか、import を撤去してください。"
    )


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_declared_dependency_set_has_no_stale_entries(filename):
    """本表に、実際には使われていない許可エントリが残っていない（宣言の陳腐化を防ぐ）。"""
    got = _external_imports(_PKG / filename)
    stale = _ALLOWED[filename] - got
    assert not stale, (
        f"{filename} の許可表に未使用のエントリが残っています: {sorted(stale)}。"
        " 依存が消えたら宣言側も狭めてください。"
    )
