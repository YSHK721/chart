"""共有フロント供給根の台帳が**単一の所有者**であることを強制する（ISSUE-502 C-4 3b）。

## 何を守るか

``indigators/<pkg>/web/js`` に実体を置く共有フロントパッケージの一覧は、``common.shared_web_roots``
だけが持つ。この一覧は静的配信の**許可根**に直結し、載っていないサブツリーは URL が同じでも
``realpath`` が許可根の外へ出て 404 になる。

## なぜ検定が要るか（実測 2026-09-06）

この一覧は元々 2 箇所へ手書きされていた（ライブ殻の ``_MP_WEB_JS_ROOT`` と replay 側
StaticFileServer._allowed_roots）。C-4 3b の第 1 回は共有カーネルを新設したのに列挙を
足さなかったため、移設した 6 本が**実測で 404** になり中断・復元となった。宣言（コメント）
では再発を止められないので、第 2 の列挙が復活したら Red になる検定で機械的に塞ぐ。

## 何を「第 2 の列挙」とみなすか

配信殻のコードに ``... / "web" / "js"`` という**連続した join**（＝共有フロント供給根を
自前で導出する形）が現れることをもって違反とする。名前（``"market_profile"`` 等）の出現では
判定しない——ライブ殻には ``_API_ROOT.parents[1] / "market_profile" / "api"`` という
**import パスの結線**（配信とは無関係）が正当に存在し、名前で測ると誤検出になる（下の
検出器自己検定が両方を逐語で固定する）。判定は AST で行うのでコメント・docstring の散文は
対象外である（散文で概念を説明することは禁じない）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from common.shared_web_roots import SHARED_WEB_PACKAGES, shared_web_js_roots

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 走査対象を**構造で見つける**ための印。
#:
#: 配信殻を名前で列挙すると、3 枚目の殻が生えた日に検定が黙って素通りする（列挙の取り残しは
#: 本リポジトリの既往型）。静的配信の許可根を持つ殻の定義的な行為は「resolve() 後の実パスが
#: 許可根の配下かを区切り境界一致で判定する」ことであり、その唯一の書き方が Path.is_relative_to
#: である。よってこの印を持つ**本番**モジュールを走査対象として発見する。
#: 実測（2026-09-07）: 本番側でこの印を持つのはライブ殻と共有静的配信クラスの 2 枚だけである。
_SHELL_MARK = "is_relative_to"

#: 走査から外す経路（試作・依存物・テスト自身）。テストは印を持っていても配信殻ではない。
_SKIP_PARTS = frozenset({"node_modules", ".git", ".venv", "tests", "__pycache__"})

#: 発見が壊れていないことの錨（この 2 枚は必ず見つかる）。増えた 3 枚目は自動で対象に入る。
_KNOWN_SHELLS = (
    "indigators/indicator_ui/api/framework/server.py",
    "simulator/replay_ui/framework/static_file_server.py",
)


def _is_in_scope(rel: Path) -> bool:
    """本番の Python モジュールか（試作・依存物・テストを除く）。"""
    parts = rel.parts
    if any(part in _SKIP_PARTS for part in parts):
        return False
    if parts[0].startswith("prototype_") or parts[0].startswith("."):
        return False
    if parts[0] == "lightweight-charts-python-main":
        return False
    return rel.name.startswith("test_") is False


def _discover_shells() -> "dict[str, str]":
    """許可根の境界判定を行う本番モジュールを発見し、``相対パス -> ソース`` で返す。

    1 ファイルにつき読取は 1 回だけ（同じ木を検査項目ごとに読み直さない）。
    """
    found: "dict[str, str]" = {}
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT)
        if not _is_in_scope(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _SHELL_MARK in source:
            found[rel.as_posix()] = source
    return found


#: 走査は 1 回だけ行い、以下の全検定で共有する（読み直さない）。
_SHELLS = _discover_shells()


def _web_js_join_offenders(source: str) -> "list[int]":
    """``<何か> / "web" / "js"`` という連続 join の行番号を返す（自前導出の検出）。"""
    offenders: "list[int]" = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        right = node.right
        left = node.left
        if not (isinstance(right, ast.Constant) and right.value == "js"):
            continue
        if not (isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div)):
            continue
        inner = left.right
        if isinstance(inner, ast.Constant) and inner.value == "web":
            offenders.append(node.lineno)
    return offenders


# --------------------------------------------------------------------------- #
# 台帳そのもの
# --------------------------------------------------------------------------- #
def test_ledger_is_not_empty() -> None:
    """台帳が空なら以下の検定はすべて恒真式に退化する（検査の生存確認）。"""
    assert len(SHARED_WEB_PACKAGES) > 0, "台帳が空＝許可根の導出が何も出さない"


def test_derivation_is_the_web_js_subtree_of_each_package() -> None:
    """導出は ``<root>/<pkg>/web/js``（最小権限＝web 根全体を許可しない）。"""
    root = Path("/somewhere/indigators")
    assert shared_web_js_roots(root) == tuple(
        root / name / "web" / "js" for name in SHARED_WEB_PACKAGES
    )


@pytest.mark.parametrize("js_root", shared_web_js_roots(_REPO_ROOT / "indigators"))
def test_each_declared_package_actually_supplies_a_web_js_tree(js_root: Path) -> None:
    """台帳の各行は本チェックアウトに実在する（改名・撤去の取り残しを検出する）。

    実在しない行を放置すると許可根に「存在しない根」が混ざり、台帳が実態と食い違ったまま
    静かに通り続ける（許可根は多い分には 404 を出さないので、症状が出ない）。
    """
    assert js_root.is_dir(), f"台帳の行に対応する供給根が無い: {js_root}"


# --------------------------------------------------------------------------- #
# 第 2 の列挙の禁止（再発防止の本体）
# --------------------------------------------------------------------------- #
def test_shell_discovery_is_not_vacuous() -> None:
    """発見が空振りしていない（既知の 2 枚が構造から見つかる）。

    ここが落ちるのは、殻が移動・改名されたか、境界判定の書き方が変わったときである。
    そのときは以下の 2 検定が「対象 0 件」で静かに通ってしまうので、先に赤で止める。
    """
    for known in _KNOWN_SHELLS:
        assert known in _SHELLS, (
            f"既知の配信殻が発見できない: {known}（移動・改名か、境界判定の書き方が変わった）"
        )


@pytest.mark.parametrize("rel", _KNOWN_SHELLS)
def test_serving_shell_derives_the_roots_from_the_ledger(rel: str) -> None:
    """共有フロントを配る殻は台帳を import して導出する（値を持たない）。

    ここだけ**既知の 2 枚に限る**理由: 「共有フロントを配る」かどうかは構造から判定できない。
    印（Path.is_relative_to）は境界判定の存在しか示さず、自分の根だけを配る殻——共有パッケージ
    を一切扱わない殻——にも同じ印は付く。そこへ台帳の import を強いるのは誤検出であり、
    無関係なモジュールに配信の台帳を持たせる誤った指示になる。

    第 3 の殻が生えたときに実際に事故を起こすのは「台帳を読まないこと」ではなく
    「**自前で列挙すること**」であり、そちらは下の検定が発見された全モジュールに対して
    測る（＝取り残しの穴は塞がっている）。
    """
    assert "shared_web_js_roots" in _SHELLS[rel], (
        f"{rel} が台帳（common.shared_web_roots）から導出していない"
    )


@pytest.mark.parametrize("rel", sorted(_SHELLS))
def test_serving_shell_has_no_second_enumeration(rel: str) -> None:
    """配信殻に共有フロント供給根の自前導出が無い（列挙の所有者は台帳ただ 1 つ）。"""
    offenders = _web_js_join_offenders(_SHELLS[rel])
    assert offenders == [], (
        f"{rel} が共有フロント供給根を自前で導出している（行 {offenders}）。"
        "台帳 common.shared_web_roots.SHARED_WEB_PACKAGES へ足し、ここでは導出しないこと"
        "（2 重所有だと片方だけ足した日にその core だけ 404 になる・2026-09-06 実測）。"
    )


def test_discovery_reads_each_file_at_most_once() -> None:
    """計算量: 発見の 1 巡で同じファイルを読み直さない（読取 − 相異なるファイル = 0）。

    検査項目（発見された殻の数）が増えても走査は 1 回である——上の 2 検定が
    ``_SHELLS`` を共有するのはそのためで、項目ごとに rglob し直す実装だと木全体の読取が
    項目数倍に増える（出力は正しいまま静かに重くなる型）。
    """
    reads: "list[str]" = []
    original_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        reads.append(str(self))
        return original_read_text(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", counting_read_text)
        discovered = _discover_shells()
    assert len(reads) - len(set(reads)) == 0, "同じファイルを読み直している"
    assert discovered.keys() == _SHELLS.keys(), "発見結果が走査ごとに変わる（非決定）"


# --------------------------------------------------------------------------- #
# 検出器の自己検定 — 検出する側と誤検出しない側を同じ形で固定する
# --------------------------------------------------------------------------- #
def test_detector_flags_a_hand_written_supply_root() -> None:
    """撤去した旧形を逐語で再現し、実際に捕捉することを示す。"""
    old_shape = 'MP = SHARED.parents[1] / "market_profile" / "web" / "js"\n'
    assert _web_js_join_offenders(old_shape) == [1], "旧形（自前導出）を捕捉できていない"


def test_detector_does_not_flag_the_import_path_wiring() -> None:
    """ライブ殻に正当に在る import パス結線を誤検出しない（名前で測っていない証拠）。"""
    wiring = '_MP_API_ROOT = _API_ROOT.parents[1] / "market_profile" / "api"\n'
    assert _web_js_join_offenders(wiring) == [], "配信と無関係な結線を誤検出している"


def test_detector_ignores_prose_mentioning_the_package_names() -> None:
    """散文（docstring・コメント）でパッケージ名に触れることは違反ではない。"""
    prose = '"""market_profile/web/js の実体を配る。"""\n# chart_kernel/web/js も同様。\n'
    assert _web_js_join_offenders(prose) == [], "散文を違反として数えている"
