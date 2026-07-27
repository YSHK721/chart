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

# ISSUE-137（DIP）: compute（方針層）が gateway 永続化 Store 具象を **module-level** で import または
#   直接 new する逆流を禁ずる。自己完結起動の遅延フォールバック（関数本体・インデント行）は
#   tick_store_port / store_port の getter 規律として許容するため、**行頭（インデントなし）** のみ照合する。
_GATEWAY_STORE_IMPORT = re.compile(
    r"from\s+market_profile_api\.gateway\.(zp_store|dwell_rollup_store)\s+import\b"
)
_GATEWAY_STORE_NEW = re.compile(r"=\s*(ZpStore|DwellRollupStore)\s*\(")


def _offenders(path: Path, pattern: re.Pattern) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if pattern.match(line):
            out.append(f"{path.relative_to(_PKG)}:{i}: {line.strip()}")
    return out


def _module_level_offenders(path: Path, pattern: re.Pattern) -> list[str]:
    """行頭（インデントなし＝module-level）の一致のみを違反として返す。

    関数本体（インデント行）の遅延 import / 合成は自己完結起動の許容パターンのため除外する。
    """
    out: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line[:1] not in (" ", "\t") and pattern.search(line):
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


def test_compute_has_no_module_level_gateway_store_binding():
    """ISSUE-137（DIP）: compute（方針層）が gateway 永続化 Store 具象を module-level で import / 直接 new
    しない（既定結線は composition root が担い、compute は StorePort にのみ依存する）。

    自己完結起動の遅延フォールバック（getter 内・関数本体のインデント import/合成）は許容する。
    """
    # 互換再エクスポートシム（旧 import パス温存・ISSUE-092 ④）は gateway クラスを **再エクスポート**
    #   するのが唯一の責務で、Store の合成（new）も方針への持ち込みも行わない＝DIP 違反ではない。
    #   これらは test_old_compute_store_paths_reexport が別途保証するため本ガードの対象外にする。
    _REEXPORT_SHIMS = {"market_profile_zp_store.py", "market_profile_dwell_store.py"}
    offenders: list[str] = []
    for p in (_PKG / "compute").rglob("*.py"):
        if p.name in _REEXPORT_SHIMS:
            continue
        offenders += _module_level_offenders(p, _GATEWAY_STORE_IMPORT)
        offenders += _module_level_offenders(p, _GATEWAY_STORE_NEW)
    assert not offenders, (
        "compute に gateway 永続化 Store 具象の module-level 直結（import / new）が残存:\n"
        + "\n".join(offenders)
    )


def test_default_store_wiring_lives_in_gateway_composition():
    """ISSUE-137: 既定 Store の合成（具象クラス名指し）は composition root（gateway）に集約される。"""
    comp = (_PKG / "gateway" / "composition.py").read_text(encoding="utf-8")
    assert "def default_zp_store" in comp and "ZpStore(" in comp
    assert "def default_dwell_store" in comp and "DwellRollupStore(" in comp
    assert "def default_tick_store" in comp and "MarketdataTickStore(" in comp


class _FakeZp:
    """``ZpStorePort`` を満たす代替実装（ディスクの代わりにプロセス内 dict へ保存する）。

    ISSUE-177: ``set_zp_store`` の ``isinstance`` ガード導入に伴い、Port の**全**必須属性を備える。
    ダミー返却ではなく Port の意味論（save→load の往復／未保存は :attr:`CACHE_MISS`／``None`` は
    「実データ無しの完了日」として ``CACHE_MISS`` と区別）を実際に満たすため、代替実装が既定具象と
    置換可能であること（LSP）をテストが実挙動で検出できる。
    """

    CACHE_MISS = object()

    def __init__(self, root: Path = Path("/fake/zp")) -> None:
        self._root = root
        self._saved: dict = {}

    def cache_root(self) -> Path:
        return self._root

    def mgrid_path(self, symbol, day_start):  # noqa: ANN001
        return self._root / "mgrid" / str(symbol) / f"{int(day_start)}.npz"

    def null_path(self, symbol, day_start):  # noqa: ANN001
        return self._root / "znull" / str(symbol) / f"{int(day_start)}.npz"

    def save_mgrid(self, path, grid, sig: str = "") -> None:  # noqa: ANN001
        self._saved[path] = (grid, sig)

    def load_mgrid(self, path):  # noqa: ANN001
        return self._saved.get(path, (self.CACHE_MISS, ""))

    def save_null(self, path, roll, sig: str = "") -> None:  # noqa: ANN001
        self._saved[path] = (roll, sig)

    def load_null(self, path):  # noqa: ANN001
        return self._saved.get(path, (self.CACHE_MISS, ""))

    def day_source_signature(self, symbol, day_start) -> str:  # noqa: ANN001
        return f"{symbol}:{int(day_start)}"


