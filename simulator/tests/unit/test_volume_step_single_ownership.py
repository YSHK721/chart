"""刻み量子化規則の所有者は 1 つだけ — 再実装を AST 走査で赤にするゲート（ISSUE-502 D-13）。

**なぜ規約ではなく検査なのか**: 「刻み比が整数に十分近いか（abs(ratio - round(ratio))
<= 許容）を見て round か floor を選ぶ」規則の本体が、domain の volume_step ・
partial close rule ・ order の 3 モジュールに書かれていた（実測: 正規化 AST 指紋で
前 2 者は完全一致、3 者目は同じ近整数判定の述語側）。部分決済側のコメントは
「floor to step と同一の許容」と**複製を自認**したうえで複製されており、コメント・規約では
守られなかった。よって機械的に検出する。

**検出の仕組み（4 規則・どれか 1 つでも当たれば違反）**

1. **構造一致**: 式の部分木に abs(X - round(X)) の形が現れたら違反。実際に複製された
   形そのものを捕える。
2. **許容定数の再定義**: 名前が STEP RATIO TOL で終わる束縛（先頭アンダースコアの
   有無を問わない）が現れたら違反。「同じ値を自分で持ち直す」経路を塞ぐ。
3. **別綴りの近整数判定**: 1 つの式（Compare / Call）の部分木に、刻み許容の参照と
   丸め関数（round / floor / ceil / trunc / isclose）の呼び出しが**同時に**現れたら違反。
   規則 1 を isclose 等へ書き換えた再実装を捕える。
4. **越境 import**: 刻み許容を所有モジュール以外から import したら違反。是正前に実在した
   「他モジュールの private 定数を輸入する」経路（order の private 定数を 2 モジュールが
   輸入していた）の再発を塞ぐ。

**走査対象をハードコードしない**: simulator/domain/*.py の glob から所有モジュール
自身を除いて拾う（新しい domain モジュールは自動的に対象に入る）。

**非空虚性**: (a) 所有モジュール自身を走査すると規則 1 が当たること、(b) 是正前の
部分決済側の本体・isclose 書き換え版・許容定数の再定義・越境 import を合成ファイルへ
注入すると赤になること、を実証する。

**射程の限定子（この検査で捕えられないもの）**:
  * domain の外（usecase / adapter）は対象外。戦略側のロット正規化は「戦略ごとに原典
    （MQL5 EA）が異なるため意図的に重複させる」ものであり、共通化してはならない
    （test normalize lot originals diverge が反対側を固定している）。
  * 許容も丸め関数も使わずに書き直した「別物に見える等価実装」は検出できない。捕えるのは
    実際に起きた失敗モード（コピー & ペーストと綴り替え）である。
  * 部分決済側の「quantized >= position_volume - 許容 * volume_step」のような**量子化で
    ない**許容の使用は違反にしない（規則 3 が丸め関数との共起を要求するため）。この偽陽性
    でないことは test_the_real_domain_tree_is_clean が実証する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.domain import volume_step

OWNER_PATH = Path(volume_step.__file__).resolve()
DOMAIN_DIR = OWNER_PATH.parent

#: 刻み許容の唯一の供給元（規則 4 の越境判定に使う）。
_OWNER_MODULE = volume_step.__name__

#: 丸め・切り捨ての語彙（規則 3 の共起判定に使う）。
_ROUNDING_CALLS = frozenset({"round", "floor", "ceil", "trunc", "isclose"})

#: 刻み許容の名前（先頭アンダースコアを無視して末尾一致で見る）。
_TOL_SUFFIX = "STEP_RATIO_TOL"


# --- 判定の素材 -----------------------------------------------------------------

def _called_name(node: ast.AST) -> "str | None":
    """``round(...)`` / ``math.floor(...)`` の呼び先名（属性なら末尾）を返す。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_tolerance_ref(node: ast.AST) -> bool:
    """刻み許容（``*STEP_RATIO_TOL``）への参照か。"""
    if isinstance(node, ast.Name):
        return node.id.lstrip("_").endswith(_TOL_SUFFIX)
    if isinstance(node, ast.Attribute):
        return node.attr.lstrip("_").endswith(_TOL_SUFFIX)
    return False


def _is_near_integer_form(node: ast.AST) -> bool:
    """``abs(X - round(X))`` の形か（X の綴りは問わない）。"""
    if _called_name(node) != "abs" or len(node.args) != 1:
        return False
    inner = node.args[0]
    if not (isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Sub)):
        return False
    if _called_name(inner.right) != "round":
        return False
    left = ast.dump(inner.left)
    rounded_arg = inner.right.args
    return len(rounded_arg) == 1 and ast.dump(rounded_arg[0]) == left


def _binds_tolerance(node: ast.AST) -> bool:
    """``*STEP_RATIO_TOL = ...`` の束縛か。"""
    targets: "list[ast.expr]" = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return False
    return any(_is_tolerance_ref(t) for t in targets)


def _mixes_tolerance_with_rounding(node: ast.AST) -> bool:
    """1 つの式の部分木に「刻み許容の参照」と「丸め呼び出し」が同時に現れるか。"""
    if not isinstance(node, (ast.Compare, ast.Call)):
        return False
    has_tol = False
    has_round = False
    for sub in ast.walk(node):
        if _is_tolerance_ref(sub):
            has_tol = True
        name = _called_name(sub)
        if name is not None and name in _ROUNDING_CALLS:
            has_round = True
    return has_tol and has_round


