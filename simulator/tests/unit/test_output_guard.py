"""TDD: assert_safe_output_dir 拒否プレフィクス（詳細設計 §6.2.3 / H-2・C1）。

既存データディレクトリ（marketdata/・fixtures/・confirmation/）配下と
repo_root 外への書込を構造的に拒否する純関数。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.tools.run_is_oos_cli import OutputGuardError, assert_safe_output_dir


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.parametrize(
    "out_dir",
    [
        "marketdata/x",
        "marketdata",  # 完全一致
        "simulator/tests/fixtures/y",
        "simulator/tests/confirmation/z",
        "../escape",  # repo_root 外脱出
    ],
)
def test_assert_safe_output_dir_rejects_forbidden_and_escape(repo_root, out_dir):
    # Act / Assert
    with pytest.raises(OutputGuardError):
        assert_safe_output_dir(out_dir, repo_root)


def test_assert_safe_output_dir_allows_similar_prefix_not_segment_match(repo_root):
    # Arrange: marketdata2 は marketdata の部分文字列だが別セグメント → 許可
    # Act
    resolved = assert_safe_output_dir("marketdata2/x", repo_root)
    # Assert
    assert isinstance(resolved, Path)
    assert resolved == (repo_root / "marketdata2" / "x").resolve()


def test_assert_safe_output_dir_allows_normal_out(repo_root):
    # Act
    resolved = assert_safe_output_dir("outputs/isoos/run1", repo_root)
    # Assert
    assert resolved == (repo_root / "outputs" / "isoos" / "run1").resolve()
