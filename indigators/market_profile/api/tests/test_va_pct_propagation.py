"""ISSUE-260: VA 比率（``va``）が全プロファイル生成経路へ届き、決定権が 1 箇所に閉じることの検定。

破れていた不変条件（是正前）:
  「VA 比率」という 1 つの業務パラメータの決定権が 4 面へ分散していた——
  UI（catalog の param 既定 0.70）／ ``market_profile_controller._DEFAULT_VA`` ／
  ``tf_period_columns`` の直書き 0.70（3 箇所）／ front ``DwellAccumulator`` の ``VA_PCT``。
  帰結: ``/market_profile`` の非増分 refresh だけが設定を反映し、tf-period 列と増分成長は
  UI をどう操作しても 0.70 に固定される（＝効かないツマミ）。

本テストが固定する不変条件（3 層）:

1. 単一情報源（構造）
   - VA 比率のリテラルは :data:`market_profile.VA_PCT_DEFAULT` ただ 1 箇所にしか存在しない
     （Python は AST の数値定数、front JS はソース文字列で走査する）。
   - プロファイル生成の入口関数は ``va_pct`` を**必須引数**として要求する（既定値を持たない
     ＝呼出側が決定を明示せざるを得ない）。

2. 透過（識別力の本体）
   - tf-period 列（count 日次 / count バケット / zp）が ``va`` を反映して応答を変える。
   - 反映値は参照実装と同一規則（``_value_area_sparse`` / ``_value_area``）を同一比率で
     適用した値に一致する（＝「変わりはするが別物」を排除する）。
   - 増分成長経路（``/market_profile_forming`` base=1）が**解決済み比率**を応答に載せ、
     それが参照実装（``/market_profile``）の解決値と同一である。

3. 後方互換
   - ``va`` 省略時の応答は既定比率指定時と byte 等価であり、完了日ディスクキャッシュの
     配置も従来と同一（＝既存キャッシュを孤児化しない）。異なる ``va`` は別配置になる。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import numpy as np
import pytest

from market_profile_api.compute import market_profile as mp
from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.compute import market_profile_zp as mpz
from market_profile_api.compute import market_profile_zp as _zp_mod
from market_profile_api.compute.rollup_dto import ZpRollup
from market_profile_api.compute import tf_period_columns as tfc
from market_profile_api.compute import tf_period_profile as tfp
from market_profile_api.controller import market_profile_controller as mpc
from market_profile_api.controller import market_profile_forming_controller as mpfc
from market_profile_api.controller import tf_period_profile_controller as ctl

_HERE = Path(__file__).resolve()
_MP_API_PKG = _HERE.parents[1] / "market_profile_api"
_MP_WEB_JS = _HERE.parents[2] / "web" / "js"
#: 単一情報源が置かれる唯一のファイル（ここ以外に VA 比率のリテラルがあってはならない）。
_SINGLE_SOURCE_PY = _MP_API_PKG / "compute" / "market_profile.py"


# --------------------------------------------------------------------------- #
# 1. 単一情報源（構造ガード）
# --------------------------------------------------------------------------- #
def _float_constants(path: Path) -> "list[tuple[int, float]]":
    """Python ソース中の数値定数（int/float・bool 除く）を (行, 値) で列挙する。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "list[tuple[int, float]]" = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            out.append((node.lineno, float(node.value)))
    return out


def test_va_ratio_literal_exists_only_in_the_single_source():
    """VA 比率のリテラル（0.7）は単一情報源の宣言 1 行にしか現れない（Python 側）。"""
    offenders: "list[str]" = []
    for path in sorted(_MP_API_PKG.rglob("*.py")):
        for lineno, value in _float_constants(path):
            if value != 0.70:
                continue
            if path == _SINGLE_SOURCE_PY:
                continue
            offenders.append(f"{path.relative_to(_MP_API_PKG)}:{lineno}")
    assert not offenders, (
        "VA 比率の第 2 定義が残っている（決定権の分散＝ISSUE-260 の原因そのもの）: "
        + ", ".join(offenders)
    )


