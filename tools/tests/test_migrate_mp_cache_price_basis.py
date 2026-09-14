"""MP キャッシュを価格基準つきの鍵へ引き継ぐ移行（ISSUE-511 段階 1c）の検定。

移行の中身は「``<root>/<tree>`` を ``<root>/price-<basis>/<tree>`` へ **複写** する」だけである
（キャッシュの中身は 1 バイトも変えない＝再計算 0）。旧配置は **触らない**（消さない・動かさない）。

なぜ名前変更ではなく複写か（実測 2026-09-14）:
    実キャッシュでの ``os.rename`` が ``EXDEV: Invalid cross-device link`` で失敗した。この環境の
    データ置き場ではディレクトリの名前変更が成り立たない。複写なら置き場に依らず成り立ち、旧配置が
    残るので「戻す」操作も要らない（旧コードに戻しても旧配置をそのまま読める）。旧配置の掃除は
    GC の孤児判定で別に行う（削除は依頼者の承認事項）。

台帳の木（jp225_tick の JP225・jp225_mt5 の MT5 枝名）だけを複写し、台帳に無い dir は触らない。
移し先が既にあればその木は飛ばす（混ぜない・上書きしない）。

すべて ``tmp_path`` 上で行う（実キャッシュへ触れない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from tools import migrate_mp_cache_price_basis as mig

_BASES = {"JP225": "mid", "JP225@OANDA-Japan-MT5-Live": "bid"}


def _tree(root, name, files=("a.npz", "sub/b.npz")):
    for rel in files:
        p = root / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"{name}/{rel}".encode())


def _snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def roots(tmp_path):
    out = [tmp_path / name for name in ("dwell", "mgrid", "znull", "tf_period")]
    for root in out:
        _tree(root, "JP225")
        _tree(root, "JP225@OANDA-Japan-MT5-Live")
        _tree(root, "NOSYM")          # 台帳に無い dir（触らない）
    return out


def test_the_plan_copies_only_ledger_trees_under_their_basis(roots):
    """計画は台帳の木だけを ``price-<basis>/<tree>`` へ複写する（台帳に無い dir は含めない）。"""
    # Arrange / Act
    plan = mig.plan_copies(roots, _BASES)

    # Assert
    assert {(s.relative_to(s.parents[0]).as_posix(), d.relative_to(s.parents[0]).as_posix())
            for s, d in plan} == {
        ("JP225", "price-mid/JP225"),
        ("JP225@OANDA-Japan-MT5-Live", "price-bid/JP225@OANDA-Japan-MT5-Live"),
    }
    assert len(plan) == 2 * len(roots)


def test_planning_writes_nothing(roots, tmp_path):
    """計画（dry-run）はファイルを 1 つも書かない。"""
    # Arrange
    before = _snapshot(tmp_path)

    # Act
    mig.plan_copies(roots, _BASES)

    # Assert
    assert _snapshot(tmp_path) == before


def test_applying_copies_every_byte_and_leaves_the_originals(roots, tmp_path):
    """適用後、新しい鍵の位置に同じ中身があり、旧配置と台帳外の dir は 1 バイトも変わらない。"""
    # Arrange
    before = _snapshot(tmp_path)

    # Act
    mig.apply_copies(mig.plan_copies(roots, _BASES))

    # Assert
    after = _snapshot(tmp_path)
    assert {k: v for k, v in after.items() if k in before} == before, "旧配置が変わった"
    for root in roots:
        for tree, basis in _BASES.items():
            for rel in ("a.npz", "sub/b.npz"):
                assert (root / f"price-{basis}" / tree / rel).read_bytes() == (
                    root / tree / rel
                ).read_bytes()
        assert not (root / "price-mid" / "NOSYM").exists()


def test_a_second_run_finds_nothing_to_copy(roots):
    """適用済みなら計画は空（何度流しても同じ結果）。"""
    # Arrange
    mig.apply_copies(mig.plan_copies(roots, _BASES))

    # Act / Assert
    assert mig.plan_copies(roots, _BASES) == []


def test_an_existing_destination_is_left_alone(roots):
    """移し先が既にある木は飛ばす（混ぜない・上書きしない）。"""
    # Arrange
    occupied = roots[0] / "price-mid" / "JP225"
    occupied.mkdir(parents=True)
    (occupied / "server_made.npz").write_bytes(b"x")

    # Act
    plan = mig.plan_copies(roots, _BASES)
    mig.apply_copies(plan)

    # Assert
    assert all(d != occupied for _, d in plan)
    assert sorted(p.name for p in occupied.iterdir()) == ["server_made.npz"]


@pytest.mark.parametrize("n_roots", [1, 4])
def test_copies_issued_equal_copies_planned(monkeypatch, tmp_path, n_roots):
    """計算量: 発行した複写 − 計画した複写 = 0（root 数 1/4 の 2 点で固定）。"""
    # Arrange
    roots = [tmp_path / f"r{i}" for i in range(n_roots)]
    for root in roots:
        _tree(root, "JP225")
    plan = mig.plan_copies(roots, _BASES)
    issued = []
    real = mig._copy_tree
    # 数えるのは本ツールの発行単位（木 1 本）。shutil.copytree は下位 dir ごとに自身を再帰で呼ぶので数えない。
    monkeypatch.setattr(mig, "_copy_tree", lambda s, d: (issued.append(s), real(s, d))[1])

    # Act
    mig.apply_copies(plan)

    # Assert
    assert len(issued) - len(plan) == 0
