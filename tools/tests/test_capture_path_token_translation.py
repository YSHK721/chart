"""``capture_mt5_symbol_spec`` がパス成分規則を **所有せず参照する**ことを固定する（ISSUE-479 F-1）。

なぜこの検定が要るのか:
    規則の実体を ``marketdata.path_tokens`` へ移すと、送出される例外は ``CaptureError``
    （RuntimeError 系）から ``PathTokenError``（ValueError 系）へ変わる。``main()`` は
    ``default_out_path`` 経由で sanitize を呼ぶため、捕捉集合を広げないと
    「[FAIL-STOP] …／終了コード 2」だったものが **素のトレースバック**に退化する。
    例外型の付替えで静かに失われる契約なので、状態ではなく **CLI の出口**で固定する。

配布形態（2 ファイル）:
    本スクリプトはリポジトリごとではなく**ファイル単位で Windows VM へ持ち込む**運用である
    （MT5 端末は VM 側にしかない）。規則を marketdata へ移した以上、VM へは
    ``capture_mt5_symbol_spec.py`` と ``marketdata/path_tokens.py`` の写しの **2 ファイル**を
    同じディレクトリへ置いて配る。スクリプトはリポジトリ内では ``marketdata.path_tokens`` を、
    リポジトリ外（VM 単体）では隣の ``path_tokens.py`` を読む。後者は宣言では守れないので
    **実際に隔離した一時ディレクトリで別プロセス実行して**固定する。
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tools.capture_mt5_symbol_spec as cap
from marketdata import path_tokens

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPTURE_SOURCE = _REPO_ROOT / "tools" / "capture_mt5_symbol_spec.py"
_AUTHORITY_SOURCE = _REPO_ROOT / "marketdata" / "path_tokens.py"

#: 許可文字集合の「相当物」を見分ける印（``test_mt5_equivalence.py`` の判定と同じ発想）。
_CHARSET_MARK = "abcdefghijklmnopqrstuvwxyz"


# ======================================================================================
# 1. 規則の第 2 実装が tools 側に残っていない
# ======================================================================================

def _charset_literals(source: str) -> "list[str]":
    """文字集合リテラル（許可文字の並び）を含む文字列定数を列挙する（走査本体）。"""
    tree = ast.parse(source)
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and _CHARSET_MARK in node.value
    ]


def test_tools_no_longer_owns_a_second_character_set() -> None:
    """``tools/capture_mt5_symbol_spec.py`` は許可文字集合を自前で持たない（参照のみ）。

    識別力: ``_SAFE_CHARS = frozenset("abc…")`` を書き戻すと Red になる。
    """
    # Arrange / Act
    literals = _charset_literals(_CAPTURE_SOURCE.read_text(encoding="utf-8"))
    # Assert
    assert literals == [], (
        f"tools 側に文字集合の第 2 実装が残っています: {literals}。"
        " marketdata.path_tokens.sanitize_path_component の参照にしてください。"
    )


def test_the_charset_scan_has_detection_power() -> None:
    """走査が恒真式に退化していないこと（合成ソースで検出できる）。"""
    snippet = '_SAFE_CHARS = frozenset("' + _CHARSET_MARK + '0123456789._-")\n'
    assert _charset_literals(snippet)


def test_tools_reexports_the_very_same_function_object() -> None:
    """再エクスポートは同一関数オブジェクト（規則の第 2 実装を作らない）。

    ``marketdata/tests/test_mt5_equivalence.py`` と ``test_mt5_ingest.py`` の同一性検定は
    この性質の上に成り立っている（3 者が同じ 1 つの関数を指す）。
    """
    assert cap.sanitize_path_component is path_tokens.sanitize_path_component


def test_the_module_does_not_define_the_sanitizer_itself() -> None:
    """AST 上、``sanitize_path_component`` は capture 側の関数定義として現れない。"""
    tree = ast.parse(_CAPTURE_SOURCE.read_text(encoding="utf-8"))
    defined = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "sanitize_path_component" not in defined


# ======================================================================================
# 2. 例外の所有者移転と CLI の Fail-Stop 契約
# ======================================================================================

def test_default_out_path_raises_the_owner_error_type() -> None:
    """規則の所有者が送出する型で落ちる（tools 側で別の型へ握り替えない）。"""
    with pytest.raises(path_tokens.PathTokenError):
        cap.default_out_path("   ", "JP225")


def test_path_token_error_is_not_a_capture_error() -> None:
    """捕捉集合を広げる必要が実在すること（恒真式でない）を明示的に固定する。"""
    assert not issubclass(path_tokens.PathTokenError, cap.CaptureError)


class _Info:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping

    def _asdict(self) -> dict:
        return dict(self._mapping)


class _FakeTerminal:
    """capture のセッション文脈が要求する最小の端末ダック（server が空白のみ）。"""

    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self):
        return (0, "ok")

    def symbol_select(self, symbol, enable=True) -> bool:  # noqa: ARG002, FBT002
        return True

    def symbol_info(self, symbol):
        return _Info({"name": symbol, "digits": 1, "point": 0.1})

    def account_info(self):
        return _Info({"company": "c", "currency": "JPY", "leverage": 1,
                      "server": "   ", "trade_mode": 0})

    def terminal_info(self):
        return _Info({"company": "c", "name": "n", "build": 1})


def test_bad_server_component_still_exits_with_the_fail_stop_contract(capsys) -> None:
    """パス成分に使えない server でも、トレースバックではなく Fail-Stop で終わる。

    回帰錨: 例外の所有者を移す前後で **CLI の出口が変わらない**ことを固定する。
    """
    # Arrange
    argv = ["--symbol", "JP225"]
    # Act
    rc = cap.main(argv, mt5=_FakeTerminal())
    # Assert
    assert rc == 2
    assert "[FAIL-STOP]" in capsys.readouterr().err


# ======================================================================================
# 3. VM 単体環境（リポジトリ根が sys.path に無い）でも 2 ファイルで動く
# ======================================================================================

_DRIVER = """\
import sys

