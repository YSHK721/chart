"""パス成分変換規則の所有権を marketdata 側へ反転したことを検定で固定する（ISSUE-479 F-1）。

なぜ所有権を移すのか（循環 C-1 の根治）:
    銘柄・サーバ名 → パス成分の変換規則は ``tools/capture_mt5_symbol_spec.py`` にあり、
    最下層である ``marketdata/mt5_ticks/ingest.py`` がそれを import していた（層の逆流）。
    実害は例外型でも出ていた: sanitize が送出する CaptureError は tools の型なので、
    ``tools/mt5_tick_watch.py`` の捕捉集合（SupplyUnavailable / Mt5SupplyError / WireError）を
    すり抜け、周期処理がトレースバックで exit 1 になっていた。

本 Wave の解:
    規則の実体を **依存ゼロ**の ``marketdata/path_tokens.py`` へ移し、``tools`` 側は同一関数
    オブジェクトを再エクスポートする（第 2 実装を作らない＝既存の同一性検定を壊さない）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketdata import path_tokens
from marketdata.mt5_ticks import ingest
from marketdata.mt5_ticks.port import Mt5SupplyError

_ROOT = Path(__file__).resolve().parents[2]
_AUTHORITY = _ROOT / "marketdata" / "path_tokens.py"
_INGEST = _ROOT / "marketdata" / "mt5_ticks" / "ingest.py"


# --------------------------------------------------------------------------------------
# (a) 権威モジュールは依存ゼロ
# --------------------------------------------------------------------------------------
def test_path_tokens_has_no_imports() -> None:
    """``marketdata/path_tokens.py`` は import 文を 1 つも持たない（最下層の中立核）。

    識別力: ここに import を 1 つ足すと Red になる。最下層（``mt5_ticks``）から参照できる
    ことが移設の目的であり、依存が入るとその目的が崩れる。
    """
    # Arrange
    tree = ast.parse(_AUTHORITY.read_text(encoding="utf-8"))
    # Act
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    # Assert
    assert imports == [], (
        "marketdata/path_tokens.py に import 文があります。"
        " ここが何かに依存すると、最下層から参照するという移設の目的が崩れます。"
    )


def test_the_import_scan_has_detection_power() -> None:
    """走査が恒真式に退化していないこと（合成ソースで検出できる）。"""
    tree = ast.parse("from __future__ import annotations\nimport os\n")
    found = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert len(found) == 2


# --------------------------------------------------------------------------------------
# 変換規則そのもの（移設前と 1 文字も変わらないこと）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("OANDA-Japan MT5 Live", "OANDA-Japan-MT5-Live"),
        ("Broker.com-Demo", "Broker.com-Demo"),
        ("a/b\\c", "a-b-c"),
        ("A:B*C?D", "A-B-C-D"),
        ("日本 Live", "---Live"),
        ("Trim  Me", "Trim--Me"),
    ],
)
def test_sanitize_rule_is_byte_identical_to_the_previous_owner(raw, expected) -> None:
    """移設は挙動を変えない（旧所有者 ``tools`` の検定表と同一の入出力対）。"""
    assert path_tokens.sanitize_path_component(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", ".", ".."])
def test_sanitize_rejects_components_that_would_escape_the_parent(bad) -> None:
    """境界値: 空・空白のみ・``.``・``..`` は親ディレクトリへ逃げる経路なので中断する。"""
    with pytest.raises(path_tokens.PathTokenError):
        path_tokens.sanitize_path_component(bad)


def test_sanitize_preserves_length_one_character_at_a_time() -> None:
    """置換は 1 文字 → 1 文字（長さを変えない＝トークンの見た目が黙って縮まない）。"""
    raw = "A B/C:D"
    assert len(path_tokens.sanitize_path_component(raw)) == len(raw)


def test_path_token_error_is_a_value_error() -> None:
    """``PathTokenError`` は入力値の異常であり ValueError 系（RuntimeError ではない）。

    層ごとの失敗型（``Mt5SupplyError`` 等）へ翻訳するのは上位の責務であり、権威側は
    「入力値が規則を満たさない」だけを表明する。
    """
    assert issubclass(path_tokens.PathTokenError, ValueError)


# --------------------------------------------------------------------------------------
# (b) 最下層は規則を marketdata から取る（tools への逆流ゼロ）
# --------------------------------------------------------------------------------------
def _imported_modules(path: Path) -> "set[str]":
    """``path`` の絶対 import 文からモジュール名の集合を返す（関数内 import も含む）。"""
    out: "set[str]" = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def test_ingest_takes_the_rule_from_marketdata_not_from_tools() -> None:
    """``ingest`` は規則を最下層から取る（``tools`` への逆流＝循環 C-1 の辺が消える）。

    識別力: ``from tools.capture_mt5_symbol_spec import sanitize_path_component`` に戻すと Red。
    """
    modules = _imported_modules(_INGEST)
    assert "marketdata.path_tokens" in modules
    assert not {m for m in modules if m.split(".")[0] == "tools"}, (
        f"ingest が tools を import しています: {sorted(modules)}"
    )


def test_all_three_holders_share_one_function_object() -> None:
    """権威・``tools`` 側の再エクスポート・``ingest`` が同じ 1 つの関数を指す。"""
    from tools.capture_mt5_symbol_spec import sanitize_path_component as reexported

    assert reexported is path_tokens.sanitize_path_component
    assert ingest.sanitize_path_component is path_tokens.sanitize_path_component


# --------------------------------------------------------------------------------------
# (c) token_for の例外翻訳（port.py の Fail-Stop 契約へ載せる）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", ".", ".."])
def test_token_for_translates_a_bad_symbol_into_the_supply_error(bad) -> None:
    """不正な symbol は ``Mt5SupplyError``（周期処理が捕捉できる型）で落ちる。

    ``tools/mt5_tick_watch.py`` の捕捉集合は SupplyUnavailable / Mt5SupplyError /
    wire.WireError である。翻訳しないと ValueError 系がすり抜け、周期処理が
    トレースバックで exit 1 になる（これが循環 C-1 の実害だった）。
    """
    with pytest.raises(Mt5SupplyError):
        ingest.token_for(bad, "OANDA-Japan-MT5-Live")


@pytest.mark.parametrize("bad", ["", "   ", ".", ".."])
def test_token_for_translates_a_bad_server_into_the_supply_error(bad) -> None:
    """server 側も同じ契約（片側だけ翻訳する非対称を作らない）。"""
    with pytest.raises(Mt5SupplyError):
        ingest.token_for("JP225", bad)


def test_token_for_keeps_the_original_cause_attached() -> None:
    """翻訳は原因を捨てない（``__cause__`` に PathTokenError が残る）。"""
    with pytest.raises(Mt5SupplyError) as ei:
        ingest.token_for("..", "OANDA-Japan-MT5-Live")
    assert isinstance(ei.value.__cause__, path_tokens.PathTokenError)


def test_token_for_still_builds_the_expected_token() -> None:
    """正常系は移設前と同一（挙動不変）。"""
    assert ingest.token_for("JP225", "OANDA-Japan MT5 Live") == "JP225@OANDA-Japan-MT5-Live"


# --------------------------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def _spy_on_sanitize(monkeypatch) -> "list[str]":
    calls: "list[str]" = []
    original = ingest.sanitize_path_component

    def _spy(raw):
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(ingest, "sanitize_path_component", _spy)
    return calls


def test_token_for_issues_exactly_one_conversion_per_token_component(monkeypatch) -> None:
    """発行した変換 − 出力トークンの成分数 = 0（作って捨てる変換が無い）。"""
    # Arrange
    calls = _spy_on_sanitize(monkeypatch)
    # Act
    token = ingest.token_for("JP225", "OANDA-Japan MT5 Live")
    # Assert（期待値は出力から導出する。定数 2 を焼き込まない）
    components = len(token.split(ingest.TOKEN_SEPARATOR))
    assert len(calls) - components == 0


def test_token_for_issue_count_does_not_grow_with_input_length(monkeypatch) -> None:
    """入力長 8 → 64 の 2 点で発行数が変わらない（発行は成分数だけで決まる）。"""
    # Arrange / Act
    measured = {}
    for length in (8, 64):
        calls = _spy_on_sanitize(monkeypatch)
        token = ingest.token_for("S" * length, "V" * length)
        measured[length] = (len(calls), len(token.split(ingest.TOKEN_SEPARATOR)))
    # Assert
    assert measured[8][0] == measured[64][0], f"入力長で発行数が変わりました: {measured}"
    for length, (issued, components) in measured.items():
        assert issued - components == 0, length


def test_the_conversion_spy_would_detect_a_wasteful_implementation(monkeypatch) -> None:
    """検出力: 捨てられる変換を 1 つ足すと、この測り方で必ず落ちる（恒真式ではない）。"""
    # Arrange
    calls = _spy_on_sanitize(monkeypatch)
    spy = ingest.sanitize_path_component

    def _wasteful(symbol, server):
        spy(symbol)                       # 発行するが出力に使わない（浪費）
        return spy(symbol) + ingest.TOKEN_SEPARATOR + spy(server)

    # Act
    token = _wasteful("JP225", "OANDA-Japan MT5 Live")
    # Assert
    assert len(calls) - len(token.split(ingest.TOKEN_SEPARATOR)) != 0
