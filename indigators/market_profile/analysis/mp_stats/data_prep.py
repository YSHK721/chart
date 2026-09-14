"""データ準備層 — M1 CSV → 日次特徴量（OC/CO・RV・ブラケット TPO・季節性 ŝ(b)・時間変更ブラケット）。

規約（計画確定値）:
  - 営業日 = UTC 暦日。セッション窓 = 分オフセット [61, 1438]（01:01〜23:58 UTC、
    simulator/adapter/calendar/session_calendar.py の tradeable 窓と同値）。
  - ブラケット = 01:00 起点 30 分刻み（暦時間 K=46。先頭・末尾はセッション窓の切り落としで
    29 分の部分ブラケット。長さは bracket_minutes() が単一情報源）。
  - RV^OC_d = 同一 UTC 日内・間隔ちょうど 300 秒の 5 分境界 close ペアの log リターン二乗和
    （simulator/usecase/estimate_weekly_band.py aggregate_weekly_rs の規則の日次版）。
  - TPO: 行幅は日次適応 row_w=(high_d−low_d)/n_rows（grid_w 指定時は固定幅）。
    N_d(p) = ブラケット [min low, max high] が行中心を覆う本数。POC はタイ時に日中間値
    (high_d+low_d)/2 へ最も近い行（market_profile._value_area の決定論規約に倣う）。
  - ffill 変種: セッション窓内の欠測分を直前 close の仮想バー（high=low=close）で補完。
    日先頭の欠測は当日最初のバーの open で埋める（前日値の持ち込みはしない）。
  - ルックアヘッド排除: 全特徴量は当日の完結セッションのみから計算する。
    s_hat_expanding は day d に対し day < d のみ（ウォームアップ 250 日）を用いる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SESSION_OPEN_MOD = 61      # 01:01 UTC（分オフセット）
SESSION_CLOSE_MOD = 1438   # 23:58 UTC（最終取引可能分）
BRACKET_BASE_MOD = 60      # ブラケット起点 01:00
BRACKET_MIN = 30
K_BRACKETS = (SESSION_CLOSE_MOD - BRACKET_BASE_MOD) // BRACKET_MIN + 1  # 46
MIN_BARS_PER_DAY = 60
FIVE_MIN_SECONDS = 300
DAY_SECONDS = 86400


# --------------------------------------------------------------------------- #
# 変種
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VariantSpec:
    """TPO 構築の頑健性変種。grid_w 指定時は n_rows を無視し固定価格幅の行を使う。"""

    fill: str            # "raw" | "ffill"
    n_rows: int          # 20 | 40 | 80（日次適応行数）
    grid_w: float | None = None  # 固定行幅 (pt)。指定時は n_rows 非使用

    @property
    def key(self) -> str:
        if self.grid_w is not None:
            return f"{self.fill}_grid{self.grid_w:g}"
        return f"{self.fill}_r{self.n_rows}"


VARIANTS: "tuple[VariantSpec, ...]" = (
    VariantSpec("raw", 20),
    VariantSpec("raw", 40),
    VariantSpec("raw", 80),
    VariantSpec("ffill", 20),
    VariantSpec("ffill", 40),
    VariantSpec("ffill", 80),
    VariantSpec("raw", 0, grid_w=10.0),  # 付録: 固定 10pt 行（クロスチェック）
)
PRIMARY = VariantSpec("raw", 40)


# --------------------------------------------------------------------------- #
# ロード / セッション切出し
# --------------------------------------------------------------------------- #
def load_m1(csv_path, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """M1 CSV を全期間ロードする（naive=UTC とみなす。tail キャップ回避のため直接 read_csv）。

    Returns:
        columns=[epoch(int64 秒), open, high, low, close] の DataFrame（時刻昇順）。
    """
    df = pd.read_csv(csv_path, usecols=["date", "open", "high", "low", "close"])
    ts = pd.to_datetime(df["date"], utc=True)
    if start is not None:
        df = df[ts >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[ts < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    ts = ts.loc[df.index]
    # 分解能（ns/us/s）非依存の UNIX 秒変換（pandas 3.x は to_datetime の単位が ns 固定でない）
    epoch_sec = (ts - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    out = pd.DataFrame(
        {
            "epoch": epoch_sec.to_numpy(np.int64),
            "open": df["open"].to_numpy(float),
            "high": df["high"].to_numpy(float),
            "low": df["low"].to_numpy(float),
            "close": df["close"].to_numpy(float),
        }
    )
    return out.sort_values("epoch", kind="stable").reset_index(drop=True)


@dataclass(frozen=True)
class SessionData:
    """セッション窓内 M1 バーのフラット配列（日別スライスは starts/ends）。"""

    day_epoch: "np.ndarray"   # (D,) int64 各営業日 00:00 UTC
    starts: "np.ndarray"      # (D,) int64 バー配列内の開始 index
    ends: "np.ndarray"        # (D,) int64 終了 index（半開）
    mod: "np.ndarray"         # (N,) int32 分オフセット（61..1438）
    epoch: "np.ndarray"       # (N,) int64
    open: "np.ndarray"        # (N,) float64
    high: "np.ndarray"
    low: "np.ndarray"
    close: "np.ndarray"

    @property
    def n_days(self) -> int:
        return int(self.day_epoch.size)


def build_session_data(m1: pd.DataFrame, *, min_bars: int = MIN_BARS_PER_DAY) -> SessionData:
    """セッション窓 [01:01, 23:58] のバーのみ残し、バー数 < min_bars の日を除外する。"""
    epoch = m1["epoch"].to_numpy()
    mod = ((epoch % DAY_SECONDS) // 60).astype(np.int32)
    keep = (mod >= SESSION_OPEN_MOD) & (mod <= SESSION_CLOSE_MOD)
    epoch = epoch[keep]
    mod = mod[keep]
    o = m1["open"].to_numpy(float)[keep]
    h = m1["high"].to_numpy(float)[keep]
    lo = m1["low"].to_numpy(float)[keep]
    c = m1["close"].to_numpy(float)[keep]

    day = (epoch // DAY_SECONDS) * DAY_SECONDS
    day_u, starts_u = np.unique(day, return_index=True)
    ends_u = np.append(starts_u[1:], day.size)
    counts = ends_u - starts_u
    ok = counts >= min_bars
    # 除外日のバーも落としてフラット配列を再構成する
    keep_rows = np.zeros(day.size, dtype=bool)
    for s, e, k in zip(starts_u, ends_u, ok):
        if k:
            keep_rows[s:e] = True
    epoch, mod = epoch[keep_rows], mod[keep_rows]
    o, h, lo, c = o[keep_rows], h[keep_rows], lo[keep_rows], c[keep_rows]
    day = day[keep_rows]
    day_u, starts_u = np.unique(day, return_index=True)
    ends_u = np.append(starts_u[1:], day.size)
    return SessionData(
        day_epoch=day_u, starts=starts_u, ends=ends_u,
        mod=mod, epoch=epoch, open=o, high=h, low=lo, close=c,
    )


# --------------------------------------------------------------------------- #
# ブラケット定義
# --------------------------------------------------------------------------- #
def calendar_bracket_of_mod(mod: "np.ndarray") -> "np.ndarray":
    """分オフセット → 暦時間ブラケット index（0..K-1）。"""
    return ((np.asarray(mod) - BRACKET_BASE_MOD) // BRACKET_MIN).astype(np.int32)


def bracket_minutes() -> "np.ndarray":
    """各暦時間ブラケットのセッション窓内分数（単一情報源・部分ブラケット対応）。"""
    mods = np.arange(SESSION_OPEN_MOD, SESSION_CLOSE_MOD + 1)
    return np.bincount(calendar_bracket_of_mod(mods), minlength=K_BRACKETS).astype(float)


def tau_bracket_of_mod(s2_by_bracket: "np.ndarray") -> "np.ndarray":
    """ビジネス時間ブラケット割当を返す（分オフセット 0..1439 → τ ブラケット index）。

    τ(t)=∫ŝ² の累積が等分になるよう K 個へ再分割する（Ané-Geman 型時間変更）。
    セッション窓外の分は -1。s2 が全ゼロ/欠損なら暦時間割当へフォールバック。
    """
    s2 = np.nan_to_num(np.asarray(s2_by_bracket, dtype=float), nan=0.0)
    mods = np.arange(SESSION_OPEN_MOD, SESSION_CLOSE_MOD + 1)
    w = s2[calendar_bracket_of_mod(mods)]
    total = float(w.sum())
    out = np.full(1440, -1, dtype=np.int32)
    if total <= 0:
        out[mods] = calendar_bracket_of_mod(mods)
        return out
    cum = np.cumsum(w) - w / 2.0  # 各分の中点累積
    idx = np.minimum((cum / total * K_BRACKETS).astype(np.int32), K_BRACKETS - 1)
    out[mods] = idx
    return out


# --------------------------------------------------------------------------- #
# 日次特徴量
# --------------------------------------------------------------------------- #
@dataclass
class DailyFeatures:
    """全ステップ共通の日次テーブル（配列は日付昇順・同一 index）。"""

    day: "np.ndarray"            # (D,) int64 epoch（day_start UTC）
    o: "np.ndarray"              # (D,) 日 open（01:01 以降最初のバーの open）
    c: "np.ndarray"              # (D,) 日 close（23:58 以前最後のバーの close）
    day_high: "np.ndarray"
    day_low: "np.ndarray"
    r_oc: "np.ndarray"           # ln(C/O)
    r_co: "np.ndarray"           # ln(O_d/C_{d-1})、先頭は NaN
    co_span_days: "np.ndarray"   # CO が跨いだ暦日数（1=翌日, >=3=週末跨ぎ）
    rv_oc: "np.ndarray"          # 5 分 RV（日中のみ）
    n_bars: "np.ndarray"         # セッション窓内バー数
    bracket_minutes: "np.ndarray"        # (K,)
    br_ret: "np.ndarray"         # (D,K) ブラケット close-to-close log リターン（欠測 NaN）
    br_rv: "np.ndarray"          # (D,K) ブラケット内 1 分 r² 和（欠測 NaN）
    br_ndistinct: "np.ndarray"   # (D,K) 相異なる close 値の個数（欠測 0）
    br_maxrun: "np.ndarray"      # (D,K) 同一 close の最長連続反復長（欠測 0）
    br_hi: "np.ndarray | None" = None   # (D,K) raw ブラケット高値（欠測 NaN・Step4 多窓 TPO 用）
    br_lo: "np.ndarray | None" = None   # (D,K) raw ブラケット安値（欠測 NaN）
    conc: "dict[str, np.ndarray]" = field(default_factory=dict)        # max_p N_d(p)
    poc_price: "dict[str, np.ndarray]" = field(default_factory=dict)   # POC 行中心価格
    poc_bracket_median: "dict[str, np.ndarray]" = field(default_factory=dict)
    poc_bracket_first: "dict[str, np.ndarray]" = field(default_factory=dict)
    poc_touch_primary: "np.ndarray | None" = None   # (D,K) 主変種: ブラケットが POC 行を覆ったか
    excluded_brackets: "tuple[int, ...]" = ()

    @property
    def n_days(self) -> int:
        return int(self.day.size)


def _bracket_segments(br: "np.ndarray") -> "tuple[np.ndarray, np.ndarray]":
    """時刻昇順のブラケット id 列 → (セグメント開始 index 列, セグメントのブラケット id 列)。"""
    if br.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int32)
    starts = np.flatnonzero(np.diff(br) != 0) + 1
    starts = np.concatenate([[0], starts])
    return starts, br[starts]


def _max_run_length(values: "np.ndarray") -> int:
    """連続同値の最長 run 長。空は 0。"""
    if values.size == 0:
        return 0
    change = np.flatnonzero(np.diff(values) != 0)
    edges = np.concatenate([[-1], change, [values.size - 1]])
    return int(np.max(np.diff(edges)))


def _ffill_day(
    mod: "np.ndarray", o: "np.ndarray", h: "np.ndarray", lo: "np.ndarray", c: "np.ndarray"
) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    """セッション窓の分グリッドへ ffill 補完した (mod_grid, high, low) を返す。

    欠測分は直前 close の仮想バー（high=low=close）。先頭欠測は当日最初のバーの open。
    """
    grid = np.arange(SESSION_OPEN_MOD, SESSION_CLOSE_MOD + 1, dtype=np.int32)
    gh = np.full(grid.size, np.nan)
    gl = np.full(grid.size, np.nan)
    gc = np.full(grid.size, np.nan)
    pos = mod - SESSION_OPEN_MOD
    gh[pos], gl[pos], gc[pos] = h, lo, c
    # close を ffill → 欠測位置の high/low を埋める
    filled = gc.copy()
    mask = np.isnan(filled)
    idx = np.where(~mask, np.arange(filled.size), -1)
    np.maximum.accumulate(idx, out=idx)
    first_open = o[0]
    filled = np.where(idx >= 0, filled[np.maximum(idx, 0)], first_open)
    gh = np.where(mask, filled, gh)
    gl = np.where(mask, filled, gl)
    return grid, gh, gl


def _tpo_day(
    br_hi: "np.ndarray",
    br_lo: "np.ndarray",
    br_ids: "np.ndarray",
    day_high: float,
    day_low: float,
    variant: VariantSpec,
) -> "tuple[int, float, np.ndarray]":
    """1 日ぶんの TPO。(conc, poc_price, POC 行を覆うブラケット id の bool マスク(K,)) を返す。

    行区間 [row_lo, row_hi] を [day_low, day_high] に張り、N(p) = 行区間と
    [lo_b, hi_b] が重なるブラケット本数（閉区間 overlap。ゼロ幅の ffill 仮想バーも計上）。
    POC はタイ時に日中間値へ最も近い行。
    """
    span = day_high - day_low
    if variant.grid_w is not None:
        n_rows = max(1, int(np.ceil(span / variant.grid_w))) if span > 0 else 1
        row_w = variant.grid_w
    else:
        n_rows = variant.n_rows
        row_w = span / n_rows if span > 0 else 1.0
    row_lo = day_low + np.arange(n_rows) * row_w
    row_hi = row_lo + row_w
    centers = row_lo + row_w / 2.0
    cover = (br_lo[:, None] <= row_hi[None, :]) & (br_hi[:, None] >= row_lo[None, :])
    n_p = cover.sum(axis=0)  # (n_rows,)
    n_max = int(n_p.max()) if n_p.size else 0
    mid = (day_high + day_low) / 2.0
    cand = np.flatnonzero(n_p == n_max)
    poc_row = int(cand[np.argmin(np.abs(centers[cand] - mid))])
    touch = np.zeros(K_BRACKETS, dtype=bool)
    touch[br_ids[cover[:, poc_row]]] = True
    return n_max, float(centers[poc_row]), touch


def _poc_bracket_stats(touch: "np.ndarray") -> "tuple[float, float]":
    """POC 行を覆ったブラケット集合の (中央値 index, 最初 index)。空は NaN。"""
    ids = np.flatnonzero(touch)
    if ids.size == 0:
        return float("nan"), float("nan")
    return float(np.median(ids)), float(ids[0])


def build_daily_features(
    sd: SessionData,
    variants: "tuple[VariantSpec, ...]" = VARIANTS,
    *,
    primary: VariantSpec = PRIMARY,
    exclude_brackets: "tuple[int, ...]" = (),
) -> DailyFeatures:
    """SessionData から全日次特徴量を構築する（exclude_brackets は TPO・ブラケット統計から除外）。"""
    D = sd.n_days
    K = K_BRACKETS
    excl = np.zeros(K, dtype=bool)
    if exclude_brackets:
        excl[list(exclude_brackets)] = True

    o_d = np.empty(D)
    c_d = np.empty(D)
    hi_d = np.empty(D)
    lo_d = np.empty(D)
    rv = np.zeros(D)
    n_bars = (sd.ends - sd.starts).astype(np.int64)
    br_ret = np.full((D, K), np.nan)
    br_rv = np.full((D, K), np.nan)
    br_nd = np.zeros((D, K))
    br_mr = np.zeros((D, K))
    br_hi_arr = np.full((D, K), np.nan)
    br_lo_arr = np.full((D, K), np.nan)
    conc: "dict[str, np.ndarray]" = {v.key: np.zeros(D) for v in variants}
    poc_price: "dict[str, np.ndarray]" = {v.key: np.zeros(D) for v in variants}
    poc_med: "dict[str, np.ndarray]" = {v.key: np.full(D, np.nan) for v in variants}
    poc_first: "dict[str, np.ndarray]" = {v.key: np.full(D, np.nan) for v in variants}
    touch_primary = np.zeros((D, K), dtype=bool)

    for d in range(D):
        s, e = int(sd.starts[d]), int(sd.ends[d])
        mod = sd.mod[s:e]
        o, h, lo, c = sd.open[s:e], sd.high[s:e], sd.low[s:e], sd.close[s:e]
        o_d[d], c_d[d] = o[0], c[-1]
        hi_d[d], lo_d[d] = float(h.max()), float(lo.min())

        # 5 分 RV（5 分境界 close・間隔ちょうど 300 秒のみ）
        m5 = mod % 5 == 0
        ep5, c5 = sd.epoch[s:e][m5], c[m5]
        if ep5.size >= 2:
            d_ok = (np.diff(ep5) == FIVE_MIN_SECONDS) & (c5[:-1] > 0) & (c5[1:] > 0)
            r5 = np.log(c5[1:][d_ok] / c5[:-1][d_ok])
            rv[d] = float(np.sum(r5 * r5))

        # 暦時間ブラケット統計（リターン・内部 RV・distinct・run）
        br = calendar_bracket_of_mod(mod)
        seg_starts, seg_ids = _bracket_segments(br)
        seg_ends = np.append(seg_starts[1:], mod.size)
        prev_close = o[0]
        r1 = np.log(c[1:] / c[:-1]) if c.size >= 2 else np.empty(0)
        for st, en, b in zip(seg_starts, seg_ends, seg_ids):
            b = int(b)
            seg_c = c[st:en]
            last = float(seg_c[-1])
            br_ret[d, b] = np.log(last / prev_close) if prev_close > 0 and last > 0 else np.nan
            prev_close = last
            # ブラケット内 1 分 r²（ペアの後端がブラケット内）
            lo_i = max(st, 1)
            br_rv[d, b] = float(np.sum(r1[lo_i - 1 : en - 1] ** 2)) if en > lo_i else 0.0
            br_nd[d, b] = float(np.unique(seg_c).size)
            br_mr[d, b] = float(_max_run_length(seg_c))

        # TPO（変種横断・raw / ffill のブラケット hi/lo を先に作る）
        keep_raw = ~excl[br]
        segs_raw = _bracket_segments(br[keep_raw])
        h_raw, lo_raw = h[keep_raw], lo[keep_raw]
        st_raw, ids_raw = segs_raw
        en_raw = np.append(st_raw[1:], h_raw.size)
        bh_raw = np.array([h_raw[a:b2].max() for a, b2 in zip(st_raw, en_raw)])
        bl_raw = np.array([lo_raw[a:b2].min() for a, b2 in zip(st_raw, en_raw)])
        br_hi_arr[d, ids_raw] = bh_raw
        br_lo_arr[d, ids_raw] = bl_raw

        need_ffill = any(v.fill == "ffill" for v in variants)
        if need_ffill:
            gmod, gh, gl = _ffill_day(mod, o, h, lo, c)
            gbr = calendar_bracket_of_mod(gmod)
            gkeep = ~excl[gbr]
            st_f, ids_f = _bracket_segments(gbr[gkeep])
            gh2, gl2 = gh[gkeep], gl[gkeep]
            en_f = np.append(st_f[1:], gh2.size)
            bh_f = np.array([gh2[a:b2].max() for a, b2 in zip(st_f, en_f)])
            bl_f = np.array([gl2[a:b2].min() for a, b2 in zip(st_f, en_f)])

        for v in variants:
            if v.fill == "ffill":
                bh, bl, ids = bh_f, bl_f, ids_f
            else:
                bh, bl, ids = bh_raw, bl_raw, ids_raw
            n_max, poc_p, touch = _tpo_day(bh, bl, ids, hi_d[d], lo_d[d], v)
            conc[v.key][d] = n_max
            poc_price[v.key][d] = poc_p
            med, first = _poc_bracket_stats(touch)
            poc_med[v.key][d] = med
            poc_first[v.key][d] = first
            if v == primary:
                touch_primary[d] = touch

    r_oc = np.log(c_d / o_d)
    r_co = np.full(D, np.nan)
    span = np.full(D, np.nan)
    if D >= 2:
        r_co[1:] = np.log(o_d[1:] / c_d[:-1])
        span[1:] = (sd.day_epoch[1:] - sd.day_epoch[:-1]) / DAY_SECONDS

    return DailyFeatures(
        day=sd.day_epoch, o=o_d, c=c_d, day_high=hi_d, day_low=lo_d,
        r_oc=r_oc, r_co=r_co, co_span_days=span, rv_oc=rv, n_bars=n_bars,
        bracket_minutes=bracket_minutes(),
        br_ret=br_ret, br_rv=br_rv, br_ndistinct=br_nd, br_maxrun=br_mr,
        br_hi=br_hi_arr, br_lo=br_lo_arr,
        conc=conc, poc_price=poc_price,
        poc_bracket_median=poc_med, poc_bracket_first=poc_first,
        poc_touch_primary=touch_primary,
        excluded_brackets=tuple(exclude_brackets),
    )


# --------------------------------------------------------------------------- #
# 季節性 ŝ(b)
# --------------------------------------------------------------------------- #
def _seasonality_ratio(f: DailyFeatures) -> "np.ndarray":
    """(D,K) の r²_{d,b} / (RV_br_d / B_eff_d)（欠測 NaN）。ŝ 推定の共通素材。"""
    r2 = f.br_ret**2
    rv_br = np.nansum(r2, axis=1)  # (D,)
    b_eff = np.sum(~np.isnan(r2), axis=1).astype(float)
    denom = np.where((rv_br > 0) & (b_eff > 0), rv_br / np.maximum(b_eff, 1.0), np.nan)
    return r2 / denom[:, None]


def s_hat_full(f: DailyFeatures) -> "np.ndarray":
    """全標本 ŝ(b)（(1/B)Σŝ²=1 に正規化）。同時点 artifact 検定（2a/2b/2c）用。"""
    ratio = _seasonality_ratio(f)
    s2 = np.nanmean(ratio, axis=0)
    s2 = np.nan_to_num(s2, nan=0.0)
    mean = s2[s2 > 0].mean() if np.any(s2 > 0) else 1.0
    return np.sqrt(s2 / mean)


def s_hat_expanding(f: DailyFeatures, *, warmup: int = 250) -> "np.ndarray":
    """expanding ŝ_d(b)（day d に対し day < d のみ・ウォームアップ未満は NaN）。Step3 供給用。"""
    ratio = _seasonality_ratio(f)
    filled = np.nan_to_num(ratio, nan=0.0)
    cnt = (~np.isnan(ratio)).astype(float)
    cum = np.cumsum(filled, axis=0)
    cum_cnt = np.cumsum(cnt, axis=0)
    D, K = ratio.shape
    out = np.full((D, K), np.nan)
    for d in range(warmup, D):
        c_sum, c_cnt = cum[d - 1], cum_cnt[d - 1]  # day < d のみ
        s2 = np.where(c_cnt > 0, c_sum / np.maximum(c_cnt, 1.0), 0.0)
        mean = s2[s2 > 0].mean() if np.any(s2 > 0) else 1.0
        out[d] = np.sqrt(s2 / mean)
    return out


# --------------------------------------------------------------------------- #
# 時間変更（τ）ブラケット TPO
# --------------------------------------------------------------------------- #
def tpo_tau_series(
    sd: SessionData,
    f: DailyFeatures,
    variant: VariantSpec,
    s2: "np.ndarray",
    *,
    exclude_brackets: "tuple[int, ...]" = (),
) -> "dict[str, np.ndarray]":
    """ビジネス時間ブラケット TPO の日次系列を返す。

    Args:
        s2: (K,) 全標本 ŝ² または (D,K) expanding ŝ²（day 行が NaN の日は結果 NaN）。

    Returns:
        {"conc": (D,), "poc_price": (D,)}。expanding で ŝ 未確定の日は NaN。
    """
    D = sd.n_days
    excl = np.zeros(K_BRACKETS, dtype=bool)
    if exclude_brackets:
        excl[list(exclude_brackets)] = True
    per_day = s2.ndim == 2
    conc = np.full(D, np.nan)
    poc_p = np.full(D, np.nan)
    assign_cache: "np.ndarray | None" = None
    if not per_day:
        s2_use = np.where(excl, 0.0, np.nan_to_num(s2, nan=0.0))
        assign_cache = tau_bracket_of_mod(s2_use)
    for d in range(D):
        if per_day:
            row = s2[d]
            if np.all(np.isnan(row)):
                continue
            s2_use = np.where(excl, 0.0, np.nan_to_num(row, nan=0.0))
            assign = tau_bracket_of_mod(s2_use)
        else:
            assign = assign_cache
        s, e = int(sd.starts[d]), int(sd.ends[d])
        mod = sd.mod[s:e]
        h, lo = sd.high[s:e], sd.low[s:e]
        if variant.fill == "ffill":
            gmod, h, lo = _ffill_day(mod, sd.open[s:e], h, lo, sd.close[s:e])
            mod = gmod
        keep = ~excl[calendar_bracket_of_mod(mod)]
        br = assign[mod[keep]]
        st, ids = _bracket_segments(br)
        h2, l2 = h[keep], lo[keep]
        en = np.append(st[1:], h2.size)
        bh = np.array([h2[a:b2].max() for a, b2 in zip(st, en)])
        bl = np.array([l2[a:b2].min() for a, b2 in zip(st, en)])
        n_max, p, _ = _tpo_day(bh, bl, ids, f.day_high[d], f.day_low[d], variant)
        conc[d] = n_max
        poc_p[d] = p
    return {"conc": conc, "poc_price": poc_p}


# --------------------------------------------------------------------------- #
# ffill 分グリッド（分単位滞在 dwell 原子・Step5 Null B 用）
# --------------------------------------------------------------------------- #
def ffill_close_grid(sd: SessionData) -> "np.ndarray":
    """(D, G) の ffill 分グリッド close（G=セッション窓 1378 分）を返す。

    欠測分は直前 close（日先頭欠測は当日最初のバー open）。分単位滞在（dwell）の
    原子＝「その分に価格が居た水準」の点系列。Step5 の観測・帰無双方で close 点
    占有を用いる（観測/帰無の対称性のため hi/lo レンジは使わない）。
    """
    G = SESSION_CLOSE_MOD - SESSION_OPEN_MOD + 1
    out = np.empty((sd.n_days, G))
    for d in range(sd.n_days):
        s, e = int(sd.starts[d]), int(sd.ends[d])
        mod = sd.mod[s:e]
        gc = np.full(G, np.nan)
        gc[mod - SESSION_OPEN_MOD] = sd.close[s:e]
        mask = np.isnan(gc)
        idx = np.where(~mask, np.arange(G), -1)
        np.maximum.accumulate(idx, out=idx)
        filled = np.where(idx >= 0, gc[np.maximum(idx, 0)], sd.open[s])
        out[d] = filled
    return out


# --------------------------------------------------------------------------- #
# H0 サロゲート（Step2c 校正用）: 日内バー順列
# --------------------------------------------------------------------------- #
def permute_bars_within_day(sd: SessionData, rng) -> SessionData:
    """各日のバー並び順を無作為化した H0 サロゲートを返す（時刻配置のみ破壊）。

    各バーの内部形状（open 比の high/low/close）を保存したまま日内で並べ替え、
    open が前バー close に連鎖するよう乗法的に再基準化する。日リターン分布・
    日レンジ形状・欠測分パターン（スロット位置）は保存され、hour-of-day への
    配置（＝日中季節性）だけが破壊される。ŝ(b) をサロゲートごとに再推定する
    校正（シミュレーション校正臨界値）の入力に使う。
    """
    o = np.empty_like(sd.open)
    h = np.empty_like(sd.high)
    lo = np.empty_like(sd.low)
    c = np.empty_like(sd.close)
    for d in range(sd.n_days):
        s, e = int(sd.starts[d]), int(sd.ends[d])
        n = e - s
        pi = rng.permutation(n)
        ro = sd.open[s:e][pi]
        rh = sd.high[s:e][pi] / ro
        rl = sd.low[s:e][pi] / ro
        rc = sd.close[s:e][pi] / ro
        log_o = np.log(sd.open[s]) + np.concatenate([[0.0], np.cumsum(np.log(rc[:-1]))])
        oo = np.exp(log_o)
        o[s:e] = oo
        h[s:e] = oo * rh
        lo[s:e] = oo * rl
        c[s:e] = oo * rc
    return SessionData(
        day_epoch=sd.day_epoch, starts=sd.starts, ends=sd.ends,
        mod=sd.mod, epoch=sd.epoch, open=o, high=h, low=lo, close=c,
    )


# --------------------------------------------------------------------------- #
# ルックアヘッド排除の明示 assert（Step3 の結合用）
# --------------------------------------------------------------------------- #
def assert_no_lookahead_daily(feature_day: "np.ndarray", target_day: "np.ndarray") -> None:
    """特徴量日 + 1 日 <= 目的変数日 を全行で保証する（違反は AssertionError）。"""
    assert feature_day.shape == target_day.shape
    assert np.all(feature_day + DAY_SECONDS <= target_day), "lookahead violation"
