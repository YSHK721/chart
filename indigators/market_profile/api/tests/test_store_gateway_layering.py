"""ISSUE-092 ④: 永続化 store / キャッシュ I/O が gateway 層に隔離済みであることの回帰ガード。

:mod:`test_tick_store_port`（compute に marketdata I/O 直結が残らないこと）と同様式で、本モジュールは
「永続化の物理 I/O（npz store・tf_period 日次 JSON）が compute / controller の方針層に残らず、
gateway（結線層）に実体がある」ことを固定する。過剰に厳密な grep で誤検知しないよう、コメント・
docstring 中の語（例: "…tempfile…"）ではなく**文レベルの I/O プリミティブ**（``import tempfile`` /
``np.savez`` / ``json.dump`` / ``os.replace`` / ``open(...,"w")``）のみを対象にする。
"""
from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"

# npz store の書込プリミティブ（文レベル）。コメント/docstring 中の "tempfile" 等は先頭が `#`/文字列
# のため re.match（行頭アンカ）では一致しない。alias（import tempfile as _tempfile）も先頭一致で捕捉。
_STORE_WRITE = re.compile(r"\s*(import\s+tempfile\b|np\.savez)")

# tf_period 日次ディスク JSON の読み書きプリミティブ（文レベル）。
_JSON_IO = re.compile(r"\s*(_?json\.dump\(|_?os\.replace\(|open\()")


def _offenders(path: Path, pattern: re.Pattern) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if pattern.match(line):
            out.append(f"{path.relative_to(_PKG)}:{i}: {line.strip()}")
    return out


def test_compute_has_no_persistence_store_write_primitives():
    """compute（方針層）に npz store の書込プリミティブが残らない（store 実体は gateway へ移設済み）。"""
    offenders: list[str] = []
    for p in (_PKG / "compute").rglob("*.py"):
        offenders += _offenders(p, _STORE_WRITE)
    assert not offenders, "compute に永続化 store の書込 I/O が残存:\n" + "\n".join(offenders)


def test_tf_period_controller_has_no_raw_disk_io():
    """tf_period controller（方針層）に日次 JSON の生ディスク I/O が残らない（gateway へ委譲済み）。"""
    p = _PKG / "controller" / "tf_period_profile_controller.py"
    offenders = _offenders(p, _JSON_IO)
    assert not offenders, "controller に tf_period 日次キャッシュの生 I/O が残存:\n" + "\n".join(offenders)


def test_persistence_stores_live_in_gateway():
    """移設先 gateway に store / キャッシュ実体（クラス・load/save 関数）が存在する。"""
    gw = _PKG / "gateway"
    dwell_src = (gw / "dwell_rollup_store.py").read_text(encoding="utf-8")
    zp_src = (gw / "zp_store.py").read_text(encoding="utf-8")
    tfp_src = (gw / "tf_period_disk_cache.py").read_text(encoding="utf-8")
    assert "class DwellRollupStore" in dwell_src
    assert "np.savez" in dwell_src  # 書込実体が gateway 側にある証拠。
    assert "class ZpStore" in zp_src
    assert "np.savez" in zp_src
    assert "def load_day_disk" in tfp_src and "def save_day_disk" in tfp_src
    assert "json.dump" in tfp_src and "os.replace" in tfp_src


def test_old_compute_store_paths_reexport():
    """旧 compute パスは gateway への薄い再エクスポートとして温存される（既存 import 無変更で動く）。"""
    from market_profile_api.compute.market_profile_dwell_store import DwellRollupStore
    from market_profile_api.compute.market_profile_zp_store import ZpStore
    from market_profile_api.gateway.dwell_rollup_store import (
        DwellRollupStore as GwDwell,
    )
    from market_profile_api.gateway.zp_store import ZpStore as GwZp

    assert DwellRollupStore is GwDwell  # 同一クラス（identity）＝再エクスポートの証明。
    assert ZpStore is GwZp
