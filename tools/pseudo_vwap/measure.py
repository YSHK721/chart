"""measure — 測定の組み立て（ISSUE-479 Wave2 M-4）。

素材（data）・指標（indicators）・検定（stats）を組み合わせて測定表を作る層。ここには式も
I/O も持たない（式は indicators / stats、素材の出所は data が持つ）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.marod_bands import quantile_bands

from tools.pseudo_vwap import data as _data
from tools.pseudo_vwap.indicators import (
    forward_return,
    forward_rv,
    rolling_ratio,
    session_cum_mean,
    session_vwap,
    session_vwap_and_index,
    true_range,
)
from tools.pseudo_vwap.stats import (
    contrast_test,
    deviation_test,
    entry_test,
    holm,
    signed_state_test,
)


def measure_period(
    label: str,
    m1: pd.DataFrame,
    tfs: "tuple[str, ...]",
    windows: "tuple[int, ...]",
    horizons: "tuple[int, ...]",
    perms: int,
    seed: int,
    dev_q: float,
    dev_horizons: "tuple[int, ...]",
    band_window: int,
    confirm_only: bool = False,
) -> "dict[str, Any]":
    rows: "list[dict[str, Any]]" = []
    tests: "list[dict[str, Any]]" = []
    devs: "list[dict[str, Any]]" = []
    uses: "list[dict[str, Any]]" = []
    mech: "list[dict[str, Any]]" = []
    for tf in tfs:
        df = _data.resample_with_pv(m1, tf)
        starts = _data.session_starts(df)
        close = df["close"].to_numpy()
        tr_med = float(np.nanmedian(true_range(df)))
        for n in windows:
            pvwap = rolling_ratio(df["pv"], df["volume"], n)
            sma = df["close"].rolling(n).mean().to_numpy()
            has_dwell = "pw" in df.columns and "w" in df.columns
            twap = rolling_ratio(df["pw"], df["w"], n) if has_dwell else np.full(len(df), np.nan)
            g10 = rolling_ratio(df["pv_g10"], df["volume"], n)
            fine = rolling_ratio(df["pv_u"], df["volume"], n)
            # 既存列だけで作れる近似（新規列を足さずに済む対抗案）。
            #   bar_tp : Σ(hlc3 × volume) / Σvolume   古典的な VWAP 近似
            #   bar_oc : Σ(ohlc4 × volume) / Σvolume
            hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
            ohlc4 = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
            bar_tp = rolling_ratio(hlc3 * df["volume"], df["volume"], n)
            bar_oc = rolling_ratio(ohlc4 * df["volume"], df["volume"], n)
            d = pvwap - sma
            ok = np.isfinite(d)
            d_med = float(np.nanmedian(np.abs(d)))
            rows.append({
                "period": label, "tf": tf, "n": n, "bars": int(np.isfinite(pvwap).sum()),
                "tr_median": tr_med,
                # 測定 1
                "abs_d_median": d_med,
                "d_over_tr": d_med / tr_med if tr_med else float("nan"),
                "corr_sma": float(np.corrcoef(pvwap[ok], sma[ok])[0, 1]),
                "abs_twap_diff_median": float(np.nanmedian(np.abs(pvwap - twap))),
                "twap_over_d": (float(np.nanmedian(np.abs(pvwap - twap))) / d_med) if d_med else float("nan"),
                # 測定 2（依頼原式の量子化誤差。信号 D に対する比が本質）
                "q_err_g10_median": float(np.nanmedian(np.abs(g10 - pvwap))),
                "q_err_g10_over_d": float(np.nanmedian(np.abs(g10 - pvwap))) / d_med if d_med else float("nan"),
                "q_err_fine_median": float(np.nanmedian(np.abs(fine - pvwap))),
                "q_err_fine_over_d": float(np.nanmedian(np.abs(fine - pvwap))) / d_med if d_med else float("nan"),
                # 対抗案（既存列のみ・新規列なし）との差。これが小さいなら pv 列追加は不要。
                "bar_tp_err_median": float(np.nanmedian(np.abs(bar_tp - pvwap))),
                "bar_tp_over_d": float(np.nanmedian(np.abs(bar_tp - pvwap))) / d_med if d_med else float("nan"),
                "bar_oc_err_median": float(np.nanmedian(np.abs(bar_oc - pvwap))),
                "bar_oc_over_d": float(np.nanmedian(np.abs(bar_oc - pvwap))) / d_med if d_med else float("nan"),
            })
            if not confirm_only:
                for h in horizons:
                    res = signed_state_test(close, pvwap, h, block_bars=10 * n,
                                            perms=perms, seed=seed)
                    res.update({"period": label, "tf": tf, "n": n, "h": h})
                    tests.append(res)
                for h in dev_horizons:
                    for row in deviation_test(close, pvwap, sma, n=n, h=h, q=dev_q,
                                              band_window=band_window, perms=perms, seed=seed):
                        row.update({"period": label, "tf": tf, "n": n, "h": h, "q": dev_q})
                        devs.append(row)

            # --- 用法 A: pv 固有成分（既存列では作れない残差）に情報があるか
            resid = pvwap - bar_tp
            # --- 用法 C: 疑似VWAP からの乖離幅（ボラティリティ代理）
            spread = np.abs(close - pvwap) / close
            # 対照: 同じ量を SMA で作ったもの。ボラは自己持続するため、この対照を超えない限り
            #       「疑似VWAP 固有の情報」とは言えない。差分が pv 列で初めて得られる成分。
            spread_sma = np.abs(close - sma) / close
            d_spread = spread - spread_sma
            # Δspread が単に spread_sma の裏返しでないかを見る条件付き標本:
            #   spread_sma が因果 25〜75% 分位の中位帯にあるバーだけに限定する。
            sma_lo, sma_hi = quantile_bands(spread_sma, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            mid_mask = (np.isfinite(sma_lo) & np.isfinite(sma_hi)
                        & (spread_sma >= sma_lo) & (spread_sma <= sma_hi))
            vol_n = df["volume"].rolling(n).sum().to_numpy()
            vol_lo, vol_hi = quantile_bands(vol_n, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            vol_mid_mask = (np.isfinite(vol_lo) & np.isfinite(vol_hi)
                            & (vol_n >= vol_lo) & (vol_n <= vol_hi))
            step_r = np.full(close.size, np.nan)
            step_r[1:] = np.log(close[1:] / close[:-1])
            past_rv = pd.Series(np.square(step_r)).rolling(n).sum().pow(0.5).to_numpy()
            prv_lo, prv_hi = quantile_bands(past_rv, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            prv_mid_mask = (np.isfinite(prv_lo) & np.isfinite(prv_hi)
                            & (past_rv >= prv_lo) & (past_rv <= prv_hi))
            for h in dev_horizons:
                fret = forward_return(close, h)
                frv = forward_rv(close, h)
                res = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                    band_window=band_window, perms=perms, seed=seed,
                                    mask=mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res:
                        res[k + "_bp"] = res.pop(k) * 1e4
                res.update({"period": label, "tf": tf, "n": n, "h": h,
                            "use": "C条件付:Δspread|sma中位→rv"})
                uses.append(res)
                # 交絡の排除: ティック数（既存 tickvol 指標）自体が RV の強い予測子なので、
                #   窓合計 tickvol も中位帯へ固定した標本で残るかを見る。
                res2 = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                     band_window=band_window, perms=perms, seed=seed,
                                     mask=mid_mask & vol_mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res2:
                        res2[k + "_bp"] = res2.pop(k) * 1e4
                res2.update({"period": label, "tf": tf, "n": n, "h": h,
                             "use": "C条件付2:Δspread|sma中位&tickvol中位→rv"})
                uses.append(res2)
                # 交絡の排除（測定 7 で判明した分）: Δspread 上位群は直近ボラが 7〜12% 低い。
                #   過去 N 本の実現ボラも中位帯へ固定して、ボラの自己持続で説明できるかを見る。
                res3 = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                     band_window=band_window, perms=perms, seed=seed,
                                     mask=mid_mask & vol_mid_mask & prv_mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res3:
                        res3[k + "_bp"] = res3.pop(k) * 1e4
                res3.update({"period": label, "tf": tf, "n": n, "h": h,
                             "use": "C条件付3:Δspread|sma&tickvol&過去RV中位→rv"})
                uses.append(res3)
                # 「静か＝疑似VWAP へ戻る」なのかを直接測る。
                #   gap_chg : |close−疑似VWAP| が h 本後にどれだけ縮むか（負＝縮む＝戻る）
                #   sgn_chg : 符号付き (close−疑似VWAP) の変化（負＝下へ動く＝上から戻る）
                gap = np.abs(close - pvwap)
                sgn = close - pvwap
                gap_chg = np.full(close.size, np.nan)
                sgn_chg = np.full(close.size, np.nan)
                lim2 = close.size - h
                if lim2 > 0:
                    gap_chg[:lim2] = (gap[h:] - gap[:lim2]) / close[:lim2]
                    sgn_chg[:lim2] = (sgn[h:] - sgn[:lim2]) / close[:lim2]
                # 乖離が縮むのは「価格が戻る」からか「平均が追いつく」からかを分解する。
                close_chg = np.full(close.size, np.nan)
                pvwap_chg = np.full(close.size, np.nan)
                if lim2 > 0:
                    close_chg[:lim2] = (close[h:] - close[:lim2]) / close[:lim2]
                    pvwap_chg[:lim2] = (pvwap[h:] - pvwap[:lim2]) / close[:lim2]
                for oname, ov in (("gap縮小", gap_chg), ("符号付き変化", sgn_chg),
                                  ("終値の動き", close_chg), ("疑似VWAPの動き", pvwap_chg)):
                    r4 = contrast_test(d_spread, ov, n=n, h=h, q=dev_q,
                                       band_window=band_window, perms=perms, seed=seed,
                                       mask=mid_mask & vol_mid_mask & prv_mid_mask)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in r4:
                            r4[k + "_bp"] = r4.pop(k) * 1e4
                    r4.update({"period": label, "tf": tf, "n": n, "h": h,
                               "use": f"D:Δspread→{oname}"})
                    uses.append(r4)

                # --- 当日版（セッションアンカー）で同じボラ予測を測る。
                #   svwap    : セッション開始からの累積 疑似VWAP
                #   scum_ma  : セッション開始からの累積 終値平均（OHLCV だけで作れる対照）
                #   Δs_spread: 両者の乖離幅の差＝当日版の pv 固有成分
                sv, sidx = session_vwap_and_index(df, starts)
                scum_ma = session_cum_mean(df, "close", starts)
                s_spread = np.abs(close - sv) / close
                s_spread_ma = np.abs(close - scum_ma) / close
                ds_spread = s_spread - s_spread_ma
                # 場中序盤は累積平均が不安定なので開始 24 本（5m で 2 時間）を除く。
                warm = sidx >= 24
                for sname, sstate, smask in (
                    ("E当日:Δs_spread→rv", ds_spread, warm),
                    ("E当日:Δs_spread|過去RV&tickvol中位→rv", ds_spread,
                     warm & vol_mid_mask & prv_mid_mask),
                    ("E当日対照:s_spread→rv", s_spread, warm),
                ):
                    r5 = contrast_test(sstate, frv, n=n, h=h, q=dev_q,
                                       band_window=band_window, perms=perms, seed=seed,
                                       mask=smask)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in r5:
                            r5[k + "_bp"] = r5.pop(k) * 1e4
                    r5.update({"period": label, "tf": tf, "n": n, "h": h, "use": sname})
                    uses.append(r5)
                if confirm_only:
                    continue
                for name, state, outcome, unit in (
                    ("A:resid→ret", resid, fret, "bp"),
                    ("A:resid→rv", resid, frv, "bp"),
                    ("C:spread→rv", spread, frv, "bp"),
                    ("C:spread→ret", spread, fret, "bp"),
                    ("C対照:spread_sma→rv", spread_sma, frv, "bp"),
                    ("C対照:spread_sma→ret", spread_sma, fret, "bp"),
                    ("C固有:Δspread→rv", d_spread, frv, "bp"),
                    ("C固有:Δspread→ret", d_spread, fret, "bp"),
                ):
                    res = contrast_test(state, outcome, n=n, h=h, q=dev_q,
                                        band_window=band_window, perms=perms, seed=seed)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in res:
                            res[k + "_bp"] = res.pop(k) * 1e4
                    res.update({"period": label, "tf": tf, "n": n, "h": h, "use": name})
                    uses.append(res)

            # --- 機序の記述: Δspread 上位群 / 下位群が「何が違うバーなのか」を共変量で測る。
            #     仮説を立てず、観測できる量の群間差だけを出す（機序の断定はしない）。
            if not confirm_only:
                lo_d, hi_d = quantile_bands(d_spread, window_n=band_window,
                                            q_low=dev_q, q_high=1.0 - dev_q)
                step = np.full(close.size, np.nan)
                step[1:] = np.abs(close[1:] - close[:-1])
                path = pd.Series(step).rolling(n).sum().to_numpy()
                net = np.full(close.size, np.nan)
                net[n:] = np.abs(close[n:] - close[:-n])
                covars = {
                    "トレンド効率(net/path)": net / path,
                    "過去RV(直近N本)": pd.Series(np.square(np.log(
                        np.concatenate([[np.nan], close[1:] / close[:-1]])
                    ))).rolling(n).sum().pow(0.5).to_numpy() * 1e4,
                    "窓tickvol": df["volume"].rolling(n).sum().to_numpy(),
                    "疑似VWAP−SMA(pt)": pvwap - sma,
                    "close−疑似VWAP(pt)": close - pvwap,
                }
                top_m = np.isfinite(hi_d) & (d_spread >= hi_d)
                bot_m = np.isfinite(lo_d) & (d_spread <= lo_d)
                for cname, cv in covars.items():
                    mech.append({
                        "period": label, "tf": tf, "n": n, "covar": cname,
                        "top": float(np.nanmedian(cv[top_m])),
                        "bot": float(np.nanmedian(cv[bot_m])),
                        "ratio": float(np.nanmedian(cv[top_m]) / np.nanmedian(cv[bot_m]))
                        if np.nanmedian(cv[bot_m]) else float("nan"),
                    })

            # --- 用法 B: セッションアンカーVWAP（本来の VWAP 用法・N に依存しないので n 最小のみ）
            if (not confirm_only) and n == min(windows):
                svwap = session_vwap(df, starts)
                sdev = close / svwap - 1.0
                lo_s, hi_s = quantile_bands(sdev, window_n=band_window,
                                            q_low=dev_q, q_high=1.0 - dev_q)
                cross_up = np.zeros(close.size, dtype=bool)
                cross_up[1:] = (close[1:] > svwap[1:]) & (close[:-1] <= svwap[:-1])
                flags = {
                    "B:下方乖離ロング": np.isfinite(lo_s) & (sdev <= lo_s),
                    "B:上方乖離（反転）": np.isfinite(hi_s) & (sdev >= hi_s),
                    "B:上抜け順張り": cross_up & np.isfinite(svwap),
                }
                for h in dev_horizons:
                    fret = forward_return(close, h)
                    for name, fl in flags.items():
                        res = entry_test(fl, fret, n=n, h=h, perms=perms, seed=seed)
                        res.update({"period": label, "tf": tf, "n": n, "h": h, "use": name})
                        uses.append(res)
    live = [t for t in tests if "p" in t]
    for t, adj in zip(live, holm([t["p"] for t in live])):
        t["p_holm"] = adj
    return {"rows": rows, "tests": tests, "devs": devs, "uses": uses, "mech": mech}


def measure_forming(path: Path, seed: int) -> "dict[str, Any]":
    """測定 4: 部分窓（形成中バー）でも Σmid が厳密に足せることを tick から直接検算する。"""
    work = _data.read_day_work(path)
    per_min = work.groupby("date", sort=True)["mid"].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    worst = 0.0
    checks = 0
    minutes = per_min.index.to_numpy()
    for _ in range(50):
        if len(minutes) < 10:
            break
        i = int(rng.integers(5, len(minutes)))
        start = minutes[i - 5]
        cut = minutes[i] + np.timedelta64(int(rng.integers(1, 60)), "s")
        sel = work[(work["ts"] >= start) & (work["ts"] < cut)]
        if len(sel) == 0:
            continue
        closed = per_min.loc[(per_min.index >= start) & (per_min.index < minutes[i])]
        part = work[(work["ts"] >= minutes[i]) & (work["ts"] < cut)]
        num = float(closed["sum"].sum() + part["mid"].sum())
        den = float(closed["count"].sum() + len(part))
        exact = float(sel["mid"].sum()) / len(sel)
        worst = max(worst, abs(num / den - exact))
        checks += 1
    return {"file": str(path), "checks": checks, "max_abs_error": worst}