def test_single_source_declares_the_default_exactly_once():
    """単一情報源自身も宣言は 1 行のみ（同ファイル内での再掲を許さない）。"""
    lines = [ln for ln, v in _float_constants(_SINGLE_SOURCE_PY) if v == 0.70]
    assert len(lines) == 1, f"VA 比率の宣言が 1 行でない: lines={lines}"
    assert mp.VA_PCT_DEFAULT == 0.70


def test_front_js_has_no_va_ratio_literal():
    """front（MP web js）に VA 比率のリテラルが無い（既定は Python 生成物から読む）。"""
    pattern = re.compile(r"0\.70?(?![0-9])")
    offenders: "list[str]" = []
    scanned = 0
    for path in sorted(_MP_WEB_JS.rglob("*.js")):
        if path.name.endswith("_generated.js"):
            continue  # 生成物（Python 唯一源からの写像・鮮度は parity 検定が担保）。
        scanned += 1
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_MP_WEB_JS)}:{i}: {line.strip()}")
    assert scanned > 10, f"front JS を走査できていない（テストの前提崩壊）: {_MP_WEB_JS}"
    assert not offenders, (
        "front に VA 比率のリテラル（第 2 定義）が残っている:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "fn",
    [
        mp.compute_candle_profile,
        mpd.compute_dwell_profile,
        mpz.compute_zp_profile,
        tfp.tf_period_profiles,
        tfc.day_columns_zp_compute,
        tfc.bucket_columns_compute,
        tfc.bucket_columns_zp_compute,
    ],
    ids=lambda f: f.__name__,
)
def test_profile_producers_require_va_pct(fn):
    """プロファイル生成関数は ``va_pct`` を必須引数として要求する（既定値を持たない）。"""
    param = inspect.signature(fn).parameters.get("va_pct")
    assert param is not None, f"{fn.__name__} が va_pct を受け取らない"
    assert param.default is inspect.Parameter.empty, (
        f"{fn.__name__} が va_pct の既定値を持つ（既定の第 2 定義＝決定権の分散）"
    )


# --------------------------------------------------------------------------- #
# 2. 透過（tf-period 列）
# --------------------------------------------------------------------------- #
_UNIT_1M = 0.0255  # ISSUE-073: 1m のビニング解像度（_UNIT_BY_TF）。


def _fake_ticks(_symbol, start, end):
    """1 周期（1m）に 10 tick。VA が比率で明確に変わる非対称な分布にする。"""
    secs = np.arange(0, 10, dtype=np.int64)
    mids = np.array([100.0, 100.0, 100.0, 100.0, 100.0,
                     100.0, 100.1, 100.2, 100.3, 100.4])
    m = (secs >= start) & (secs < end)
    return secs[m], mids[m]


@pytest.fixture()
def tfp_env(monkeypatch):
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)  # ディスク無効（テスト隔離）。
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    yield
    ctl._reset_tf_period_cache()


def _tfp_columns(va):
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1m", 0, 60, now=1e12, va=va
    )
    assert st == 200, body
    return body["columns"]


def _reference_va_from_levels(levels, va_pct):
    """参照実装と同一規則（count 列＝``_value_area_sparse``）を応答 levels へ独立適用する。"""
    prices = np.asarray([p for p, _ in levels], dtype=float)
    counts = np.asarray([c for _, c in levels])
    poc_i = int(counts.argmax())
    lo, hi = tfp._value_area_sparse(counts, poc_i, va_pct)
    return round(float(prices[lo]), 4), round(float(prices[hi]), 4)


def test_tf_period_count_columns_follow_va(tfp_env):
    """count 列の VA が ``va`` を反映する（是正前は 0.70 固定＝両者一致で Red）。"""
    narrow = _tfp_columns(0.30)[0]
    wide = _tfp_columns(0.95)[0]
    assert (narrow["va_low"], narrow["va_high"]) != (wide["va_low"], wide["va_high"]), (
        "va を変えても tf-period 列の VA が変わらない（設定が届いていない）"
    )
    span_narrow = narrow["va_high"] - narrow["va_low"]
    span_wide = wide["va_high"] - wide["va_low"]
    assert span_narrow < span_wide


@pytest.mark.parametrize("va", [0.30, 0.55, 0.70, 0.95])
def test_tf_period_count_va_matches_reference_rule(tfp_env, va):
    """応答の VA は、同一比率で参照規則を独立適用した値と一致する。"""
    col = _tfp_columns(va)[0]
    expected = _reference_va_from_levels(col["levels"], va)
    assert (col["va_low"], col["va_high"]) == expected


