"""``run_watch`` の本番呼出が致命型の集合を必ず明示する（ISSUE-511 段階 3 の段階 4・V-4）。

用語（初出定義）:
    致命型の集合
        ＝ :func:`common.watch_loop.run_watch` の ``fatal=`` 引数へ渡す例外型のタプル。待っても
          直らない失敗（何がそれかは呼出側が決める）を、次インターバルへ進めないための通過口である。
    渡し忘れ
        ＝ 本番の呼出が ``fatal=`` を省略すること。既定は空タプルで ``except ()`` はどの例外とも
          一致しないため、省略しても構文誤りにも実行時エラーにもならず、致命の失敗が包括
          ``except Exception`` に握られて WARNING へ格下げされる。

なぜ構文木で固定するのか:
    既定値が安全側に倒れない形（空タプル＝握り潰しての継続）であるため、渡し忘れは実行時に
    何の症状も出さない。観測できるのは呼出の**形**だけである。「渡すこと」を文章で決めても、
    守られたかどうかを機械が確かめられない規約は守られない。よって宣言ではなく走査で強制する。

本検定が固定するもの:
  A-1 本番の ``run_watch`` 呼出はすべて ``fatal=`` を明示する。
  A-2 走査が本番の呼出点に実際に届いている（空振り防止・呼出点の台帳）。
  A-3 走査に検出力がある（``fatal=`` を落とした形を検出し、明示した形は検出しない）。
  A-4 渡し忘れが実行時には無症状であること（＝構文木で固定するほかない理由の実証）。
  CX  計算量（Test Spy・発行 − 使用 = 0・規模 2 点・回数は期待値に焼き込まない）。
      **射程**: 継ぎ目は本検定の読込点であり、測るのは**走査自身のコスト**である
      （本番の発行回数ではない — 下の CX 節に理由）。

対象コードを import せず構文木だけを読む（常駐・サーバは起動しない・ネットワークを叩かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from common.watch_loop import run_watch

#: リポジトリ根（このファイル: <repo>/common/tests/ → parents[2]）。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 走査から外すディレクトリ名。検定・生成物・他ブランチの複製・vendored ライブラリは本番ではない。
#: ``.claude`` を外すのは作業ブランチの完全な複製が並ぶためである（他ブランチの呼出を拾わない）。
_SKIP_PARTS = frozenset({
    ".git", "__pycache__", ".claude", "venv", ".venv", "node_modules",
    "lightweight-charts-python-main", "tests",
})

#: 本番で ``run_watch`` を呼ぶモジュール（常駐 2 本）の台帳。
#:
#: 閉じた集合にしてあるのは、新しい常駐が「致命型を宣言しないまま」生えることを防ぐためである。
#: 呼び手を増やすときは、その呼出へ ``fatal=`` を書いたうえでここへも足す（2 箇所を同時に
#: 触らせることで、宣言のない常駐が黙って増えない）。
_KNOWN_CALLERS = frozenset({
    "indigators/indicator_ui/tools/export_jp225_m1.py",
    "tools/live_tick_watch.py",
})


class _Fatal(RuntimeError):
    """待っても直らない失敗（本検定専用の型・本番の型に依存しない）。"""


def _production_sources() -> "list[Path]":
    """本番コード（検定・複製・vendored を除く ``*.py``）を**再帰的に**列挙する。"""
    return sorted(
        p for p in _REPO_ROOT.rglob("*.py")
        if not (_SKIP_PARTS & set(p.relative_to(_REPO_ROOT).parts))
    )


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return Path(path).read_text(encoding="utf-8")


def _label(path: Path) -> str:
    """表示名（リポジトリ内は根からの相対・根の外＝合成ファイルはファイル名）。"""
    path = Path(path)
    return (
        path.relative_to(_REPO_ROOT).as_posix()
        if path.is_relative_to(_REPO_ROOT) else path.name
    )


def _call_name(node: ast.Call) -> str:
    """呼出の名前（``run_watch(...)`` も ``watch_loop.run_watch(...)`` も同じ名前で見る）。"""
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _run_watch_calls_in(source: str) -> "list[tuple[int, bool]]":
    """``source`` 内の ``run_watch`` 呼出を（行番号, ``fatal=`` を明示したか）で返す。"""
    return [
        (node.lineno, any(kw.arg == "fatal" for kw in node.keywords))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _call_name(node) == "run_watch"
    ]


def _calls_over(files, read=None) -> "list[tuple[str, int, bool]]":
    """与えられたファイル群を走査する。**1 ファイルにつき読込 1 回**（免除リストを持たない）。"""
    reader = read or _read_source
    found: "list[tuple[str, int, bool]]" = []
    for path in files:
        for lineno, has_fatal in _run_watch_calls_in(reader(path)):
            found.append((_label(path), lineno, has_fatal))
    return found


def _synthetic_sources(root: Path, count: int) -> "list[Path]":
    """``count`` 件の合成ソースを置く（1 ファイルにつき ``run_watch`` 呼出 1 つ）。"""
    out: "list[Path]" = []
    for i in range(count):
        path = root / f"s{count}_{i}.py"
        path.write_text("run_watch(_update, interval=60, fatal=())\n", encoding="utf-8")
        out.append(path)
    return out


# =====================================================================
# A-1 本番の呼出はすべて致命型の集合を明示する
# =====================================================================
def test_every_production_call_of_run_watch_names_its_fatal_types() -> None:
    """A-1: 渡し忘れ 0 件（既定の空タプルへ黙って落ちる呼出が無い）。"""
    # Arrange / Act
    silent = [
        f"{label}:{lineno}"
        for label, lineno, has_fatal in _calls_over(_production_sources())
        if not has_fatal
    ]

    # Assert
    assert silent == [], (
        "run_watch の本番呼出が fatal= を渡していません: " + ", ".join(silent)
        + "。既定の空タプルはどの例外とも一致しないため、致命の失敗が包括 except に握られ"
        " WARNING へ格下げされ、同じ失敗を繰り返したまま回り続けます。渡すべき型が無いときも"
        "空を明示的に渡し、「知らないから既定」と「知ったうえで空」を区別してください。"
    )


# =====================================================================
# A-2 空振り防止（走査が本番の呼出点に届いている）
# =====================================================================
def test_the_scan_reaches_the_known_production_callers() -> None:
    """A-2: 走査が拾った呼び手の集合が、常駐 2 本の台帳と一致する。

    一致を要求するのは両方向のためである。減れば走査が壊れた（または常駐が消えた）こと、
    増えれば致命型を宣言しない常駐が生えたことを、どちらも 1 つの assert で捉える。
    """
    # Arrange / Act
    callers = {label for label, _lineno, _has_fatal in _calls_over(_production_sources())}

    # Assert
    assert callers == set(_KNOWN_CALLERS)


# =====================================================================
# A-3 走査の検出力（恒真式に退化していない）
# =====================================================================
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("run_watch(_u, interval=60)\n", [(1, False)], id="omitted"),
        pytest.param("run_watch(_u, interval=60, fatal=())\n", [(1, True)], id="explicit_empty"),
        pytest.param("run_watch(_u, interval=60, fatal=_f())\n", [(1, True)], id="explicit_types"),
        pytest.param("watch_loop.run_watch(_u, interval=1)\n", [(1, False)], id="attribute_call"),
        pytest.param("run_watch(_u, interval=1, **kw)\n", [(1, False)], id="star_star_is_not_explicit"),
        pytest.param("other_call(_u, interval=1)\n", [], id="not_run_watch"),
    ],
)
def test_the_scan_has_detection_power(source: str, expected) -> None:
    """A-3: 合成した呼出で「落とした形」を検出し、「明示した形」を検出しない。

    ``**kw`` を明示と認めないのは、何が入るかが呼出の形からは読めないためである
    （読めない宣言は宣言ではない）。
    """
    # Arrange / Act / Assert
    assert _run_watch_calls_in(source) == expected


# =====================================================================
# A-4 渡し忘れは実行時に無症状（＝構文木で固定するほかない理由）
# =====================================================================
def test_omitting_the_fatal_types_is_silent_at_run_time() -> None:
    """A-4: 同じ致命の失敗が、渡し忘れた呼び方では握られて周期を回し切り 0 を返す。

    渡した呼び方では最初の周期で送出したまま抜ける。差が戻り値にも例外にも出ない側
    （渡し忘れ）は、運用者からは正常稼働と区別できない。
    """
    # Arrange
    swallowed: "list[int]" = []
    surfaced: "list[int]" = []

    def _without_fatal() -> None:
        swallowed.append(len(swallowed))
        raise _Fatal("待っても直らない失敗")

    def _with_fatal() -> None:
        surfaced.append(len(surfaced))
        raise _Fatal("待っても直らない失敗")

    # Act
    rc = run_watch(_without_fatal, interval=1, sleep_fn=lambda _s: None, stop_after=3)
    with pytest.raises(_Fatal):
        run_watch(_with_fatal, interval=1, sleep_fn=lambda _s: None, stop_after=3,
                  fatal=(_Fatal,))

    # Assert
    assert (rc, swallowed) == (0, [0, 1, 2])   # 渡し忘れ: 回し切り、戻り値も正常
    assert surfaced == [0]                     # 明示: 最初の周期で抜ける


# =====================================================================
# CX 計算量（継ぎ目 _read_source・発行 − 使用 = 0・規模 2 点・射程は走査自身のコスト）
# =====================================================================
def test_every_production_source_is_read_exactly_once_by_the_scan() -> None:
    """CX: 読込集合 == 走査対象の集合（読み捨ても二度読みも無い＝発行 − 使用 = 0）。

    **射程（継ぎ目が検定側に在ることの明示）**: 継ぎ目は本検定の読込点 :func:`_read_source` で
    あり、測っているのは**走査自身のコスト**であって**本番の発行回数ではない**。本検定は対象を
    import も実行もせず構文木だけを読むため（モジュール docstring「対象コードを import せず」）、
    測れる本番側の実行時発行がそもそも無い。したがって本番側の浪費は本検定では原理的に検出
    できない — それは各本番経路の計算量検定が担う。
    """
    # Arrange
    reads: "list[Path]" = []

    # Act
    _calls_over(_production_sources(), read=lambda p: (reads.append(p), _read_source(p))[1])

    # Assert
    used = _production_sources()
    assert len(reads) - len(used) == 0
    assert set(reads) == set(used)
    assert len(set(reads)) - len(reads) == 0


@pytest.mark.parametrize("count", [4, 8])
def test_the_read_count_is_determined_by_the_file_count_alone(tmp_path, count: int) -> None:
    """CX（2 点）: 走査対象 4 件 / 8 件で「読込数 == ファイル数」（オーダーの表明）。

    回数そのものは期待値に焼き込まない（期待値は与えたファイル数から導く）。
    射程は上の CX と同じである: 測るのは**走査自身のコスト**であって本番の発行回数ではない。
    """
    # Arrange
    files = _synthetic_sources(tmp_path, count)
    reads: "list[Path]" = []

    # Act
    found = _calls_over(files, read=lambda p: (reads.append(p), _read_source(p))[1])

    # Assert
    assert len(reads) - count == 0
    assert len(found) - count == 0      # 呼出の取りこぼしも無い（1 ファイル 1 呼出）
