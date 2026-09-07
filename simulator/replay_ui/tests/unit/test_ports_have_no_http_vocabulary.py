"""usecase の抽象に HTTP の語彙が現れないことを固定する（ISSUE-502 段階 5B・DIP）。

## 是正前の欠陥（SOLID 精査台帳 2026-09-06 の実測）

``replay_ports.py`` の 4 つの Port（MarketProfileFormingPort / MarketProfilePort /
TickvolProfilePort / CatalogPort）は戻り型が ``tuple[int, dict]``＝ **(HTTP ステータス, ボディ)**
だった。内側（usecase）の抽象定義が外側の技術（HTTP）の語彙を持つと、HTTP のエラー表現を
変えることが**抽象の変更**になる。依存の向きが反転している。

## 是正後の規則（本ファイルが Red で守るもの）

1. ``usecase`` 配下に HTTP ステータス番号が 1 つも現れない（``200`` / ``400`` / ``422`` / ``500``）。
2. ``usecase`` 配下が HTTP 契約モジュール（``api_shared.http_contract``）を import しない。
3. 4 つの Port の各メソッド（profile / forming / catalog）の戻り注釈が :class:`PortResult` である。
4. 分類 → ステータスの写像は framework の ``http_response_for`` **1 箇所だけ**が持つ。

各検定には検出力の自己検定（変異体を与えれば落ちること）を添える——走査が空振りしていると、
規則は宣言だけになって何も止めない。

計算量検定（絶対命令 2026-08-28）: 走査はモジュール 1 本につき parse 1 回
    （発行 − モジュール数 = 0）。モジュール数を変えた 2 点で固定する。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.framework import serve_replay
from simulator.replay_ui.framework.serve_replay import http_response_for
from simulator.replay_ui.usecase import replay_ports
from simulator.replay_ui.usecase.port_result import PortResult

#: usecase 層のディレクトリ（HTTP 語彙の不在を要求する範囲）。
_USECASE_DIR = Path(inspect.getsourcefile(replay_ports)).parent

#: HTTP ステータス番号（正典表 ERROR_STATUS の値 ＋ 成功の 200）。
_HTTP_STATUS_NUMBERS = {200, 400, 422, 500}

#: 戻り注釈が PortResult であることを要求する Port（Protocol 名, メソッド名）。
_PORT_METHODS = (
    ("MarketProfileFormingPort", "forming"),
    ("MarketProfilePort", "profile"),
    ("TickvolProfilePort", "profile"),
    ("CatalogPort", "catalog"),
)


def _usecase_sources() -> "list[Path]":
    return sorted(p for p in _USECASE_DIR.glob("*.py"))


def _tree_of(path: Path, *, parse=ast.parse) -> ast.Module:
    """1 ファイルを **1 回だけ** parse して木を返す。"""
    return parse(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def usecase_trees() -> "dict[str, ast.Module]":
    return {p.name: _tree_of(p) for p in _usecase_sources()}


# --------------------------------------------------------------------------------------
# 1. HTTP ステータス番号が現れない
# --------------------------------------------------------------------------------------
def _status_numbers_in(tree: ast.Module) -> "set[int]":
    """コードに現れる HTTP ステータス相当の整数リテラルを集める。"""
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value in _HTTP_STATUS_NUMBERS
    }


@pytest.mark.parametrize("name", [p.name for p in _usecase_sources()])
def test_the_usecase_module_names_no_http_status_number(usecase_trees, name: str) -> None:
    """usecase のコードに HTTP ステータス番号が 1 つも無い。

    番号を内側が知っていると、HTTP のエラー表現の変更が内側の変更になる（DIP 違反）。
    """
    found = sorted(_status_numbers_in(usecase_trees[name]))
    assert found == [], (
        f"usecase/{name} に HTTP ステータス番号が現れている: {found}。"
        " 分類 → 番号の写像は framework の http_response_for だけが持つ。"
    )


def test_the_status_scanner_can_see_a_status_number() -> None:
    """走査器の検出力: 変異体（``return 400, body``）を与えれば検出する。"""
    mutated = ast.parse("def f():\n    return 400, {}\n")
    assert _status_numbers_in(mutated) == {400}, "走査器が番号を見落としている（ガードが空虚）"


# --------------------------------------------------------------------------------------
# 2. HTTP 契約モジュールを import しない
# --------------------------------------------------------------------------------------
def _imported_modules(tree: ast.Module) -> "set[str]":
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("name", [p.name for p in _usecase_sources()])
def test_the_usecase_module_does_not_import_the_http_contract(usecase_trees, name: str) -> None:
    """usecase は ``api_shared.http_contract``（ステータス表・nested ボディ整形）を知らない。"""
    imported = _imported_modules(usecase_trees[name])
    http_ones = sorted(m for m in imported if "http" in m)
    assert http_ones == [], f"usecase/{name} が HTTP 契約を import している: {http_ones}"


def test_the_import_scanner_is_not_vacuous(usecase_trees) -> None:
    """走査器の空振り検定: 実際に import 名を採れている。"""
    collected = {name: _imported_modules(tree) for name, tree in usecase_trees.items()}
    assert any(collected.values()), "import を 1 つも採れていない（走査が空振り）"
    assert "simulator.replay_ui.usecase.port_result" in collected["replay_ports.py"]


# --------------------------------------------------------------------------------------
# 3. Port の戻り注釈が PortResult
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("protocol_name,method_name", _PORT_METHODS,
                         ids=[f"{p}.{m}" for p, m in _PORT_METHODS])
def test_the_port_method_returns_a_port_result(usecase_trees, protocol_name, method_name) -> None:
    """4 つの Port の戻り注釈が ``PortResult``（``tuple[int, dict]`` ではない）。"""
    tree = usecase_trees["replay_ports.py"]
    klass = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == protocol_name), None
    )
    assert klass is not None, f"Protocol が見つからない: {protocol_name}"
    method = next(
        (m for m in klass.body if isinstance(m, ast.FunctionDef) and m.name == method_name), None
    )
    assert method is not None, f"{protocol_name}.{method_name} が見つからない"
    assert method.returns is not None, f"{protocol_name}.{method_name} に戻り注釈が無い"
    assert ast.unparse(method.returns) == "PortResult", ast.unparse(method.returns)


# --------------------------------------------------------------------------------------
# 4. 分類 → ステータスの写像は framework の 1 箇所だけ
# --------------------------------------------------------------------------------------
def test_the_classification_maps_to_the_canonical_status():
    """写像は正典表 ``ERROR_STATUS`` を引くだけ（第 2 の定義を作らない）。"""
    from api_shared.http_contract import ERROR_STATUS

    assert http_response_for(PortResult.success({"ok": True})) == (200, {"ok": True})
    for error_type, status in ERROR_STATUS.items():
        body = {"ok": False, "error": {"type": error_type}}
        assert http_response_for(PortResult.failure(error_type, body)) == (status, body)
    # 未知の分類は 500（nested_error と同じ既定）。
    assert http_response_for(PortResult.failure("no-such-kind", {}))[0] == 500


def test_only_one_place_maps_a_classification_to_a_status() -> None:
    """``ERROR_STATUS`` を引く本番コードは framework の ``http_response_for`` だけである。

    2 箇所目ができると、片方だけが直されて同じ失敗が違う番号で返る。
    """
    framework_dir = Path(inspect.getsourcefile(serve_replay)).parent
    users = sorted(
        p.name for p in framework_dir.glob("*.py")
        if "ERROR_STATUS" in p.read_text(encoding="utf-8")
    )
    assert users == ["serve_replay.py"], users
    source = Path(inspect.getsourcefile(serve_replay)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    holders = sorted(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and "ERROR_STATUS" in _code_without_docstring(node)
    )
    assert holders == ["http_response_for"], holders


def _code_without_docstring(fn: ast.FunctionDef) -> str:
    """関数の**コード**（docstring を除く）を文字列にする。

    散文での言及（例外翻訳器の docstring が正典表を説明している）を「第 2 の写像」と
    誤判定しないため。見たいのは表を**引いている**かであって、説明を禁じることではない。
    """
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


# --------------------------------------------------------------------------------------
# 5. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("files_requested", [2, 5], ids=["parse_2", "parse_5"])
def test_the_usecase_source_is_parsed_once_per_file(files_requested: int) -> None:
    """ファイル 2 本 / 5 本の 2 点で「parse 回数 − ファイル数 = 0」。

    走査のたびに parse し直す（O(n^2)）形になっていないことだけを固定する。
    回数リテラルは焼き込まず、要求したファイル数から導出する。
    """
    # Arrange
    parsed: "list[int]" = []

    def _spy(source, *args, **kwargs):
        parsed.append(len(source))
        return ast.parse(source, *args, **kwargs)

    targets = _usecase_sources()[:files_requested]
    assert len(targets) == files_requested, "usecase のファイル数が足りない（走査対象の前提が崩れた）"
    # Act
    collected = {p.name: _tree_of(p, parse=_spy) for p in targets}
    # Assert
    assert len(collected) == files_requested
    assert len(parsed) - files_requested == 0, (
        f"ファイル {files_requested} 本に対し parse が {len(parsed)} 回発行された"
        "（採取した木を使い回さず作り直して捨てている）"
    )