def test_tf_period_omitted_va_equals_default(tfp_env):
    """``va`` 省略は既定比率の明示指定と byte 等価（後方互換）。"""
    omitted = _tfp_columns(None)
    explicit = _tfp_columns(mp.VA_PCT_DEFAULT)
    assert omitted == explicit


def test_tf_period_cache_does_not_mix_different_va(tfp_env):
    """完了日キャッシュが va を跨いで混線しない（0.7→0.3→0.7 で 1 回目と 3 回目が一致）。"""
    first = _tfp_columns(0.70)
    other = _tfp_columns(0.30)
    again = _tfp_columns(0.70)
    assert first == again
    assert first != other


def test_completed_day_cache_is_reused_within_same_va(tfp_env, monkeypatch):
    """同一 va の完了日は 2 回目でティックを読まない（キャッシュ有効性の維持）。"""
    calls = {"n": 0}

    def counting(symbol, start, end):
        calls["n"] += 1
        return _fake_ticks(symbol, start, end)

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", counting)
    _tfp_columns(0.55)
    n1 = calls["n"]
    _tfp_columns(0.55)
    assert calls["n"] == n1 and n1 > 0


def test_default_va_keeps_existing_disk_layout_and_others_diverge():
    """既定 va のディスク配置は従来と同一（既存キャッシュを孤児化しない）・他の va は別配置。"""
    default_paths = ctl._disk_tf_variants("1m", 10.0, mp.VA_PCT_DEFAULT)
    assert default_paths == ("1m/s1/g10", "1m/s1/g10", f"1m/s3/zp-v{ctl._cache_settings.ZP_CACHE_VERSION}")
    other = ctl._disk_tf_variants("1m", 10.0, 0.55)
    assert len(set(other) & set(default_paths)) == 0, (
        "異なる va が既定 va と同一のディスク配置を共有している（内容の異なる列が混線する）"
    )


def test_bucket_count_columns_follow_va():
    """1W/1M バケット（count 合成）の VA も ``va`` を反映する。"""
    levels = [[100.0, 5], [110.0, 4], [120.0, 3], [130.0, 2], [140.0, 1]]
    day_cols = [{
        "time": 0, "levels": levels, "poc": 100.0, "va_low": 100.0, "va_high": 100.0,
        "price_min": 100.0, "price_max": 140.0, "tpo_units": 15,
    }]

    def day_columns_fn(*_a, **_k):
        return 10.0, day_cols

    def bucket(va_pct):
        _u, cols = tfc.bucket_columns_compute(
            "JP225", "1W", "2026-07-10", 0, 1e12, None,
            va_pct=va_pct, day_columns_fn=day_columns_fn,
        )
        return cols[0]["va_low"], cols[0]["va_high"]

    assert bucket(0.30) != bucket(0.95)


def _zp_fakes(monkeypatch, closes):
    """zp 集計の外部依存（格子・帰無・観測）を決定論の fake に差し替える。

    z の算出（``_fine_z``）・POC*・VA（``_value_area``）は**本物**を通す＝比率が結果に効くことを
    値レベルで測れる（構造検定だけだと ``_value_area(..., VA_PCT_DEFAULT)`` の再直書きを見逃す）。
    """
    n_cells = 6
    monkeypatch.setattr(_zp_mod, "_mgrid_of_day", lambda *a, **k: (closes, float(closes[0])))
    monkeypatch.setattr(_zp_mod, "_hist_step_matrix", lambda *a, **k: np.ones((3, 3)))
    monkeypatch.setattr(
        _zp_mod, "null_b_period_moments",
        lambda S, open_d, klo, khi, bounds, rng=None, m_reps=None: [
            (np.full(khi - klo + 1, 1.0), np.full(khi - klo + 1, 1.0)) for _ in bounds
        ],
    )
    obs = np.array([1.0, 3.0, 9.0, 2.0, 1.0, 1.0])[:n_cells]
    monkeypatch.setattr(
        _zp_mod, "obs_cell_counts",
        lambda closes_, klo, khi, col_lo=0, col_hi=None: np.resize(obs, khi - klo + 1),
    )