class _FakeDwell:
    """``DwellStorePort`` を満たす代替実装（プロセス内 dict 保存・意味論は :class:`_FakeZp` と同じ）。"""

    CACHE_MISS = object()

    def __init__(self, root: Path = Path("/fake/dwell")) -> None:
        self._root = root
        self._saved: dict = {}

    def cache_root(self) -> Path:
        return self._root

    def cache_path(self, symbol, day_start):  # noqa: ANN001
        return self._root / str(symbol) / f"{int(day_start)}.npz"

    def save_day_rollup(self, path, roll, sig: str = "") -> None:  # noqa: ANN001
        self._saved[path] = (roll, sig)

    def load_day_rollup(self, path):  # noqa: ANN001
        return self._saved.get(path, (self.CACHE_MISS, ""))

    def day_source_signature(self, symbol, day_start) -> str:  # noqa: ANN001
        return f"{symbol}:{int(day_start)}"


def test_store_port_injection_round_trip(monkeypatch):
    """ISSUE-137: set_zp_store / set_dwell_store 注入シームが compute から機能する（TickStorePort と同規律）。"""
    from market_profile_api.compute import store_port as sp

    fz, fd = _FakeZp(), _FakeDwell()
    monkeypatch.setattr(sp, "_ZP_STORE", None)
    monkeypatch.setattr(sp, "_DWELL_STORE", None)
    # 未注入時は composition root の既定へ遅延合成される（自己完結起動）。
    from market_profile_api.gateway.zp_store import ZpStore
    from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore

    assert isinstance(sp.zp_store(), ZpStore)
    assert isinstance(sp.dwell_store(), DwellRollupStore)
    # 注入すると getter は注入実体を返す。
    sp.set_zp_store(fz)
    sp.set_dwell_store(fd)
    try:
        assert sp.zp_store() is fz
        assert sp.dwell_store() is fd
        assert sp.zp_cache_miss() is _FakeZp.CACHE_MISS
        assert sp.dwell_cache_miss() is _FakeDwell.CACHE_MISS
        # 代替実装が Port の意味論を満たす（未保存は CACHE_MISS・save→load で往復・sig 併記）。
        zpath = fz.null_path("SYN", 1704067200)
        assert fz.load_null(zpath)[0] is _FakeZp.CACHE_MISS
        fz.save_null(zpath, {"probe": 1}, "sig-z")
        assert fz.load_null(zpath) == ({"probe": 1}, "sig-z")
        dpath = fd.cache_path("SYN", 1704067200)
        assert fd.load_day_rollup(dpath)[0] is _FakeDwell.CACHE_MISS
        fd.save_day_rollup(dpath, None, "sig-d")
        assert fd.load_day_rollup(dpath) == (None, "sig-d")  # None は「実データ無し」＝MISS と別物。
    finally:
        sp.set_zp_store(None)
        sp.set_dwell_store(None)


def test_old_compute_store_paths_reexport():
    """旧 compute パスは gateway への薄い再エクスポートとして温存される（既存 import 無変更で動く）。

    ISSUE-183: シムの**消費者**（``test_market_profile_dwell_store`` / ``test_market_profile_zp_store``）は
    gateway 直参照へ移行済みで、旧 compute パスを import するのは本テスト（＝シム自身の契約テスト）
    のみになった。本テストを gateway 直参照へ書き換えると ``GwDwell is GwDwell`` の恒真式に退化して
    契約検証が消えるため、シムが現存する限り本テストは旧パス参照のまま残す
    （``_REEXPORT_SHIMS`` 免除の根拠もこれ）。シム 2 ファイルの削除は要承認事項。
    """
    from market_profile_api.compute.market_profile_dwell_store import DwellRollupStore
    from market_profile_api.compute.market_profile_zp_store import ZpStore
    from market_profile_api.gateway.dwell_rollup_store import (
        DwellRollupStore as GwDwell,
    )
    from market_profile_api.gateway.zp_store import ZpStore as GwZp

    assert DwellRollupStore is GwDwell  # 同一クラス（identity）＝再エクスポートの証明。
    assert ZpStore is GwZp
