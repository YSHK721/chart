"""marketdata.paths.DATA_DIR の検証（TDD: Red→Green）— 時系列データの単一基点。

Sd（data 分離＋DATA_DIR 単一基点・設計 §10.1 C-1 / §10.2 H-5）の確定仕様:
  - ``DATA_DIR = Path(os.environ.get("MARKETDATA_DATA_DIR", _REPO_ROOT / "data" / "marketdata"))``
  - ``_REPO_ROOT = Path(__file__).resolve().parents[1]``（marketdata 直上＝唯一の基点）。
  - 既定（env 未設定）はリポジトリ直下 ``data/marketdata``。
  - env override が指す path が**存在しない場合は FileNotFoundError で fail-fast**（fallback 禁止）。

回帰観点（memory bugfix-pair-with-regression-test）:
  - 旧 ``marketdata/data`` ハードコードが復活したら落ちる（DATA_DIR が data/marketdata を指す）。
  - 多基点ハードコード（marketdata/data 直下解決）への退行を path 解決で禁止する。

検証手法: env と DATA_DIR 解決は import 時に確定するため、``importlib.reload`` で
``MARKETDATA_DATA_DIR`` 変更を反映して再評価する（プロセス汚染回避・F.I.R.S.T Independent）。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _reload_paths():
    """marketdata.paths を再 import して現在の環境変数で DATA_DIR を再評価する。"""
    import marketdata.paths as paths

    return importlib.reload(paths)


def test_default_data_dir_is_repo_root_data_marketdata(monkeypatch):
    # Arrange: env 未設定（既定経路）。
    monkeypatch.delenv("MARKETDATA_DATA_DIR", raising=False)
    # Act
    paths = _reload_paths()
    # Assert: 既定はリポジトリ直下 data/marketdata（marketdata/data ではない）。
    repo_root = Path(paths.__file__).resolve().parents[1]
    assert paths.DATA_DIR == repo_root / "data" / "marketdata"


def test_default_data_dir_is_not_legacy_marketdata_data(monkeypatch):
    # Arrange: env 未設定。
    monkeypatch.delenv("MARKETDATA_DATA_DIR", raising=False)
    # Act
    paths = _reload_paths()
    # Assert（回帰）: 旧多基点 marketdata/data へ退行していない。
    repo_root = Path(paths.__file__).resolve().parents[1]
    assert paths.DATA_DIR != repo_root / "marketdata" / "data"
    assert "marketdata/data" not in str(paths.DATA_DIR).replace("\\", "/")


def test_env_override_points_to_existing_dir(tmp_path, monkeypatch):
    # Arrange: 実在する override ディレクトリ。
    override = tmp_path / "custom_marketdata"
    override.mkdir()
    monkeypatch.setenv("MARKETDATA_DATA_DIR", str(override))
    # Act
    paths = _reload_paths()
    # Assert: env が指す実在 path を採用する。
    assert paths.DATA_DIR == override


def test_env_override_missing_path_raises_filenotfounderror(tmp_path, monkeypatch):
    # Arrange: 存在しない override path（fail-fast 対象）。
    missing = tmp_path / "does_not_exist"
    monkeypatch.setenv("MARKETDATA_DATA_DIR", str(missing))
    # Act / Assert: fallback せず FileNotFoundError で即時失敗する。
    with pytest.raises(FileNotFoundError):
        _reload_paths()


def test_repo_root_is_marketdata_parent(monkeypatch):
    # Arrange: env 未設定。
    monkeypatch.delenv("MARKETDATA_DATA_DIR", raising=False)
    # Act
    paths = _reload_paths()
    # Assert: 唯一の基点 _REPO_ROOT は marketdata パッケージの直上。
    assert paths._REPO_ROOT == Path(paths.__file__).resolve().parents[1]
    assert (paths._REPO_ROOT / "marketdata").is_dir()