def _expected_zp_va(closes, va_pct):
    """fake が生む z 分布に対し、参照規則（``_value_area``）を同一比率で独立適用した期待値。"""
    klo = int(np.floor(np.log(float(closes.min())) / _zp_mod.W_LOG))
    khi = int(np.floor(np.log(float(closes.max())) / _zp_mod.W_LOG))
    centers = np.exp((klo + np.arange(khi - klo + 1) + 0.5) * _zp_mod.W_LOG)
    obs = np.resize(np.array([1.0, 3.0, 9.0, 2.0, 1.0, 1.0]), khi - klo + 1)
    z = _zp_mod._fine_z(obs, np.full(khi - klo + 1, 1.0), np.full(khi - klo + 1, 1.0))
    lo, hi = mp._value_area(np.maximum(z, 0.0), centers, va_pct)
    return round(float(lo), 6), round(float(hi), 6)


def test_zp_day_columns_va_matches_reference_rule(monkeypatch):
    """zp 日次列の VA が引数比率で決まり、参照規則の独立適用と一致する（値レベル）。"""
    closes = np.linspace(100.0, 100.05, 60)
    _zp_fakes(monkeypatch, closes)
    out = {}
    for va in (0.30, 0.95):
        # 周期は「始端が本セッション日に属し、かつセッション開始 (SESSION_OPEN_MOD 分) 以降」に
        #   限られるため、窓は 2 周期ぶん（[0, 7200)）を与えて 1 本の実周期を成立させる。
        _unit, cols = tfc.day_columns_zp_compute(
            "JP225", 3600, 0, 7200, True, 1e12, None, va_pct=va
        )
        assert cols, "zp 列が生成されていない（fake の前提崩壊）"
        out[va] = (cols[0]["va_low"], cols[0]["va_high"])
        assert out[va] == _expected_zp_va(closes, va)
    assert out[0.30] != out[0.95], "va を変えても zp 日次列の VA が変わらない"


def test_zp_bucket_columns_va_matches_reference_rule(monkeypatch):
    """zp バケット列（1W/1M 合成）の VA も引数比率で決まる（値レベル）。"""
    closes = np.linspace(100.0, 100.05, 60)
    klo = int(np.floor(np.log(float(closes.min())) / _zp_mod.W_LOG))
    khi = int(np.floor(np.log(float(closes.max())) / _zp_mod.W_LOG))
    size = khi - klo + 1
    roll = ZpRollup(
        kmin=klo,
        obs=np.resize(np.array([1.0, 3.0, 9.0, 2.0, 1.0, 1.0]), size),
        mean=np.full(size, 1.0),
        var=np.full(size, 1.0),
    )
    monkeypatch.setattr(tfc, "period_session_labels", lambda tf, label: ["2026-07-10"])
    monkeypatch.setattr(tfc, "session_label_to_start", lambda label: 0)
    monkeypatch.setattr(tfc, "next_session_day_start", lambda day: 86400)
    monkeypatch.setattr(_zp_mod, "_zp_day_rollup", lambda *a, **k: roll)
    out = {}
    for va in (0.30, 0.95):
        _unit, cols = tfc.bucket_columns_zp_compute(
            "JP225", "1W", "2026-07-10", 0, 1e12, None, va_pct=va
        )
        assert cols
        out[va] = (cols[0]["va_low"], cols[0]["va_high"])
        assert out[va] == _expected_zp_va(closes, va)
    assert out[0.30] != out[0.95], "va を変えても zp バケット列の VA が変わらない"


def test_zp_columns_receive_the_resolved_va(monkeypatch):
    """zp 経路（日次・バケット）へ controller が解決済み比率をそのまま渡す。"""
    seen: "list[float]" = []

    def spy_day(symbol, tf_sec, day_start, day_end, completed, now_val, live_ticks, *, va_pct):
        seen.append(va_pct)
        return 1.0, []

    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    monkeypatch.setattr(tfc, "day_columns_zp_compute", spy_day)
    ctl._reset_tf_period_cache()
    st, _ = ctl.handle_tf_period_profile(
        "jp225_tick", "1h", 0, 3600, now=1e12, src="zp", va="0.55"
    )
    assert st == 200
    assert seen and all(v == 0.55 for v in seen), seen


