"""usecase/vol_band_ports.py の read/write ロール分離テスト（ISP・ISSUE-099 🟡-2）。

太った `VolBandRepositoryPort`（save/save_all/get/all_week_ids）を書込ロール
`VolBandWriterPort`（save_all）と読取ロール `VolBandReaderPort`（get）へ分離した
ことを検証する。未使用の `save`単体・`all_week_ids` は削除済み。
"""
from __future__ import annotations

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
