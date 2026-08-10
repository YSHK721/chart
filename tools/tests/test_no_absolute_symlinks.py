"""追跡 symlink に絶対パスが混入していないことを固定する（ISSUE-365）。

## 何を防ぐか

git が追跡する symlink の中身が**絶対パス**だと、別のチェックアウトへ展開されたときに
「自分自身を指す symlink」になり、実体参照が失われる。

2026-08-10 に実際に起きた（ISSUE-363）。worktree から core を起動するために張った
``data/marketdata -> /workspaces/app/data/marketdata`` と ``.venv -> /workspaces/app/...``
の 2 本が ``git add -A`` でコミットされ、本チェックアウトへマージされた瞬間に自己参照へ
置換されて venv とデータの両方が参照不能になり、サーバが起動しなくなった。

## なぜ .gitignore では足りないか

- ``.gitignore`` は**既に追跡されているファイルには効かない**。一度入ったら止められない。
- 守る対象を特定のパス名で列挙すると、次に別のパスで同じことが起きたとき素通しになる。

守るべきは特定のパスではなく「**絶対パスの symlink**」という形そのものである。
相対パス（``../../``）の symlink はツリー内で完結するため、どこへ展開しても自己参照にならない。
実測（2026-08-10）: 本リポジトリの追跡 symlink 126 本はすべて相対パスで、事故を起こした
2 本だけが絶対パスだった。すなわち本検査は当該事故を確実に検出する。

## なぜ「作らない」ではなく「検査する」か

作る理由そのものは ``tools/setup_worktree.sh``（環境変数で実体を指す）が消している。
本検査はその保険で、将来別の理由で誰かが張ったときに commit 前に落とす。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# git が symlink を表すモード。
_SYMLINK_MODE = "120000"


def _tracked_symlinks() -> list[tuple[str, str]]:
    """追跡中の symlink を [(パス, リンク先文字列)] で返す。

    走査対象は **index**（``git ls-files -s``）であって ``git ls-tree HEAD`` ではない。
    理由（実測 2026-08-10）: HEAD を見ると、``git add`` した直後の symlink を検出できず、
    **コミットしてからでないと落ちない**。それでは事故を防げない（本検査の存在意義が失われる）。
    index は「これからコミットされる内容」なので、add した時点で検出できる。
    作業ツリーがクリーンなら index は HEAD と一致するため、既存の追跡分も同時に覆う。

    リンク先は作業ツリーの実ファイルではなく git が記録している blob を読む。
    作業ツリー側は張り直されている可能性があり、コミットに入る値とは限らないため。
    """
    listing = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout

    out: list[tuple[str, str]] = []
    for line in listing.splitlines():
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[0] != _SYMLINK_MODE:
            continue
        target = subprocess.run(
            ["git", "cat-file", "blob", parts[1]],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
        out.append((path, target.strip()))
    return out


def _is_absolute_target(target: str) -> bool:
    """symlink のリンク先が絶対パスか（＝展開先によって自己参照になりうるか）。"""
    return target.startswith("/")


def test_detector_flags_the_known_bad_shape() -> None:
    """検出器の自己検査: 実際に起きた不良形を検出し、安全な形を誤検出しない。

    検出しない検出器を置かないための検査。ISSUE-363 の 2 本を逐語で使う。
    """
    # Arrange: 左が事故を起こした実際の値、右がリポジトリに実在する安全な形。
    bad = [
        "/workspaces/app/data/marketdata",
        "/workspaces/app/lightweight-charts-python-main/.venv",
    ]
    good = [
        "../../../../indigators/indicator_ui/web/css/app.css",
        "../../usecase/chrome_tokens.js",
    ]
    # Act / Assert
    for target in bad:
        assert _is_absolute_target(target), f"既知の不良形を検出できていない: {target}"
    for target in good:
        assert not _is_absolute_target(target), f"安全な形を誤検出した: {target}"


def test_no_tracked_symlink_points_at_an_absolute_path() -> None:
    """追跡 symlink はすべて相対パスである（自己参照 checkout を構造的に断つ）。"""
    # Arrange
    symlinks = _tracked_symlinks()
    # Act
    offenders = [(p, t) for p, t in symlinks if _is_absolute_target(t)]
    # Assert
    assert not offenders, (
        "追跡 symlink に絶対パスが含まれる（別チェックアウトへ展開すると自己参照になり、"
        "実体参照が失われる・ISSUE-363）:\n"
        + "\n".join(f"  {p} -> {t}" for p, t in offenders)
        + "\n環境依存の実体は tools/setup_worktree.sh（環境変数）で指すこと。"
    )


def test_repository_actually_has_tracked_symlinks() -> None:
    """本検査が空振りしていないこと（0 本なら上の検査は常に通り、無意味になる）。"""
    # Arrange / Act
    symlinks = _tracked_symlinks()
    # Assert
    assert len(symlinks) > 0, "追跡 symlink が 0 本＝検査対象が無い（走査が壊れている疑い）"
