"""usecase/vol_band_ports.py の read/write ロール分離テスト（ISP・ISSUE-099 🟡-2）。

太った `VolBandRepositoryPort`（save/save_all/get/all_week_ids）を書込ロール
`VolBandWriterPort`（save_all）と読取ロール `VolBandReaderPort`（get）へ分離した
ことを検証する。未使用の `save`単体・`all_week_ids` は削除済み。具象リポジトリは
両ロールを満たす統合実装のままである。
"""
from __future__ import annotations

from pathlib import Path

from simulator.adapter.repository.vol_band_parquet import VolBandParquetRepo
from simulator.domain.variance_forecast import VarianceForecast
from simulator.usecase.vol_band_ports import (
    VolBandReaderPort,
    VolBandWriterPort,
)


class TestRoleSeparation:
    def test_writer_port_declares_only_save_all(self):
        # Writer ロールは書込 1 メソッド（save_all）のみを公開面に持つ
        members = set(getattr(VolBandWriterPort, "__protocol_attrs__", ()))
        assert "save_all" in members
        assert "get" not in members
        assert "save" not in members
        assert "all_week_ids" not in members

    def test_reader_port_declares_only_get(self):
        # Reader ロールは読取 1 メソッド（get）のみを公開面に持つ
        members = set(getattr(VolBandReaderPort, "__protocol_attrs__", ()))
        assert "get" in members
        assert "save_all" not in members
        assert "save" not in members
        assert "all_week_ids" not in members

    def test_concrete_repo_satisfies_both_roles(self, tmp_path):
        # 具象は両ロールを満たす統合実装（runtime_checkable Protocol の isinstance で実証）
        repo = VolBandParquetRepo(out_dir=tmp_path)
        assert isinstance(repo, VolBandWriterPort)
        assert isinstance(repo, VolBandReaderPort)

    def test_write_then_read_through_role_ports(self, tmp_path):
        # Writer 経路で保存し Reader 経路で取得する round-trip（ロール分離後も統合動作）
        writer: VolBandWriterPort = VolBandParquetRepo(out_dir=tmp_path)
        writer.save_all([VarianceForecast("2024-W07", 0.025, 0.020, 0.018, estimable=True)])
        reader: VolBandReaderPort = VolBandParquetRepo(out_dir=tmp_path)
        fc = reader.get("2024-W07")
        assert fc is not None and fc.estimable is True


class TestRemovedMethodsAbsent:
    def test_repo_no_longer_exposes_unused_methods(self, tmp_path):
        # production 呼び出し 0 件だった save単体・all_week_ids は削除済み
        repo = VolBandParquetRepo(out_dir=Path(tmp_path))
        assert not hasattr(repo, "save")
        assert not hasattr(repo, "all_week_ids")