# --------------------------------------------------------------------------- #
# 3. 増分成長経路（forming）— 解決済み比率の配信
# --------------------------------------------------------------------------- #
def _forming_body(monkeypatch, va):
    """base=1 の forming 応答を返す（tick I/O は無効化し、比率の配信のみを見る）。"""
    monkeypatch.setattr(
        mpfc._mpf, "forming_ticks",
        lambda symbol, tf, now, since=None: {"formingStart": 0, "ticks": [], "now": 0},
    )
    monkeypatch.setattr(mpfc._mpf, "get_active_table", lambda symbol: [[1] * 24] * 7)
    monkeypatch.setattr(
        mpfc, "handle_market_profile",
        lambda *a, **k: (200, {"ok": True, "profile": {
            "fine": [], "fine_kmin": 0, "price_min": 0.0, "price_max": 1.0,
            "n_bins": 1, "grid_w": 10.0, "tpo_units": 0,
        }}),
    )
    st, body = mpfc.handle_market_profile_forming(
        "jp225_tick", "1m", None, 1, 0, None, va, None
    )
    assert st == 200, body
    return body


@pytest.mark.parametrize("raw,expected", [
    (None, mp.VA_PCT_DEFAULT),   # 省略＝既定（後方互換）。
    ("0.55", 0.55),              # クエリ文字列。
    (0.42, 0.42),                # 数値。
    ("abc", mp.VA_PCT_DEFAULT),  # 不正＝既定へ丸め（参照実装と同一規約）。
    ("5", 1.0),                  # 上限クランプ。
    ("0", mp.VA_PCT_MIN),        # 下限クランプ。
])
def test_forming_publishes_resolved_va(monkeypatch, raw, expected):
    """base=1 応答が**解決済み比率**を載せる（front はこれに従い自前の既定を持たない）。"""
    body = _forming_body(monkeypatch, raw)
    assert body["vaPct"] == expected


def test_forming_resolution_equals_reference_path(monkeypatch):
    """forming が配信する比率は、参照実装（/market_profile）が使う解決値と同一。"""
    captured: "list[float]" = []

    def spy_handle(*_a, **kwargs):
        captured.append(mp.resolve_va_pct(kwargs.get("va")))
        return 200, {"ok": True, "profile": {
            "fine": [], "fine_kmin": 0, "price_min": 0.0, "price_max": 1.0,
            "n_bins": 1, "grid_w": 10.0, "tpo_units": 0,
        }}

    monkeypatch.setattr(
        mpfc._mpf, "forming_ticks",
        lambda symbol, tf, now, since=None: {"formingStart": 0, "ticks": [], "now": 0},
    )
    monkeypatch.setattr(mpfc._mpf, "get_active_table", lambda symbol: [[1] * 24] * 7)
    monkeypatch.setattr(mpfc, "handle_market_profile", spy_handle)
    st, body = mpfc.handle_market_profile_forming(
        "jp225_tick", "1m", None, 1, 0, None, "0.55", None
    )
    assert st == 200
    assert captured == [body["vaPct"]] == [0.55]


def test_reference_path_uses_the_shared_resolver(monkeypatch):
    """参照実装（/market_profile）の比率解決も単一情報源の :func:`resolve_va_pct` を通る。

    識別力: 解決規則の写しを controller 側に持っていると、resolver を差し替えても値が変わらず
    Red になる（値の一致だけを見ると「たまたま同じ」を通してしまう）。
    """
    seen: "list[float]" = []
    monkeypatch.setattr(mpc, "resolve_va_pct", lambda raw: 0.123)
    monkeypatch.setattr(
        mpc, "compute_candle_profile",
        lambda candles, n_bins=60, *, va_pct, **k: seen.append(va_pct) or {
            "bins": [], "poc": 0.0, "va_low": 0.0, "va_high": 0.0,
            "price_min": 0.0, "price_max": 0.0, "tpo_units": 0, "n_bins": n_bins,
        },
    )
    monkeypatch.setattr(mpc.dataset, "load_candles", lambda *a, **k: [])
    st, _ = mpc.handle_market_profile("jp225_tick", "1D", None, None, "0.55")
    assert st == 200
    assert seen == [0.123]
