"""イベント分位水準線の**表示仕様**が common_view 側にあることを固定する（ISSUE-479 Wave2 C-1）。

背景: 色・線種・系列名サフィックスという純然たる表示仕様が、計算層 `common.event_quantiles`
に同居していた。計算仕様（MQL 移植の数値仕様）と表示仕様（UI の視認性仕様）は変更を要求する
アクターが異なるため SRP 違反であり、common は common_view を import できない
（安定度逆転の禁止が純度検定で機械的に固定されている）ため、再エクスポートによる緩和も取れない。

本ファイルが固定するもの:
    1. 表示 3 名が common_view から取得でき、値が移設前と byte 一致すること
    2. 計算層 common.event_quantiles に表示仕様の定義が残っていないこと（AST）
    3. 水準線の emit が「出力に使う本数」ちょうどしか発行されないこと（計算量）
"""
from __future__ import annotations

import ast
import importlib
import io
import pathlib
import tokenize

import pytest

event_quantiles = importlib.import_module("common.event_quantiles")


def _view():
    """新しい表示モジュールを遅延 import する。

    モジュール直下で import すると収集時エラーになり、同居する AST 検定まで巻き添えで
    実行されなくなる（失敗理由が「収集できない」に化ける）。各検定が自分の理由で
    失敗するよう、取得点を検定の中へ寄せる。
    """
    return importlib.import_module("common_view.event_quantile_view")


_CALC_SOURCE = pathlib.Path(event_quantiles.__file__)

#: 表示仕様として common_view へ移した名前（移設の全数）。
_MOVED_NAMES = ("EVQ_COLOR", "EVQ_LINE_SPECS", "emit_event_quantile_lines")


def test_the_view_module_exposes_the_moved_names() -> None:
    """表示 3 名が新モジュールから取得できる。"""
    view = _view()
    missing = [name for name in _MOVED_NAMES if not hasattr(view, name)]
    assert missing == [], f"common_view.event_quantile_view に不足: {missing}"


def test_the_package_reexports_the_moved_names() -> None:
    """パッケージ表面（common_view）からも同一オブジェクトで取得できる。"""
    import common_view

    view = _view()
    for name in _MOVED_NAMES:
        assert name in common_view.__all__, f"common_view.__all__ に不足: {name}"
        assert getattr(common_view, name) is getattr(view, name)


def test_the_display_values_are_byte_identical_to_the_pre_move_definition() -> None:
    """移設前の値と完全一致（色・線種・系列名サフィックスと順序）。"""
    assert _view().EVQ_COLOR == "rgba(210, 67, 58, 1)"
    assert _view().EVQ_LINE_SPECS == (
        ("med_hi", "solid"), ("med_lo", "solid"),
        ("ext_hi", "dashed"), ("ext_lo", "dashed"),
    )


def _top_level_definitions(tree: ast.Module) -> set[str]:
    """モジュール直下で定義される名前（関数定義・代入・注釈付き代入）の集合。"""
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return defined


def test_the_calculation_module_no_longer_defines_the_display_spec() -> None:
    """計算層に表示仕様の定義（代入・関数定義）が残っていない（AST）。"""
    # Arrange
    tree = ast.parse(_CALC_SOURCE.read_text(encoding="utf-8"))

    # Act
    leaked = sorted(_top_level_definitions(tree) & set(_MOVED_NAMES))

    # Assert
    assert leaked == [], f"表示仕様が計算層に残存: {_CALC_SOURCE.name} の {leaked}"


def test_emit_produces_one_line_per_spec_in_order() -> None:
    """4 本を EVQ_LINE_SPECS の順で emit する（系列名・色・線種の規約）。"""
    # Arrange
    calls: list[tuple] = []
    evq = {key: [1.0, 2.0] for key, _style in _view().EVQ_LINE_SPECS}
    times = [0, 1]

    def _record_line(name, ts, values, color, style):
        calls.append((name, color, style))
        return name

    # Act
    created = _view().emit_event_quantile_lines("x", times, evq, _record_line)

    # Assert
    assert created == [f"x_evq_{key}" for key, _style in _view().EVQ_LINE_SPECS]
    assert calls == [
        (f"x_evq_{key}", _view().EVQ_COLOR, style)
        for key, style in _view().EVQ_LINE_SPECS
    ]


@pytest.mark.parametrize("n_times", [10, 1000])
def test_emit_issues_exactly_the_lines_the_output_contains(n_times: int) -> None:
    """計算量テスト: 発行した emit − 戻り値の要素数 = 0。

    「使った emit」は戻り値のリストに現れた本数。作って捨てる線（NaN 判定で落とす等）が
    増えると発行が戻り値を上回り赤になる。回数を焼き込まず**無駄の不在**を固定し、
    系列長 10/1000 の 2 点で発行が変わらないこと（本数は表の行数だけで決まる）も固定する。
    """
    # Arrange
    issued: list[str] = []
    evq = {key: list(range(n_times)) for key, _style in _view().EVQ_LINE_SPECS}
    times = list(range(n_times))

    def _record_line(name, ts, values, color, style):
        issued.append(name)
        return name

    # Act
    created = _view().emit_event_quantile_lines("x", times, evq, _record_line)

    # Assert
    assert len(issued) - len(created) == 0
    assert len(issued) == len(_view().EVQ_LINE_SPECS)


