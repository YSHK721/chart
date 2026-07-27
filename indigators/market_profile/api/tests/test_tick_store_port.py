"""ISSUE-091 🔴-2: compute のティック I/O 依存が TickStorePort（Output Boundary）へ逆転済みの回帰ガード。

compute パッケージ（方針側）は marketdata の I/O 具象（tick_m1 の day parquet・paths の
DATA_DIR レイアウト）を module-level で import しない。物理格納への結線は
gateway/marketdata_tick_store（具象実装）にのみ許す。session_day / tf_meta は純業務規則
（I/O 非依存）のため対象外。
"""
from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"

# I/O 具象（物理格納・配置）とみなす marketdata サブモジュール。純業務規則（session_day/tf_meta）は許容。
_IO_IMPORT = re.compile(
    r"\s*(from\s+marketdata\.tick_m1\s+import"
    r"|from\s+marketdata\s+import\s+.*\bpaths\b"
    r"|import\s+marketdata\.tick_m1"
    r"|import\s+marketdata\.paths)"
)


def test_compute_has_no_marketdata_io_imports():
    offenders = []
    for p in (_PKG / "compute").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _IO_IMPORT.match(line):
                offenders.append(f"{p.relative_to(_PKG)}:{i}: {line.strip()}")
    assert not offenders, "compute に marketdata I/O 具象への直結が残存:\n" + "\n".join(offenders)


class _FakeStore:
    """TickStorePort 互換の決定論フェイク（構造的部分型＝Protocol 準拠を実挙動で確認）。"""

    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp
        self.calls: list[tuple] = []

    def day_files(self, lo_day, hi_day, *, symbol):
        # ISSUE-183: ポート契約は UNIX 秒 int。受領値をそのまま記録し型の貫通を検出可能にする。
        self.calls.append(("day_files", lo_day, hi_day, symbol))
        return []

    def data_dir(self):
        return self._tmp


def test_set_tick_store_injects_into_dwell_and_zp(tmp_path, monkeypatch):
    from market_profile_api.compute import market_profile_dwell as mpd
    from market_profile_api.compute import market_profile_zp as zp
    from market_profile_api.compute import tick_store_port as tsp

    fake = _FakeStore(tmp_path)
    monkeypatch.setattr(tsp, "_STORE", fake)

    # dwell / zp の列挙シム（monkeypatch 温存の module 属性）がポートへ委譲する。
    # ISSUE-183: ポート契約は UNIX 秒 int（pandas 型はポートを貫通しない）。
    day0, day1 = 1767571200, 1767657600  # 2026-01-05 / 2026-01-06 00:00 UTC。
    assert mpd.day_parquet_files(day0, day1, symbol="JP225") == []
    assert zp.day_parquet_files(day0, day1, symbol="JP225") == []
    assert [c[0] for c in fake.calls] == ["day_files", "day_files"]
    # 実引数が int のまま実装へ届く（int→pd.Timestamp 変換は gateway 実装の内側に閉じる）。
    assert [(c[1], c[2]) for c in fake.calls] == [(day0, day1), (day0, day1)]


def test_default_store_is_marketdata_gateway(monkeypatch):
    from market_profile_api.compute import tick_store_port as tsp
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

    monkeypatch.setattr(tsp, "_STORE", None)
    store = tsp.tick_store()
    assert isinstance(store, MarketdataTickStore)
    assert isinstance(store, tsp.TickStorePort)  # runtime_checkable Protocol 準拠


# --------------------------------------------------------------------------- #
# ISSUE-136（ISP）: TickStorePort を DataRootPort ＋ TickReaderPort へ分割
# --------------------------------------------------------------------------- #
class _DataRootOnly:
    """DataRootPort だけを満たすフェイク（tick 読取を持たない）。"""

    def data_dir(self):
        return Path("/fake/root")


class _TickReaderOnly:
    """TickReaderPort だけを満たすフェイク（data_dir を持たない）。

    ISSUE-182 item3: ``read_ticks`` は Port から降格したため実装しない（Port が要求しない）。
    """

    def day_files(self, lo_day, hi_day, *, symbol):
        return []

    def load_window_ticks(self, symbol, start, end, *, columns, outlier_frac):
        return (None, None)


def test_ports_are_split_by_role_isp():
    """DataRootPort（基点のみ）と TickReaderPort（tick 読取のみ）は独立に満たせる（ISP）。"""
    from market_profile_api.compute import tick_store_port as tsp

    dr, tr = _DataRootOnly(), _TickReaderOnly()
    # DataRootPort は data_dir だけを要求（tick 読取のみのフェイクは満たさない）。
    assert isinstance(dr, tsp.DataRootPort)
    assert not isinstance(tr, tsp.DataRootPort)
    # TickReaderPort は tick 読取だけを要求（基点のみのフェイクは満たさない）。
    assert isinstance(tr, tsp.TickReaderPort)
    assert not isinstance(dr, tsp.TickReaderPort)
    # 合成 TickStorePort は両方を要求（片面フェイクは満たさない）。
    assert not isinstance(dr, tsp.TickStorePort)
    assert not isinstance(tr, tsp.TickStorePort)


def test_narrow_getters_share_single_injection_seam(monkeypatch):
    """data_root() / tick_reader() は単一の注入シーム（tick_store）へ委譲する（既存挙動温存）。"""
    from market_profile_api.compute import tick_store_port as tsp

    fake = _FakeStore(Path("/tmp"))  # data_dir + day_files を満たす。
    monkeypatch.setattr(tsp, "_STORE", fake)
    assert tsp.data_root() is fake
    assert tsp.tick_reader() is fake
    assert tsp.tick_store() is fake


def test_isp_clients_depend_on_narrow_getters():
    """dwell/zp は tick_reader に、tf_period/composition は data_root に依存する（太い tick_store 直参照なし）。"""
    for rel in ("compute/market_profile_dwell.py", "compute/market_profile_zp.py"):
        src = (_PKG / rel).read_text(encoding="utf-8")
        assert "import tick_reader" in src, f"{rel} は狭い TickReaderPort に依存すべき"
        assert "import tick_store as" not in src, f"{rel} に太い tick_store 直依存が残存"
    for rel in ("controller/tf_period_profile_controller.py", "gateway/composition.py"):
        src = (_PKG / rel).read_text(encoding="utf-8")
        assert "import data_root" in src, f"{rel} は狭い DataRootPort に依存すべき"
        assert "import tick_store as" not in src, f"{rel} に太い tick_store 直依存が残存"