import capture_mt5_symbol_spec as cap
import path_tokens

# リポジトリ経路を通っていない（＝隣の写しを読んだ）ことの実証。
assert "marketdata" not in sys.modules, "リポジトリ経路が使われました（単体環境の模擬が失敗）"
assert cap.sanitize_path_component is path_tokens.sanitize_path_component

print(cap.sanitize_path_component("OANDA-Japan MT5 Live"))
try:
    cap.default_out_path("   ", "JP225")
except path_tokens.PathTokenError:
    print("PathTokenError")
"""


def _standalone_distribution(tmp_path: Path) -> Path:
    """capture 本体と path_tokens の写しだけを置いた「VM の配布先」を作る。"""
    dest = tmp_path / "vm_dist"
    dest.mkdir()
    shutil.copy2(_CAPTURE_SOURCE, dest / _CAPTURE_SOURCE.name)
    shutil.copy2(_AUTHORITY_SOURCE, dest / "path_tokens.py")
    (dest / "_drive.py").write_text(_DRIVER, encoding="utf-8")
    return dest


def _run_standalone(dest: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, str(dest / "_drive.py")],
        cwd=str(dest), env=env, capture_output=True, text=True, timeout=120,
    )


def test_the_isolated_directory_is_really_outside_any_repository(tmp_path: Path) -> None:
    """前提の検査化: 一時ディレクトリが git リポジトリ配下でない（模擬が成立している）。"""
    dest = _standalone_distribution(tmp_path)
    assert cap.find_repo_root(dest / _CAPTURE_SOURCE.name) is None


def test_two_file_distribution_works_without_the_repository(tmp_path: Path) -> None:
    """capture 本体＋path_tokens の写しの 2 ファイルだけで、規則も Fail-Stop も成立する。

    識別力: 写しを置かずに 1 ファイルだけ配ると ModuleNotFoundError で Red になる
    （``test_one_file_distribution_fails_loudly`` が対照）。
    """
    # Arrange
    dest = _standalone_distribution(tmp_path)
    # Act
    proc = _run_standalone(dest)
    # Assert
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout.splitlines() == ["OANDA-Japan-MT5-Live", "PathTokenError"]


_BROKEN_REPO_DRIVER = """\
import capture_mt5_symbol_spec as cap

