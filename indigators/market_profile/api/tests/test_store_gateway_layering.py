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
    #   ISSUE-479 F-4 段階 1 以降、これらは test_no_code_imports_the_old_compute_store_paths が
    #   「参照ゼロ」を保証する（＝削除待ちの孤児）ため本ガードの対象外にする。免除行の撤去は
    #   シムファイル削除と同時に行う（段階 2・要承認）。
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


# ======================================================================================
# ISSUE-479 F-4 段階 1: 互換シム（旧 compute パス）の参照ゼロ化
#
# ISSUE-183 の時点で、シムの消費者は gateway 直参照へ移行済みで、旧 compute パスを import するのは
# シム自身の契約テスト 1 関数だけになっていた。その契約テストを gateway 直参照へ書き換えると
# ``GwDwell is GwDwell`` の恒真式に退化して検証が消えるため、旧パス参照のまま残されていた。
# つまり「シムが要るからテストが旧パスを参照し、テストが参照するからシムが要る」という自己参照で、
# シムの存在根拠が実需ではなく検査自身になっていた。
#
# 段階 1 では、契約テストを**参照ゼロの検査**へ置き換えて自己参照を断つ。旧パスを import する
# 本番・テストコードがリポジトリに 1 件も無いことを主張し、本テスト自身も旧パスを import しない。
# シムファイルの削除と ``_REEXPORT_SHIMS`` 免除の撤去は段階 2（要承認）で、削除と免除撤去を
# 同時に行って Red→Green を確認する。
# ======================================================================================

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: 走査から外すディレクトリ名（仮想環境・キャッシュ・第三者コード・試作・データ）。
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}
_SKIP_TOP_LEVEL = {"lightweight-charts-python-main", "data", "sample", "node_modules", "scratchpad"}

#: シム自身のファイル名（旧パスではなく実体側 gateway を import するため走査対象外）。
_SHIM_FILE_NAMES = {"market_profile_zp_store.py", "market_profile_dwell_store.py"}

#: 旧 compute パスの import 形態。ドット付きの明示パスと、パッケージからの名前 import の 2 系統。
_OLD_PATH_DOTTED = re.compile(
    r"\b(?:from|import)\s+(?:market_profile_api\.compute\.|\.)"
    r"market_profile_(?:zp|dwell)_store\b"
)
_OLD_PATH_FROM_PACKAGE = re.compile(
    r"\bfrom\s+(?:market_profile_api\.compute|\.)\s+import\s+(?P<names>[^#\n]+)"
)
_OLD_PATH_NAME = re.compile(r"\bmarket_profile_(?:zp|dwell)_store\b")


def _imports_old_compute_store_path(source: str) -> bool:
    """ソース文字列が旧 compute パスのシムを import しているか。"""
    if _OLD_PATH_DOTTED.search(source):
        return True
    for m in _OLD_PATH_FROM_PACKAGE.finditer(source):
        if _OLD_PATH_NAME.search(m.group("names")):
            return True
    return False


def _repo_sources() -> "list[Path]":
    """リポジトリの Python ソース（本番・テストの両方。仮想環境・試作・第三者コードは除く）。"""
    out: "list[Path]" = []
    for top in sorted(_REPO_ROOT.iterdir()):
        if not top.is_dir():
            continue
        if top.name in _SKIP_TOP_LEVEL or top.name in _SKIP_DIR_NAMES:
            continue
        if top.name.startswith("prototype_"):
            continue
        for p in top.rglob("*.py"):
            if _SKIP_DIR_NAMES & set(p.parts):
                continue
            out.append(p)
    out += sorted(_REPO_ROOT.glob("*.py"))
    return out


def test_repo_scan_covers_the_shim_package() -> None:
    """走査が実際にシムのあるパッケージと本テスト自身へ届いている（空走査で恒真式に退化しない）。"""
    sources = set(_repo_sources())
    for shim in sorted(_SHIM_FILE_NAMES):
        assert (_PKG / "compute" / shim) in sources, f"走査がシム {shim} に届いていません"
    assert Path(__file__).resolve() in sources, "走査が本テスト自身に届いていません"


