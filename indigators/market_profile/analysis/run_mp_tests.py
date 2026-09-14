"""MP (TPO/POC) 情報価値検定パイプライン CLI — Step1→2→3（打ち切り規則付き）。

使い方（analysis/ ディレクトリから）:
    python run_mp_tests.py [--start 2012-06-15] [--end 2026-07-10] [--seed 42]
                           [--B 10000] [--mc-reps 199] [--alpha 0.05]

出力: out/mp_stats_report.json（機械可読）・out/mp_stats_report.md（人間可読）・
      out/s_hat.png / out/step3_partial.png（matplotlib があれば）。
seed 固定で JSON はバイト同一に再現される（タイムスタンプ非含有）。

注記: 計画にあった特徴量 npz キャッシュは実装しない。Step2c のシミュレーション校正
（ISSUE-056）が SessionData（生バー）をサロゲート生成に必須とするため、日次特徴量
だけのキャッシュでは CSV ロードを省略できず実益がない（フルラン支配項は校正 MC）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

from mp_stats import data_prep as dp                      # noqa: E402
from mp_stats import report as rp                         # noqa: E402
from mp_stats.step1_stop_ratio import run_step1           # noqa: E402
from mp_stats.step2_seasonality_poc import (              # noqa: E402
    freeze_diagnostics,
    run_step2,
)
from mp_stats.step3_incremental_r2 import run_step3       # noqa: E402
from mp_stats.step4_hurst import run_step4                 # noqa: E402
from mp_stats.step5_null_b import run_step5                # noqa: E402
from mp_stats.step6_conditional import run_step6           # noqa: E402
from mp_stats.step7_spa import run_step7                    # noqa: E402
from mp_stats.step8_oos import run_step8                    # noqa: E402

_DEFAULT_CSV = _HERE.parents[3] / "data/marketdata/jp225_tick_m1.csv"
_OUT = _HERE.parent / "out"


def _date_of(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc).strftime("%Y-%m-%d")


def _maybe_png_s_hat(s: "np.ndarray", window: "list[int]", path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(12, 4))
    x = np.arange(s.size)
    colors = ["#d62728" if i in window else "#1f77b4" for i in x]
    ax.bar(x, s, color=colors)
    ax.axhline(1.0, color="gray", lw=0.8, ls="--")
    ax.set_xlabel("calendar bracket (30min from 01:00 UTC)")
    ax.set_ylabel("s_hat(b)")
    ax.set_title("Intraday seasonality s_hat(b) — red = low-vol window (m_d)")
    # JST 昼休み 11:30-12:30 = 02:30-03:30 UTC = brackets 3-4
    ax.axvspan(2.5, 4.5, alpha=0.15, color="orange", label="JST lunch (02:30-03:30 UTC)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def _maybe_png_step3(f: "dp.DailyFeatures", conc: "np.ndarray", path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mp_stats import stats_core as sc
        from mp_stats.step3_incremental_r2 import build_regression_arrays
    except Exception:
        return
    arr = build_regression_arrays(f, conc, use_har=True)
    if arr["y"].size < 100:
        return
    _, ry, _ = sc.ols(arr["X0"], arr["y"])
    _, rc, _ = sc.ols(arr["X0"], arr["c"])
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(rc, ry, s=4, alpha=0.4)
    ax.set_xlabel("resid( ln conc_d | M0-HAR )")
    ax.set_ylabel("resid( ln RV_{d+1} | M0-HAR )")
    ax.set_title("Step3 partial relation (primary variant)")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def _maybe_png_zmax(z_max: "np.ndarray", path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    z = z_max[np.isfinite(z_max)]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(z, bins=60, color="#1f77b4")
    ax.set_xlabel("z_max (excess occupancy of POC*, Null B)")
    ax.set_ylabel("days")
    ax.set_title("Step5: daily max excess-occupancy z-score")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main(argv: "list[str] | None" = None) -> dict:
    ap = argparse.ArgumentParser(description="MP TPO/POC statistical validation (steps 1-3)")
    ap.add_argument("--csv", default=str(_DEFAULT_CSV))
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--B", type=int, default=10_000, help="bootstrap replicates (step1/3)")
    ap.add_argument("--mc-reps", type=int, default=199, help="surrogate reps for step2c calibration")
    ap.add_argument("--m-reps", type=int, default=10_000, help="Null B reps per day (step5)")
    ap.add_argument("--m-reps-migration", type=int, default=2_000,
                    help="Null B stop-position reps per migration day (step6 part B)")
    ap.add_argument("--spa-B", type=int, default=5_000, help="SPA bootstrap reps (step7)")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--tick-check-days", type=int, default=250,
                    help="tick dwell agreement check days for step5 (0 = skip)")
    ap.add_argument("--out-dir", default=str(_OUT))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)

    print(f"[1/6] load M1: {args.csv}")
    m1 = dp.load_m1(args.csv, start=args.start, end=args.end)
    sd = dp.build_session_data(m1)
    print(f"      n_days={sd.n_days}  session_bars={sd.mod.size}")

    print("[2/6] build daily features (all variants)")
    f = dp.build_daily_features(sd, dp.VARIANTS)

    # 2-0 凍結チェック → 除外があれば特徴量を再構築（全ステップへ波及）
    diag = freeze_diagnostics(f)
    frozen = diag["frozen_brackets"]
    if frozen:
        print(f"      frozen brackets detected: {frozen} → rebuild with exclusion")
        f = dp.build_daily_features(sd, dp.VARIANTS, exclude_brackets=frozen)

    print(f"[3/6] step1 (B={args.B})")
    step1 = run_step1(f, seed=args.seed, B=args.B)
    print(f"      decision={step1.decision}  kappa={step1.statistics['kappa']:.4f}")

    print(f"[4/6] step2 (calibrated, M={args.mc_reps}) — 支配的な実行時間")
    step2, s_hat = run_step2(
        sd, f, variants=dp.VARIANTS, primary=dp.PRIMARY,
        alpha=args.alpha, seed=args.seed, mc_reps=args.mc_reps,
    )
    print(f"      decision={step2.decision}  p_mc={step2.statistics['p_mc']:.4f}"
          f"  t_bar={step2.statistics['t_bar']:.4f}")

    # Step3 の conc: 2c 棄却時は expanding ŝ による τ 版（ルックアヘッド排除）
    if "use_time_changed_poc" in step2.flags:
        print("[5/6] step3 with time-changed (tau) conc — expanding s_hat (warmup 250)")
        s_exp = dp.s_hat_expanding(f, warmup=250)
        conc_by_key = {
            v.key: dp.tpo_tau_series(sd, f, v, s_exp**2, exclude_brackets=f.excluded_brackets)["conc"]
            for v in dp.VARIANTS
        }
        conc_source = "tau_expanding"
    else:
        print("[5/6] step3 with raw calendar conc")
        conc_by_key = dict(f.conc)
        conc_source = "calendar_raw"
    step3 = run_step3(
        f, conc_by_key, primary_key=dp.PRIMARY.key,
        seed=args.seed, B=args.B, alpha=args.alpha,
    )
    print(f"      decision={step3.decision}  p_hac={step3.statistics.get('p_hac')}")

    results = [step1, step2, step3]
    step5_out = None
    if step3.decision == "reject":
        print(f"[5.4] step4 (Hurst/VR, B=500)")
        step4 = run_step4(f, seed=args.seed, B=500, alpha=args.alpha)
        print(f"      decision={step4.decision}  H={step4.statistics['hurst_h']:.4f}"
              f"  b_hat={step4.statistics['scaling_b_hat']:.4f}")
        results.append(step4)

        print(f"[5.5] step5 (Null B, M={args.m_reps}/day) — 長時間")
        step5, step5_out = run_step5(
            sd, f, seed=args.seed, m_reps=args.m_reps,
            normalization_exponent=step4.statistics["normalization_exponent"],
        )
        if args.tick_check_days > 0:
            from mp_stats.tick_dwell_check import tick_dwell_agreement
            print(f"      tick dwell agreement check ({args.tick_check_days} days)")
            step5.statistics["tick_dwell_agreement"] = tick_dwell_agreement(
                sd, f, n_days=args.tick_check_days
            )
        print(f"      z_max median={step5.statistics['z_max_median']:.2f}"
              f"  disagree_rate={step5.statistics['raw_poc_disagree_rate']:.3f}")
        results.append(step5)

        print(f"[5.6] step6 (VA外寄り付き + POC*移動先検定, M={args.m_reps_migration}/移動日)")
        step6 = run_step6(
            sd, f, step5_out["poc_star"],
            seed=args.seed, alpha=args.alpha,
            m_reps_migration=args.m_reps_migration,
        )
        pa = step6.statistics["part_a"]
        pb = step6.statistics["part_b_migration"]
        print(f"      decision={step6.decision}  p_joint(A)={pa['p_joint_bonferroni2']:.4g}"
              f"  migration: u_mean={pb.get('u_mean')}  p={pb.get('p_one_sided')}")
        results.append(step6)

        print(f"[5.7] step7 (SPA 216ルール, B={args.spa_B}) — ダミー構築＋ウォークフォワード")
        step7, spa_out = run_step7(f, seed=args.seed, B=args.spa_B, alpha=args.alpha)
        print(f"      decision={step7.decision}  p_spa={step7.statistics.get('p_spa')}"
              f"  best={step7.statistics.get('best_rule')}")
        results.append(step7)

        if step7.decision == "reject":
            print("[5.8] step8 (IS/OOS 70/30 + Kupiec/Christoffersen)")
            step8 = run_step8(f, spa_out, alpha=args.alpha)
            print(f"      decision={step8.decision}"
                  f"  oos_p={step8.statistics.get('oos_p_one_sided')}"
                  f"  kupiec={step8.statistics.get('p_kupiec')}")
            results.append(step8)
        else:
            results.append(rp.StepResult(
                step=8, name="oos_calibration", decision="skipped",
                notes="censored: step7 fail_to_reject（全ルール無効）→ OOS 検証は対象なし。",
            ))
            print("[5.8] step8 は打ち切り（step7 fail_to_reject）")
    else:
        print("[5.4-5.8] step4-8 は打ち切り規則により実行しない（step3 fail_to_reject）")

    print("[6/6] write report")
    meta = {
        "period": [_date_of(sd.day_epoch[0]), _date_of(sd.day_epoch[-1])],
        "n_days": sd.n_days,
        "seed": args.seed,
        "B": args.B,
        "mc_reps": args.mc_reps,
        "alpha": args.alpha,
        "primary_variant": dp.PRIMARY.key,
        "variants": [v.key for v in dp.VARIANTS],
        "excluded_brackets": list(f.excluded_brackets),
        "conc_source_step3": conc_source,
        "m_reps_step5": args.m_reps,
        "csv": str(args.csv),
    }
    report = rp.build_report(results, meta)
    if step5_out is not None:
        np.savez(
            out_dir / "step5_poc_star.npz",
            day=f.day,
            **step5_out,
        )
        _maybe_png_zmax(step5_out["z_max"], out_dir / "step5_zmax.png")
    rp.write_json(report, out_dir / "mp_stats_report.json")
    rp.write_markdown(report, out_dir / "mp_stats_report.md")
    _maybe_png_s_hat(
        np.asarray(step2.statistics["s_hat"], dtype=float),
        list(step2.statistics.get("low_vol_window_brackets", [])),
        out_dir / "s_hat.png",
    )
    _maybe_png_step3(f, conc_by_key[dp.PRIMARY.key], out_dir / "step3_partial.png")
    print(f"      -> {out_dir}/mp_stats_report.json / .md")
    return report


if __name__ == "__main__":
    main()
