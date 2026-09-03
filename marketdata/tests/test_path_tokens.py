"""パス成分変換規則の所有権を marketdata 側へ反転したことを検定で固定する（ISSUE-479 F-1）。

なぜ所有権を移すのか（循環 C-1 の根治）:
    銘柄・サーバ名 → パス成分の変換規則は ``tools/capture_mt5_symbol_spec.py`` にあり、
    最下層である ``marketdata/mt5_ticks/ingest.py`` がそれを import していた（層の逆流）。
    実害は例外型でも出ていた: sanitize が送出する ``CaptureError`` は ``tools`` の型なので、
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

_ROOT = Path(__file__).resolve().parents[2]
_AUTHORITY = _ROOT / "marketdata" / "path_tokens.py"


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