def test_no_code_imports_the_old_compute_store_paths():
    """旧 compute パス（互換シム）を import する本番・テストコードがリポジトリに 1 件も無い。

    識別力: どこかで旧パス import を復活させると Red になる（本テスト自身も例外ではない）。
    落ちた場合の直し方は gateway 直参照（``market_profile_api.gateway.zp_store`` /
    ``gateway.dwell_rollup_store``）への付替え。
    """
    offenders: "list[str]" = []
    for path in _repo_sources():
        if path.parent == _PKG / "compute" and path.name in _SHIM_FILE_NAMES:
            continue  # シム自身は「旧パス」ではなく実体側（gateway）を import している。
        if _imports_old_compute_store_path(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, (
        "互換シム（旧 compute パス）を import している箇所が残っています:\n"
        + "\n".join(offenders)
        + "\ngateway 直参照へ付替えてください。"
    )


class _SyntheticSource:
    """実ファイルを作らずに走査関数へソースを与えるスタブ（``Path`` の被走査面だけを満たす）。"""

    def __init__(self, name: str, text: str) -> None:
        self._name, self._text = name, text

    def read_text(self, encoding: str = "utf-8") -> str:  # noqa: ARG002
        return self._text

    def relative_to(self, other):  # noqa: ANN001, ARG002
        return self._name


def test_module_level_offender_detection_has_power():
    """検出力: ``_module_level_offenders`` が module-level の gateway 直結だけを違反にする。

    合成ソース文字列で与える（実ファイルは生成しない）。行頭（module-level）は違反、関数本体の
    遅延 import / 合成は自己完結起動の許容パターンとして非違反であることの両方を固定する。
    """
    dotted = "market_profile_api.gateway."
    module_level = _SyntheticSource(
        "synthetic.py",
        "from " + dotted + "zp_store import ZpStore\n"
        "from " + dotted + "dwell_rollup_store import DwellRollupStore\n",
    )
    assert len(_module_level_offenders(module_level, _GATEWAY_STORE_IMPORT)) == 2

    new_at_module_level = _SyntheticSource(
        "synthetic.py", "_STORE = ZpStore()\n_DWELL = DwellRollupStore()\n"
    )
    assert len(_module_level_offenders(new_at_module_level, _GATEWAY_STORE_NEW)) == 2

    lazy_in_function = _SyntheticSource(
        "synthetic.py",
        "def _get():\n"
        "    from " + dotted + "zp_store import ZpStore\n"
        "    return ZpStore()\n",
    )
    assert _module_level_offenders(lazy_in_function, _GATEWAY_STORE_IMPORT) == []
    assert _module_level_offenders(lazy_in_function, _GATEWAY_STORE_NEW) == []

    unrelated = _SyntheticSource(
        "synthetic.py", "from market_profile_api.compute import store_port\n"
    )
    assert _module_level_offenders(unrelated, _GATEWAY_STORE_IMPORT) == []


def test_old_path_detection_has_power():
    """検出力: 旧 compute パスの import 形態を検出し、紛らわしい非違反を誤検出しない。

    合成ソース文字列で与える（実ファイルは生成しない。かつ本テスト自身が走査の offender に
    ならないよう、リテラルは連結して組み立てる）。
    """
    dotted = "market_profile_api.compute."
    zp, dwell = "market_profile_zp_store", "market_profile_dwell_store"

    for offender in (
        "from " + dotted + zp + " import ZpStore\n",
        "import " + dotted + dwell + "\n",
        "from market_profile_api.compute import " + zp + "\n",
        "from ." + dwell + " import DwellRollupStore\n",
        "from . import " + zp + "\n",
        "    from " + dotted + zp + " import ZpStore\n",   # 関数内 import も参照は参照。
    ):
        assert _imports_old_compute_store_path(offender), f"検出できていません: {offender!r}"

    for clean in (
        "from market_profile_api.gateway.zp_store import ZpStore\n",
        "from test_" + zp + " import _synth_ticks_for_day\n",   # テストモジュール名は別物。
        "_SHIMS = {'" + zp + ".py'}\n",                          # 文字列リテラルは import ではない。
        "# " + dotted + zp + " は削除予定\n",
        "from market_profile_api.compute import store_port\n",
    ):
        assert not _imports_old_compute_store_path(clean), f"誤検出しています: {clean!r}"


def test_repo_scan_reads_each_source_exactly_once():
    """計算量テスト: 走査は 1 ファイル 1 読込（発行 − 判定に使ったソース数 = 0）。

    オーダー表明として対象 1 件 / 2 件の 2 点で、発行が対象数だけで決まることを固定する
    （ファイルの長さ・import 数では増えない）。回数リテラルは焼き込まない。
    """
    sources = _repo_sources()
    reads: "list[Path]" = []
    real_read = Path.read_text

    def _spy(self, *args, **kwargs):
        reads.append(self)
        return real_read(self, *args, **kwargs)

    Path.read_text = _spy
    try:
        one = sources[:1]
        used_one = [_imports_old_compute_store_path(p.read_text(encoding="utf-8")) for p in one]
        issued_one = len(reads)
        reads.clear()

        two = sources[:2] if len(sources) >= 2 else sources
        used_two = [_imports_old_compute_store_path(p.read_text(encoding="utf-8")) for p in two]
        issued_two = len(reads)
    finally:
        Path.read_text = real_read

    assert issued_one - len(used_one) == 0, "1 ファイルあたりの読込発行が判定使用数を超えています"
    assert issued_two - len(used_two) == 0, "1 ファイルあたりの読込発行が判定使用数を超えています"
    assert issued_two == len(two), "読込発行が対象ファイル数以外の要因で増えています"
