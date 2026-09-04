"""試作 prep_tick_rollup が本番 M1 CSV を上書きできないこと（ISSUE-479 Wave2 1-E → Wave2b）。

固定する仕様（反転後）:
    prototype_260626-01 の M1 ロールアップ試作（prep_tick_rollup）は**存在しない**。
    M1 原子 CSV を作る経路は後継 `tools/build_tick_rollup.py` ただ 1 つである。
    削除済みのパスを構造化参照（backtick）で書かないのは、宣言整合性ゲート C1 が
    「名指されたファイルが存在しない」を違反として拾うためである。対象の綴りは
    下の定数 _PROTOTYPE_REL が唯一持つ。

なぜ fail-stop から削除へ（1-E → Wave2b・依頼者承認済み）:
    1-E の時点では「ファイルの削除は不可逆であり、試作の記録（当時どう作ったか）を
    失う」ことを理由に、実行経路だけを塞ぐ fail-stop を選んだ。その判断は**記録が
    ファイルの中にしか無い**という前提に立っていた。実際には当時の作り方は git 履歴に
    完全に残る（削除しても git show で読める）ので、前提は成立していない。

    一方 fail-stop は「ガードが先頭にある限り安全」という**条件つきの安全**である。
    ガードの上に 1 行書き足せば破れるし、それを禁じるために構文木の検査を常時
    走らせ続ける必要がある。ファイルが無ければ、上書きされ得る経路は条件抜きで
    存在しない。守るべき性質（本番 M1 CSV が試作に壊されない）が、検査ではなく
    構造で成り立つようになる。

なぜ「不在」を測るだけで足りないか:
    不在だけを見ると、後継ごと消えても緑になる。だから後継が実在すること（正規の
    経路が残っていること）を対で固定する。試作全般から保護領域への書き込みが 0 件で
    あることは `test_prototype_write_isolation.py` が引き続き構文木で固定する。
"""
from __future__ import annotations

from pathlib import Path

#: リポジトリ根（このファイル: <repo>/tools/tests/ → parents[2]）。
_REPO = Path(__file__).resolve().parents[2]

_PROTOTYPE_REL = "prototype_260626-01/prep_tick_rollup.py"

#: 後継（正規の経路）。案内と検定の単一の出所だった定数は、対象消滅に伴い
#: 検定側が直接名指しする。
_SUCCESSOR_REL = "tools/build_tick_rollup.py"


class TestThePrototypeIsGone:
    """本番 M1 CSV を無条件に上書きし得た試作が、存在しないこと。"""

    def test_the_prototype_file_does_not_exist(self):
        assert not (_REPO / _PROTOTYPE_REL).exists(), (
            f"{_PROTOTYPE_REL} が残っています。この試作は本番 M1 CSV を絶対パスで"
            f"無条件に上書きするため、正規の経路は {_SUCCESSOR_REL} ただ 1 つである。"
        )

    def test_the_prototype_directory_holds_no_rollup_variant(self):
        """名前を変えた写しが同じディレクトリへ復活していないこと。"""
        stray = sorted(
            p.relative_to(_REPO).as_posix()
            for p in (_REPO / "prototype_260626-01").glob("*tick_rollup*.py")
        )
        assert stray == [], stray


class TestTheSuccessorIsTheOnlyRoute:
    """空振り防止: 消したのは試作だけで、正規の経路は生きている。"""

    def test_the_successor_exists(self):
        assert (_REPO / _SUCCESSOR_REL).is_file(), _SUCCESSOR_REL

    def test_the_successor_is_not_itself_fail_stopped(self):
        """後継が「実行できないスタブ」に退化していないこと（不在検定の対で意味を持つ）。

        モジュール直下に無条件の ``raise SystemExit`` があれば、経路は名前だけ残って
        実体が無い状態である。それは試作を消した目的（正規の経路が 1 つ在ること）を
        満たさない。
        """
        import ast

        tree = ast.parse(
            (_REPO / _SUCCESSOR_REL).read_text(encoding="utf-8"), filename=_SUCCESSOR_REL
        )
        module_level_exits = [
            node
            for node in tree.body
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "SystemExit"
        ]
        assert module_level_exits == []
