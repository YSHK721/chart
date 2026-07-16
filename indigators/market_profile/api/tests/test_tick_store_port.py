"""ISSUE-091 🔴-2: compute のティック I/O 依存が TickStorePort（Output Boundary）へ逆転済みの回帰ガード。

compute パッケージ（方針側）は marketdata の I/O 具象（tick_m1 の day parquet・paths の
DATA_DIR レイアウト）を module-level で import しない。物理格納への結線は
gateway/marketdata_tick_store（具象実装）にのみ許す。session_day / tf_meta は純業務規則
（I/O 非依存）のため対象外。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

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
        self.calls.append(("day_files", str(lo_day), str(hi_day), symbol))
        return []

    def read_ticks(self, path, columns):
        self.calls.append(("read_ticks", str(path), tuple(columns)))
        return pd.DataFrame(columns=list(columns))

    def data_dir(self):
        return self._tmp


def test_set_tick_store_injects_into_dwell_and_zp(tmp_path, monkeypatch):
    from market_profile_api.compute import market_profile_dwell as mpd
    from market_profile_api.compute import market_profile_zp as zp
    from market_profile_api.compute import tick_store_port as tsp

    fake = _FakeStore(tmp_path)
    monkeypatch.setattr(tsp, "_STORE", fake)

    # dwell / zp の列挙シム（monkeypatch 温存の module 属性）がポートへ委譲する。
    assert mpd.day_parquet_files(pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06"), symbol="JP225") == []
    assert zp.day_parquet_files(pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06"), symbol="JP225") == []
    assert [c[0] for c in fake.calls] == ["day_files", "day_files"]


def test_default_store_is_marketdata_gateway(monkeypatch):
    from market_profile_api.compute import tick_store_port as tsp
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

    monkeypatch.setattr(tsp, "_STORE", None)
    store = tsp.tick_store()
    assert isinstance(store, MarketdataTickStore)
    assert isinstance(store, tsp.TickStorePort)  # runtime_checkable Protocol 準拠