# --------------------------------------------------------------------------- #
# 4. 消費者側の記述が移設先を指している（陳腐化の検出・ISSUE-479 Wave2 追随 A）
#
# 定義の移設（AST 検定）だけでは、消費者の docstring / コメントが旧所有者を指したまま残る。
# 読み手はそこから「どこを直せば表示が変わるか」を判断するため、間違った所有者を指す記述は
# 実装の誤りと同じ結果（別の指標へ波及する場所を書き換える）を招く。記述の鮮度も機械的に固定する。
# --------------------------------------------------------------------------- #
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREVIOUS_OWNER = "common.event_quantiles"
#: 走査から外す木（第三者コード・生成物・仮想環境）。
_EXCLUDED_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages"}


def _prose_blocks(path: pathlib.Path) -> list[tuple[int, str]]:
    """文字列定数（docstring 含む）と、連続するコメント行のまとまりを (行, 本文) で返す。"""
    source = path.read_text(encoding="utf-8")
    blocks: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - 走査対象外の壊れた木
        return blocks
    blocks.extend(
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    lines: list[str] = []
    start = previous = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        contiguous = bool(lines) and token.start[0] == previous + 1
        if not contiguous and lines:
            blocks.append((start, "\n".join(lines)))
            lines = []
        start = start if contiguous else token.start[0]
        lines.append(token.string)
        previous = token.start[0]
    if lines:
        blocks.append((start, "\n".join(lines)))
    return blocks


def _attributes_display_spec_to_the_old_owner(text: str) -> bool:
    """1 つの記述の中で「旧計算層」と「表示 3 名」が**同じ文**に現れるか。

    判定の単位は連続 2 行の窓である。大きな module docstring は計算層への正当な言及
    （外れ値イベント分位の算出関数など）と表示 3 名の言及を同居させるため、ブロック全体で
    突き合わせると偽陽性になる。折り返しで 2 行に跨る文を取りこぼさない最小の窓が 2 行。
    """
    lines = text.splitlines() or [text]
    windows = [" ".join(lines[i:i + 2]) for i in range(len(lines))]
    return any(
        _PREVIOUS_OWNER in window and any(name in window for name in _MOVED_NAMES)
        for window in windows
    )


def _stale_ownership_mentions() -> list[str]:
    """表示 3 名の所有者を旧計算層と書いている記述（docstring / コメント）を列挙する。"""
    offenders: list[str] = []
    for path in _REPO_ROOT.rglob("*.py"):
        if _EXCLUDED_PARTS & set(path.parts):
            continue
        for lineno, text in _prose_blocks(path):
            if _attributes_display_spec_to_the_old_owner(text):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    return sorted(offenders)


def test_no_document_attributes_the_display_spec_to_the_calculation_layer() -> None:
    """消費者の記述が表示 3 名の所有者を旧計算層と書いていない。"""
    offenders = _stale_ownership_mentions()
    assert offenders == [], (
        "表示仕様の所有者を旧計算層と書いた記述が残っている（所有は "
        "common_view.event_quantile_view）:\n" + "\n".join(offenders)
    )


def test_the_stale_ownership_detector_catches_a_synthetic_offender() -> None:
    """検出器の自己検定: 陳腐化記述は捕捉し、正当な計算層への言及は捕捉しない。"""
    # Arrange: 是正前の実物と同型（1 行内・折り返し 2 行）＋ 偽陽性にしてはならない記述。
    stale_one_line = f"# 色・線種は {_PREVIOUS_OWNER}（{_MOVED_NAMES[0]}）が単一情報源。"
    stale_wrapped = (
        f"表示規約（色・線種）も ``{_PREVIOUS_OWNER}`` の\n``{_MOVED_NAMES[2]}`` に委譲する。"
    )
    legitimate = (
        f"水準は ``{_PREVIOUS_OWNER}.outlier_event_quantiles`` で求める。\n"
        "\n"
        "……（間に別の段落）……\n"
        "\n"
        f"表示規約は common_view の ``{_MOVED_NAMES[1]}`` に従う。"
    )

    # Act / Assert
    assert _attributes_display_spec_to_the_old_owner(stale_one_line)
    assert _attributes_display_spec_to_the_old_owner(stale_wrapped)
    assert not _attributes_display_spec_to_the_old_owner(legitimate)
