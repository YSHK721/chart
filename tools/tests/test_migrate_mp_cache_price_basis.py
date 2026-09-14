"""MP キャッシュを価格基準つきの鍵へ引き継ぐ移行（ISSUE-511 段階 1c）の検定。

移行の中身は「``<root>/<tree>`` → ``<root>/price-<basis>/<tree>``」の名前変更だけである
（キャッシュの中身は 1 バイトも変えない＝再計算 0）。戻すのも名前変更だけで可逆。
台帳の木（jp225_tick の JP225・jp225_mt5 の MT5 枝名）だけを動かし、台帳に無い dir は触らない。

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


def test_the_plan_moves_only_ledger_trees_above_their_basis(roots):
    """計画は台帳の木だけを ``price-<basis>/<tree>`` へ動かす（台帳に無い dir は含めない）。"""
    # Arrange / Act
    plan = mig.plan_moves(roots, _BASES)

    # Assert
    assert {(s.relative_to(s.parents[0]).as_posix(), d.relative_to(s.parents[0]).as_posix())
            for s, d in plan} == {
        ("JP225", "price-mid/JP225"),
        ("JP225@OANDA-Japan-MT5-Live", "price-bid/JP225@OANDA-Japan-MT5-Live"),
    }
    assert len(plan) == 2 * len(roots)


def test_planning_moves_nothing(roots, tmp_path):
    """計画（dry-run）はファイルを 1 つも動かさない。"""
    # Arrange
    before = _snapshot(tmp_path)

    # Act
    mig.plan_moves(roots, _BASES)

    # Assert
    assert _snapshot(tmp_path) == before


def test_applying_keeps_every_byte_and_leaves_other_dirs(roots, tmp_path):
    """適用後、キャッシュの中身は 1 バイトも変わらず新しい鍵の位置にあり、台帳外の dir は元の位置のまま。"""
    # Arrange
    before = _snapshot(tmp_path)

    # Act
    mig.apply_moves(mig.plan_moves(roots, _BASES))

    # Assert
    after = _snapshot(tmp_path)
    assert sorted(after.values()) == sorted(before.values())
    for root in roots:
        assert (root / "price-mid" / "JP225" / "a.npz").is_file()
        assert (root / "price-bid" / "JP225@OANDA-Japan-MT5-Live" / "sub" / "b.npz").is_file()
        assert (root / "NOSYM" / "a.npz").is_file()
        assert not (root / "JP225").exists()


def test_reverting_restores_the_original_layout_exactly(roots, tmp_path):
    """戻すと、元の配置・中身と完全に一致する（可逆）。"""
    # Arrange
    before = _snapshot(tmp_path)
    plan = mig.plan_moves(roots, _BASES)
    mig.apply_moves(plan)

    # Act
    mig.revert_moves(plan)

    # Assert
    assert _snapshot(tmp_path) == before


def test_a_second_run_finds_nothing_to_move(roots):
    """適用済みなら計画は空（何度流しても同じ結果）。"""
    # Arrange
    mig.apply_moves(mig.plan_moves(roots, _BASES))

    # Act / Assert
    assert mig.plan_moves(roots, _BASES) == []


def test_an_occupied_destination_is_refused(roots):
    """移し先が既にあれば動かさずに止める（混ぜない・上書きしない）。"""
    # Arrange
    (roots[0] / "price-mid" / "JP225").mkdir(parents=True)

    # Act / Assert
    with pytest.raises(FileExistsError):
        mig.plan_moves(roots, _BASES)


@pytest.mark.parametrize("n_roots", [1, 4])
def test_moves_issued_equal_moves_planned(monkeypatch, tmp_path, n_roots):
    """計算量: 発行した名前変更 − 計画した移動 = 0（root 数 1/4 の 2 点で固定）。"""
    # Arrange
    roots = [tmp_path / f"r{i}" for i in range(n_roots)]
    for root in roots:
        _tree(root, "JP225")
    plan = mig.plan_moves(roots, _BASES)
    issued = []
    real = mig.os.rename
    monkeypatch.setattr(mig.os, "rename", lambda s, d: (issued.append(s), real(s, d))[1])

    # Act
    mig.apply_moves(plan)

    # Assert
    assert len(issued) - len(plan) == 0
