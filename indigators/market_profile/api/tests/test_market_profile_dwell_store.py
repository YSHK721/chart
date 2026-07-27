"""DwellRollupStore（gateway/dwell_rollup_store.py）単体検証。

ISSUE-040(b): market_profile_dwell.py の SRP 違反（dwell 集計数学 + ディスクキャッシュ Repository
+ tick 読込の混在）を解消するため、ディスクキャッシュ Repository 責務を切り出した
:class:`DwellRollupStore` の単体テスト。save/load 往復・空日往復・未ヒット/破損/バージョン/グリッド
不整合・署名往復・tmp root 注入・ソースティック署名合成を検証する（集計数学は本体側テストが担保）。

設計方針（AAA）: Store は純 I/O。root/version/day_parquet_files は provider 注入で決定論化する
（既定結線は gateway/composition が `cache_settings.DWELL_CACHE_ROOT` / `DWELL_CACHE_VERSION` と
`market_profile_dwell.day_parquet_files` を provider に束ねて渡す＝ISSUE-183 item5）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ISSUE-183: 実体（gateway）を直参照する。旧 compute 側の再エクスポートシム経由をやめ、
#   compute → gateway の module-level 逆流を消費側からも断つ。
from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore
from market_profile_api.compute.rollup_dto import DayRollup

_DAY0 = 1704067200  # 2024-01-01 00:00 UTC。


def _store(tmp_path, *, root=None, grid_w=10.0, version=2, dpf=None):
    """provider を tmp へ束ねた Store を組む（root=None なら default_root にフォールバック）。"""
    default = tmp_path / "default_cache"
    return DwellRollupStore(
        root_provider=lambda: (tmp_path / root) if root is not None else None,
        default_root_provider=lambda: default,
        grid_w=grid_w,
        cache_version_provider=lambda: version,
        day_parquet_files=dpf if dpf is not None else (lambda lo, hi, symbol=None: []),
    )


# --------------------------------------------------------------------------- #
# cache_root / cache_path: 注入 root とパス構造
# --------------------------------------------------------------------------- #
class TestCacheRootAndPath:
    def test_root_provider_overrides_default(self, tmp_path):
        s = _store(tmp_path, root="injected")
        assert s.cache_root() == tmp_path / "injected"

    def test_falls_back_to_default_when_provider_none(self, tmp_path):
        s = _store(tmp_path, root=None)
        assert s.cache_root() == tmp_path / "default_cache"

    def test_cache_path_includes_symbol_version_grid_and_day(self, tmp_path):
        # ISSUE-089: version をパスへ含める（新旧コード併走時の同一ファイル書き合いを排除）。
        s = _store(tmp_path, root="c", grid_w=10.0)
        p = s.cache_path("JP225", _DAY0)
        ver = s._cache_version_provider()
        assert p == tmp_path / "c" / "JP225" / f"v{ver}" / "g10" / f"{_DAY0}.npz"


# --------------------------------------------------------------------------- #
# save / load 往復
# --------------------------------------------------------------------------- #
class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_kmin_and_variable_length_arrays(self, tmp_path):
        s = _store(tmp_path, root="c")
        roll = DayRollup(
            kmin=97,
            dwell=np.array([1.0, 0.0, 5.5, 2.25], dtype=float),
            cnt=np.array([3.0, 0.0, 7.0, 4.0], dtype=float),
        )
        path = s.cache_path("JP225", _DAY0)
        s.save_day_rollup(path, roll)
        loaded, _sig = s.load_day_rollup(path)
        assert loaded is not s.CACHE_MISS and loaded is not None
        assert loaded.kmin == 97
        assert np.array_equal(loaded.dwell, roll.dwell)
        assert np.array_equal(loaded.cnt, roll.cnt)
        # ISSUE-178: 層間 DTO は不変（読込側も write=False）。
        assert not loaded.dwell.flags.writeable and not loaded.cnt.flags.writeable

    def test_roundtrip_empty_day_is_none(self, tmp_path):
        s = _store(tmp_path, root="c")
        path = s.cache_path("JP225", _DAY0)
        s.save_day_rollup(path, None)
        loaded, _sig = s.load_day_rollup(path)
        assert loaded is None  # 「実データ無しの完了日」= 再計算不要（CACHE_MISS と区別）。

    def test_sig_roundtrips(self, tmp_path):
        s = _store(tmp_path, root="c")
        path = s.cache_path("JP225", _DAY0)
        roll = DayRollup(kmin=5, dwell=np.array([1.0]), cnt=np.array([1.0]))
        s.save_day_rollup(path, roll, "ticks:123:456")
        loaded, sig = s.load_day_rollup(path)
        assert loaded is not None and sig == "ticks:123:456"


# --------------------------------------------------------------------------- #
# load の fail-safe: 未ヒット / 破損 / version・grid 不整合
# --------------------------------------------------------------------------- #
class TestLoadFailSafe:
    def test_missing_file_returns_cache_miss(self, tmp_path):
        s = _store(tmp_path, root="c")
        path = s.cache_path("JP225", _DAY0)
        assert s.load_day_rollup(path)[0] is s.CACHE_MISS

    def test_corrupt_file_returns_cache_miss(self, tmp_path):
        s = _store(tmp_path, root="c")
        path = s.cache_path("JP225", _DAY0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-a-valid-npz")
        assert s.load_day_rollup(path)[0] is s.CACHE_MISS

    def test_version_mismatch_returns_cache_miss(self, tmp_path):
        # 保存時 version=2 → 読込時 version=999（provider を切替）で不整合を模す。
        version_holder = {"v": 2}
        s = DwellRollupStore(
            root_provider=lambda: tmp_path / "c",
            default_root_provider=lambda: tmp_path / "d",
            grid_w=10.0,
            cache_version_provider=lambda: version_holder["v"],
            day_parquet_files=lambda lo, hi, symbol=None: [],
        )
        path = s.cache_path("JP225", _DAY0)
        s.save_day_rollup(path, DayRollup(kmin=5, dwell=np.array([1.0]), cnt=np.array([1.0])))
        version_holder["v"] = 999
        assert s.load_day_rollup(path)[0] is s.CACHE_MISS

    def test_grid_mismatch_returns_cache_miss(self, tmp_path):
        s_save = _store(tmp_path, root="c", grid_w=10.0)
        path = s_save.cache_path("JP225", _DAY0)
        s_save.save_day_rollup(path, DayRollup(kmin=5, dwell=np.array([1.0]), cnt=np.array([1.0])))
        # 別グリッド幅の Store で同一ファイルを読む → 不整合で CACHE_MISS。
        s_load = _store(tmp_path, root="c", grid_w=25.0)
        assert s_load.load_day_rollup(path)[0] is s_load.CACHE_MISS


# --------------------------------------------------------------------------- #
# day_source_signature: ソースティック署名（無効化用）
# --------------------------------------------------------------------------- #
class TestDaySourceSignature:
    def test_empty_when_no_files(self, tmp_path):
        s = _store(tmp_path, root="c", dpf=lambda lo, hi, symbol=None: [])
        assert s.day_source_signature("JP225", _DAY0) == ""

    def test_composes_name_mtime_size(self, tmp_path):
        f = tmp_path / "JP225_ticks.parquet"
        f.write_bytes(b"abc")
        st = f.stat()
        s = _store(tmp_path, root="c", dpf=lambda lo, hi, symbol=None: [f])
        expected = f"{f.name}:{int(st.st_mtime)}:{int(st.st_size)}"
        assert s.day_source_signature("JP225", _DAY0) == expected

    def test_passes_normalized_day_to_enumerator(self, tmp_path):
        seen = {}

        def _dpf(lo, hi, symbol=None):
            seen["lo"], seen["hi"], seen["symbol"] = lo, hi, symbol
            return []

        s = _store(tmp_path, root="c", dpf=_dpf)
        s.day_source_signature("JP225", _DAY0)
        # ISSUE-078: セッション日は UTC 2 日跨ぎ＝正規化日〜翌日 (day, day+1, symbol=...) を渡す。
        # ISSUE-183: 列挙契約は UNIX 秒 int（旧 pd.Timestamp 契約と同値の日始端）。
        assert seen["symbol"] == "JP225"
        assert seen["lo"] == int(pd.Timestamp(_DAY0, unit="s").normalize().timestamp())
        assert seen["hi"] == int(
            (pd.Timestamp(_DAY0, unit="s").normalize() + pd.Timedelta(days=1)).timestamp()
        )


# --------------------------------------------------------------------------- #
# day_source_signature: セッション日対応（ISSUE-078）
#   セッション日 [start, end) は UTC 暦日を 2 日跨ぐ（境界=夏21:00/冬22:00 UTC）ため、
#   署名は start の UTC 日と翌 UTC 日の両 parquet を覆う（片方の更新でも無効化される）。
# --------------------------------------------------------------------------- #
class TestSignatureCoversSessionSpan:
    def test_signature_lists_two_utc_days(self, tmp_path):
        calls = []

        def dpf(lo, hi, symbol=None):
            calls.append((lo, hi))
            return []

        s = _store(tmp_path, root="sig", dpf=dpf)
        # セッション日始端例: 2026-07-12 21:00 UTC（月曜セッション・夏）。
        s.day_source_signature("JP225", 1783890000)
        assert len(calls) == 1
        lo, hi = calls[0]
        # ISSUE-183: 列挙契約は UNIX 秒 int（旧 pd.Timestamp 契約と同値の日始端）。
        assert lo == int(pd.Timestamp("2026-07-12").timestamp())
        assert hi == int(pd.Timestamp("2026-07-13").timestamp())  # 翌 UTC 日まで覆う（セッション跨ぎ）。
