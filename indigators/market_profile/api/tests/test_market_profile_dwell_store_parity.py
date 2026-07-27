"""ISSUE-040(b) byte 不変 回帰: DwellRollupStore 抽出の前後で compute_dwell_profile 出力が完全一致。

`tests/fixtures/dwell_profile_golden.json`（抽出**前**のコードで採取した黄金値）に対し、複数
symbol 窓 / metric / want_* フラグの入力集合を通した出力（POC/VA/bins/units/sessions/fine/today）が
byte 単位で一致することを assert する。加えて warm_dwell_cache → ディスク読取経路の結果が
直接計算と一致することを検証し、Repository 抽出がキャッシュ往復でも出力を変えないことを担保する。

この回帰は「ディスクキャッシュ Repository を別モジュール（DwellRollupStore）へ分離しても
公開関数 compute_dwell_profile の出力が変わってはならない」という不変条件を禁止テストとして固定する。

黄金値は抽出**前**のコードで採取し、本テスト内へ literal（compact JSON）として埋め込む（外部 .json
fixture はリポジトリの ``*.json`` gitignore に該当し得るため、自己完結させて clean checkout でも走る）。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from market_profile_api.compute import market_profile_dwell as mpd
# ISSUE-183 item5: 永続化設定（cache root / 形式版数）の単一情報源は gateway 側 cache_settings。
from market_profile_api.gateway import cache_settings as _mp_cache_settings

_DAY = 86400
_DAY0 = 1704067200
_HOT = 1000.0
_COLD = 1100.0

# 抽出前コードで採取した黄金値（compact JSON・POC/VA/bins/units/sessions/fine/today を byte 固定）。
_GOLDEN_JSON = r"""[{"case":{"symbol":"JP225","t0":1704067200,"t1":1704240000,"price_min":990.0,"price_max":1110.0,"n_bins":12,"va_pct":0.7,"bar_sec":86400,"now":1704931200,"metric":"dwell"},"out":{"bins":[{"price":995.0,"tpo":0,"norm":0.0},{"price":1005.0,"tpo":10800,"norm":1.0},{"price":1015.0,"tpo":0,"norm":0.0},{"price":1025.0,"tpo":0,"norm":0.0},{"price":1035.0,"tpo":0,"norm":0.0},{"price":1045.0,"tpo":0,"norm":0.0},{"price":1055.0,"tpo":0,"norm":0.0},{"price":1065.0,"tpo":0,"norm":0.0},{"price":1075.0,"tpo":0,"norm":0.0},{"price":1085.0,"tpo":0,"norm":0.0},{"price":1095.0,"tpo":0,"norm":0.0},{"price":1105.0,"tpo":0,"norm":0.0}],"poc":1005.0,"va_low":1005.0,"va_high":1005.0,"price_min":990.0,"price_max":1110.0,"tpo_units":10800,"n_bins":12}},{"case":{"symbol":"JP225","t0":1704067200,"t1":1704240000,"price_min":900.0,"price_max":1200.0,"n_bins":30,"va_pct":0.7,"bar_sec":86400,"now":1704931200,"metric":"count"},"out":{"bins":[{"price":905.0,"tpo":0,"norm":0.0},{"price":915.0,"tpo":0,"norm":0.0},{"price":925.0,"tpo":0,"norm":0.0},{"price":935.0,"tpo":0,"norm":0.0},{"price":945.0,"tpo":0,"norm":0.0},{"price":955.0,"tpo":0,"norm":0.0},{"price":965.0,"tpo":0,"norm":0.0},{"price":975.0,"tpo":0,"norm":0.0},{"price":985.0,"tpo":0,"norm":0.0},{"price":995.0,"tpo":0,"norm":0.0},{"price":1005.0,"tpo":90,"norm":1.0},{"price":1015.0,"tpo":0,"norm":0.0},{"price":1025.0,"tpo":0,"norm":0.0},{"price":1035.0,"tpo":0,"norm":0.0},{"price":1045.0,"tpo":0,"norm":0.0},{"price":1055.0,"tpo":0,"norm":0.0},{"price":1065.0,"tpo":0,"norm":0.0},{"price":1075.0,"tpo":0,"norm":0.0},{"price":1085.0,"tpo":0,"norm":0.0},{"price":1095.0,"tpo":0,"norm":0.0},{"price":1105.0,"tpo":6,"norm":0.0667},{"price":1115.0,"tpo":0,"norm":0.0},{"price":1125.0,"tpo":0,"norm":0.0},{"price":1135.0,"tpo":0,"norm":0.0},{"price":1145.0,"tpo":0,"norm":0.0},{"price":1155.0,"tpo":0,"norm":0.0},{"price":1165.0,"tpo":0,"norm":0.0},{"price":1175.0,"tpo":0,"norm":0.0},{"price":1185.0,"tpo":0,"norm":0.0},{"price":1195.0,"tpo":0,"norm":0.0}],"poc":1005.0,"va_low":1005.0,"va_high":1005.0,"price_min":900.0,"price_max":1200.0,"tpo_units":96,"n_bins":30}},{"case":{"symbol":"JP225","t0":1704153600,"t1":1704153600,"price_min":900.0,"price_max":1200.0,"n_bins":30,"va_pct":0.7,"bar_sec":86400,"now":1704931200,"metric":"count"},"out":{"bins":[{"price":905.0,"tpo":0,"norm":0.0},{"price":915.0,"tpo":0,"norm":0.0},{"price":925.0,"tpo":0,"norm":0.0},{"price":935.0,"tpo":0,"norm":0.0},{"price":945.0,"tpo":0,"norm":0.0},{"price":955.0,"tpo":0,"norm":0.0},{"price":965.0,"tpo":0,"norm":0.0},{"price":975.0,"tpo":0,"norm":0.0},{"price":985.0,"tpo":0,"norm":0.0},{"price":995.0,"tpo":0,"norm":0.0},{"price":1005.0,"tpo":30,"norm":1.0},{"price":1015.0,"tpo":0,"norm":0.0},{"price":1025.0,"tpo":0,"norm":0.0},{"price":1035.0,"tpo":0,"norm":0.0},{"price":1045.0,"tpo":0,"norm":0.0},{"price":1055.0,"tpo":0,"norm":0.0},{"price":1065.0,"tpo":0,"norm":0.0},{"price":1075.0,"tpo":0,"norm":0.0},{"price":1085.0,"tpo":0,"norm":0.0},{"price":1095.0,"tpo":0,"norm":0.0},{"price":1105.0,"tpo":2,"norm":0.0667},{"price":1115.0,"tpo":0,"norm":0.0},{"price":1125.0,"tpo":0,"norm":0.0},{"price":1135.0,"tpo":0,"norm":0.0},{"price":1145.0,"tpo":0,"norm":0.0},{"price":1155.0,"tpo":0,"norm":0.0},{"price":1165.0,"tpo":0,"norm":0.0},{"price":1175.0,"tpo":0,"norm":0.0},{"price":1185.0,"tpo":0,"norm":0.0},{"price":1195.0,"tpo":0,"norm":0.0}],"poc":1005.0,"va_low":1005.0,"va_high":1005.0,"price_min":900.0,"price_max":1200.0,"tpo_units":32,"n_bins":30}},{"case":{"symbol":"JP225","t0":1704067200,"t1":1704240000,"price_min":990.0,"price_max":1110.0,"n_bins":12,"va_pct":0.7,"bar_sec":86400,"now":1704931200,"metric":"dwell","want_today":true,"want_sessions":true,"want_fine":true},"out":{"bins":[{"price":995.0,"tpo":0,"norm":0.0},{"price":1005.0,"tpo":10800,"norm":1.0},{"price":1015.0,"tpo":0,"norm":0.0},{"price":1025.0,"tpo":0,"norm":0.0},{"price":1035.0,"tpo":0,"norm":0.0},{"price":1045.0,"tpo":0,"norm":0.0},{"price":1055.0,"tpo":0,"norm":0.0},{"price":1065.0,"tpo":0,"norm":0.0},{"price":1075.0,"tpo":0,"norm":0.0},{"price":1085.0,"tpo":0,"norm":0.0},{"price":1095.0,"tpo":0,"norm":0.0},{"price":1105.0,"tpo":0,"norm":0.0}],"poc":1005.0,"va_low":1005.0,"va_high":1005.0,"price_min":990.0,"price_max":1110.0,"tpo_units":10800,"n_bins":12,"fine":[0.0,10800.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"fine_kmin":99,"grid_w":10.0,"today":[0.0,3600.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"today_max":3600.0,"sessions":[{"date":"2024-01-01","tpo":[0.0,3600.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"poc":1005.0,"va_low":1005.0,"va_high":1005.0},{"date":"2024-01-02","tpo":[0.0,3600.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"poc":1005.0,"va_low":1005.0,"va_high":1005.0},{"date":"2024-01-03","tpo":[0.0,3600.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"poc":1005.0,"va_low":1005.0,"va_high":1005.0}]}}]"""


def _synthetic_master():
    secs, mids = [], []
    for d in range(3):
        base = _DAY0 + d * _DAY
        for i in range(30):
            secs.append(base + 7200 + 10 * i)
            mids.append(_HOT)
        secs.append(base + 72000)
        mids.append(_COLD)
        secs.append(base + 72600)
        mids.append(_COLD)
    return np.asarray(secs, dtype=np.int64), np.asarray(mids, dtype=np.float64)


def _make_loader(master_secs, master_mids):
    s = np.asarray(master_secs, dtype=np.int64)
    m = np.asarray(master_mids, dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    return _loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_mp_cache_settings, "DWELL_CACHE_ROOT", tmp_path / "mp_dwell_cache")
    monkeypatch.setattr(mpd, "_day_source_signature", lambda symbol, day_start: "")
    monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))
    # ISSUE-089: golden は旧表セマンティクス（窓ティック由来）の合成値＝表をピンして意味を保存。
    _secs, _ = _synthetic_master()
    _tbl = mpd._build_active_table(np.asarray(_secs, dtype=np.int64))
    monkeypatch.setattr(mpd, "_table_for_day", lambda _s, _d: _tbl)
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _golden_cases():
    return json.loads(_GOLDEN_JSON)


class TestDwellProfileByteParity:
    @pytest.mark.parametrize("idx", range(len(_golden_cases())))
    def test_output_matches_pre_extraction_golden(self, idx, monkeypatch):
        # 抽出前に採取した黄金値と byte 一致（POC/VA/bins/units/sessions/fine/today）。
        entry = _golden_cases()[idx]
        mpd._reset_caches()
        got = mpd.compute_dwell_profile(**entry["case"])
        assert json.loads(json.dumps(got)) == entry["out"]

    def test_warm_then_read_equals_direct_compute(self, tmp_path, monkeypatch):
        # warm_dwell_cache でディスクへ完了日を焼き、再計算経路（ディスク読取）が直接計算と一致する。
        case = dict(
            symbol="JP225", t0=_DAY0, t1=_DAY0 + 2 * _DAY,
            price_min=990.0, price_max=1110.0, n_bins=12, va_pct=0.70,
            bar_sec=_DAY, now=_DAY0 + 10 * _DAY, metric="dwell",
        )
        # 直接計算（コールド）。
        mpd._reset_caches()
        direct = mpd.compute_dwell_profile(**case)

        # warm: 3 完了日ぶんの疑似 parquet を列挙し、ディスクへ焼く。
        import pandas as pd
        days = [_DAY0, _DAY0 + _DAY, _DAY0 + 2 * _DAY]
        files = []
        for day_start in days:
            ts = pd.Timestamp(day_start, unit="s")
            p = tmp_path / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}" / "JP225_ticks.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            files.append(p)
        monkeypatch.setattr(mpd, "day_parquet_files", lambda lo, hi, symbol=None: files)
        mpd._reset_caches()
        mpd.warm_dwell_cache("JP225", now=_DAY0 + 10 * _DAY)

        # ディスク読取経路（メモリキャッシュは消してディスクから復元させる）。
        mpd._reset_caches()
        from_disk = mpd.compute_dwell_profile(**case)
        assert from_disk == direct