print(cap.sanitize_path_component("x"))
"""


def test_a_broken_marketdata_package_is_not_silently_replaced_by_the_copy(tmp_path: Path) -> None:
    """リポジトリ側が壊れているとき、隣の写しへ**黙って退かない**（規則が静かに割れない）。

    経路 1 が ``ModuleNotFoundError`` になる原因は 2 通りある: (a) そこが本 repo ではない
    （marketdata が無い）＝経路 2 へ進んでよい、(b) marketdata は在るのにその依存が壊れている
    ＝進んではいけない。(b) で退くと、リポジトリ内で作業しているのに隣の写し（更新が遅れて
    いるかもしれない実体）が使われ、しかも本当の原因が握り潰される。
    """
    # Arrange — .git を持ち、marketdata は在るが依存が欠けている「壊れた repo」を作る。
    dest = _standalone_distribution(tmp_path)
    (dest / ".git").mkdir()
    pkg = dest / "marketdata"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("import definitely_absent_dependency\n", encoding="utf-8")
    shutil.copy2(_AUTHORITY_SOURCE, pkg / "path_tokens.py")
    (dest / "_drive.py").write_text(_BROKEN_REPO_DRIVER, encoding="utf-8")

    # Act
    proc = _run_standalone(dest)

    # Assert — 本当の原因が表に出る（隣の写しで動いてしまわない）。
    assert proc.returncode != 0, f"壊れた repo なのに隣の写しで動いています: {proc.stdout!r}"
    assert "definitely_absent_dependency" in proc.stderr, proc.stderr


def test_a_partially_installed_marketdata_is_not_silently_replaced_by_the_copy(
    tmp_path: Path,
) -> None:
    """規則モジュールだけが欠けた repo でも、隣の写しへ**黙って退かない**（case (b)）。

    ``test_a_broken_marketdata_package_is_not_silently_replaced_by_the_copy`` は
    ``marketdata/__init__.py`` の実行が失敗する形（欠落の名前は ``marketdata`` でない）を扱う。
    本ケースはより狭く危険な形である: ``marketdata`` パッケージは健全に import でき、
    ``marketdata/path_tokens.py`` **だけ**が欠けている。このとき欠落の名前は
    ``marketdata.path_tokens`` であり、**先頭成分は ``marketdata`` と一致する**。退避条件を
    先頭成分の一致で書くと、本ケースは「そこが本 repo ではない」と誤判定されて隣の写しへ退き、
    規則が静かに割れたまま成功してしまう（docstring の契約
    「marketdata は在るのに依存が壊れている場合は退かない」との不一致）。

    識別力: 退避条件を ``exc.name`` の完全一致から先頭成分の一致へ緩めると Red になる。
    """
    # Arrange — .git を持ち、marketdata は健全に import できるが path_tokens だけ無い repo。
    dest = _standalone_distribution(tmp_path)
    (dest / ".git").mkdir()
    pkg = dest / "marketdata"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (dest / "_drive.py").write_text(_BROKEN_REPO_DRIVER, encoding="utf-8")

    # Arrange の前提を検査化: 退避先（隣の写し）は実在する。無い状態で落ちても意味がない。
    assert (dest / "path_tokens.py").exists()
    assert not (pkg / "path_tokens.py").exists()

    # Act
    proc = _run_standalone(dest)

    # Assert — 隣の写しで動かず、欠けている当のものを名指して落ちる。
    assert proc.returncode != 0, f"隣の写しへ黙って退きました: {proc.stdout!r}"
    assert "ModuleNotFoundError" in proc.stderr, proc.stderr
    assert "marketdata.path_tokens" in proc.stderr, proc.stderr


def test_one_file_distribution_fails_loudly(tmp_path: Path) -> None:
    """写しを配り忘れたら **黙って別規則で動かず**、import 時に落ちる（負の対照）。"""
    # Arrange
    dest = _standalone_distribution(tmp_path)
    (dest / "path_tokens.py").unlink()
    # Act
    proc = _run_standalone(dest)
    # Assert
    assert proc.returncode != 0
    assert "ModuleNotFoundError" in proc.stderr, proc.stderr
    assert "path_tokens" in proc.stderr


# ======================================================================================
# 4. 計算量検定（Test Spy・発行 − 使用 = 0）
# ======================================================================================

def _spy_on_sanitize(monkeypatch) -> "list[str]":
    calls: "list[str]" = []
    original = cap.sanitize_path_component

    def _spy(raw):
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(cap, "sanitize_path_component", _spy)
    return calls


def test_default_out_path_issues_one_conversion_per_path_component(monkeypatch) -> None:
    """発行した変換 − 出力パスの被変換成分数 = 0（作って捨てる変換が無い）。"""
    # Arrange
    calls = _spy_on_sanitize(monkeypatch)
    # Act
    out = cap.default_out_path("OANDA-Japan MT5 Live", "JP225")
    # Assert（期待値は出力から導出する。定数 2 を焼き込まない）
    converted = (out.parent.name, out.stem)
    assert len(calls) - len(converted) == 0


def test_default_out_path_issue_count_does_not_grow_with_input_length(monkeypatch) -> None:
    """入力長 8 → 64 の 2 点で発行数が変わらない（発行は成分数だけで決まる）。"""
    # Arrange / Act
    measured = {}
    for length in (8, 64):
        calls = _spy_on_sanitize(monkeypatch)
        out = cap.default_out_path("S" * length, "V" * length)
        measured[length] = (len(calls), len((out.parent.name, out.stem)))
    # Assert
    assert measured[8][0] == measured[64][0], f"入力長で発行数が変わりました: {measured}"
    for length, (issued, used) in measured.items():
        assert issued - used == 0, length


def _spy_on_repo_root(monkeypatch) -> "list[Path]":
    calls: "list[Path]" = []
    original = cap.find_repo_root

    def _spy(start):
        calls.append(start)
        return original(start)

    monkeypatch.setattr(cap, "find_repo_root", _spy)
    return calls


def test_repo_root_probes_are_issued_once_per_produced_path(monkeypatch) -> None:
    """出力 1 → 4 の 2 点で「発行した根探索 − 出力したパス数 = 0」（オーダーの表明）。

    根探索は ``.git`` を求めて親方向へ走るファイルシステム走査であり、規則の解決経路
    （``_load_path_tokens`` / ``_snapshot_base_dir``）が共有する唯一の重い手続きである。
    ここが出力量以外の何か（成分数・入力長・呼出のたびの重複探索）で増えると、作った端から
    捨てる走査が入り込む。期待値は**出力から導出**し、回数そのものは焼き込まない。
    """
    # Arrange / Act
    measured = {}
    for produced in (1, 4):
        calls = _spy_on_repo_root(monkeypatch)
        outs = [cap.default_out_path(f"S{i}", f"V{i}") for i in range(produced)]
        measured[produced] = (len(calls), len(outs))
    # Assert
    for produced, (issued, used) in measured.items():
        assert issued - used == 0, f"出力 {produced} 件で余分な根探索: {measured}"