def _imports_tolerance_from_elsewhere(node: ast.AST) -> bool:
    """刻み許容を所有者以外から import しているか（越境 private import の再発防止）。"""
    if not isinstance(node, ast.ImportFrom):
        return False
    if not any(alias.name.lstrip("_").endswith(_TOL_SUFFIX) for alias in node.names):
        return False
    return node.module != _OWNER_MODULE


# --- 走査 -----------------------------------------------------------------------

def find_quantization_reimplementations(paths: "list[Path]") -> "list[tuple[str, int, str]]":
    """(ファイル名, 行番号, 違反理由) の一覧を返す。空 list なら違反なし。"""
    violations: "list[tuple[str, int, str]]" = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_near_integer_form(node):
                reason = "構造一致: abs(x - round(x)) は volume_step が所有する量子化式"
            elif _binds_tolerance(node):
                reason = f"定数の再定義: {_TOL_SUFFIX} の所有者は volume_step"
            elif _mixes_tolerance_with_rounding(node):
                reason = "別綴り: 刻み許容と丸め呼び出しの共起は近整数判定の再実装"
            elif _imports_tolerance_from_elsewhere(node):
                reason = f"越境 import: 刻み許容の供給元は {_OWNER_MODULE} だけ"
            else:
                continue
            violations.append((path.name, getattr(node, "lineno", 0), reason))
    return violations


def domain_sources() -> "list[Path]":
    """``simulator/domain/*.py`` から所有モジュール自身を除いた全ファイル。"""
    return [p for p in sorted(DOMAIN_DIR.glob("*.py")) if p.resolve() != OWNER_PATH]


# --- 検定 ------------------------------------------------------------------------

def test_the_owner_module_is_the_one_that_holds_the_rule():
    # 走査の前提: 所有者が実際に規則本体を持つこと。持たないなら以下の検査は空虚になる
    # （規則が別モジュールへ移されたのに、このゲートだけ残っている状態を赤にする）。
    assert find_quantization_reimplementations([OWNER_PATH]) != []
    assert volume_step.STEP_RATIO_TOL == 1e-6
    assert set(volume_step.__all__) >= {"STEP_RATIO_TOL", "is_step_multiple", "quantize_to_step"}


def test_domain_files_are_discovered_by_glob_not_by_a_hardcoded_list():
    # 新しい domain モジュールが黙って対象外にならないこと。
    names = {p.name for p in domain_sources()}

    assert "volume_step.py" not in names
    assert {"order.py", "partial_close_rule.py"} <= names
    assert len(names) >= 15


def test_the_real_domain_tree_is_clean():
    # 本ゲートの本体。違反 0 件であること。
    violations = find_quantization_reimplementations(domain_sources())

    assert violations == [], "刻み量子化の再実装を検出しました: " + "; ".join(
        f"{f}:{ln}（{why}）" for f, ln, why in violations
    )


_INJECTED_COPY = '''
"""負の対照: 是正前の部分決済側の量子化本体（写経）。"""
import math

from simulator.domain.volume_step import STEP_RATIO_TOL


def close_volume(raw, volume_step):
    ratio = raw / volume_step
    if abs(ratio - round(ratio)) <= STEP_RATIO_TOL:
        steps = round(ratio)
    else:
        steps = math.floor(ratio)
    return steps * volume_step
'''

_INJECTED_ISCLOSE_REWRITE = '''
"""負の対照: 綴りを isclose に替えた再実装。"""
import math

from simulator.domain.volume_step import STEP_RATIO_TOL


def quantize(raw, step):
    r = raw / step
    n = int(r) if not math.isclose(r, int(r) + 1, abs_tol=STEP_RATIO_TOL) else int(r) + 1
    return n * step
'''

_INJECTED_TOL_REDEFINITION = '''
"""負の対照: 許容定数を自分で持ち直す。"""
_STEP_RATIO_TOL = 1e-6
'''

_INJECTED_CROSS_MODULE_IMPORT = '''
"""負の対照: 是正前に実在した越境 private import。"""
from simulator.domain.order import _STEP_RATIO_TOL
'''


@pytest.mark.parametrize(
    "injected_source, expected_reason_head",
    [
        (_INJECTED_COPY, "構造一致"),
        (_INJECTED_ISCLOSE_REWRITE, "別綴り"),
        (_INJECTED_TOL_REDEFINITION, "定数の再定義"),
        (_INJECTED_CROSS_MODULE_IMPORT, "越境 import"),
    ],
)
def test_gate_detects_injected_reimplementations(tmp_path, injected_source, expected_reason_head):
    # 非空虚性の実証: 4 規則がそれぞれ独立に赤を出すこと。
    injected = tmp_path / "injected_domain_module.py"
    injected.write_text(injected_source, encoding="utf-8")

    violations = find_quantization_reimplementations([injected])

    assert violations != []
    assert any(why.startswith(expected_reason_head) for _, _, why in violations)


def test_gate_does_not_flag_a_tolerance_used_outside_quantization(tmp_path):
    # 偽陽性の限定子: 量子化でない許容の使用（部分決済の「全量でない」境界）は違反にしない。
    injected = tmp_path / "plain_domain_module.py"
    injected.write_text(
        "from simulator.domain.volume_step import STEP_RATIO_TOL\n"
        "\n"
        "def is_full_close(quantized, position_volume, volume_step):\n"
        "    return quantized >= position_volume - STEP_RATIO_TOL * volume_step\n",
        encoding="utf-8",
    )

    assert find_quantization_reimplementations([injected]) == []
