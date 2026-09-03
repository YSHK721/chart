"""verify_pseudo_vwap — 疑似VWAP（ティック回数加重平均価格）の成立判定（読み取り専用・ISSUE-243 Phase 1）。

疑似VWAP の定義（本スクリプトが検証する唯一の式）::

    疑似VWAP_t(N) = Σ_{i=t-N+1..t} PV_i / Σ_{i=t-N+1..t} V_i
        PV_i = Σ_{tick ∈ bar i} mid    （バー内のティック価格合計）
        V_i  = bar i のティック数（既存 volume 列と同一＝marketdata.tick_m1.ticks_to_m1）

依頼式 ``(価格帯別ティック回数 × 該当価格) / ティックボリューム`` は、価格帯幅 → 0 の極限で
上式に一致する。本スクリプトは (a) 上式が既存の SMA と別物か、(b) 価格帯を経由した近似が
どれだけ誤差を生むか、(c) 情報を持つか、(d) 形成中バーで厳密に更新できるか を実測する。

**コードは一切変更しない**（data/marketdata 配下を read-only で読むだけ）。

計測内容:
    測定 1 (gate): D = 疑似VWAP − SMA(close, N)。``median(|D|) / median(TR)`` が 10% 未満なら
                   「SMA の再発明」＝不採用。併せて相関と、滞在秒加重平均（時間加重）との差も出す。
    測定 2      : 依頼原式（価格帯経由）との差。GRID_W=10pt と unit=0.0255 の 2 解像度。
    測定 3      : s = sign(close − 疑似VWAP) の将来 h 本リターンに対する情報量。標本は
                  **h 本ごとの非重複**、帰無は**ブロック順列**（ブロック長 = 10N 本相当）、
                  多重比較は Holm 補正。
    測定 4      : 形成中バー（部分窓）で Σmid を厳密に持てるか（tick から直接検算）。

使い方::

    lightweight-charts-python-main/.venv/bin/python -m tools.verify_pseudo_vwap \\
        --periods 2024-01-01:2024-12-31 2026-01-01:2026-07-31 \\
        --json /tmp/.../pseudo_vwap.json

依存: numpy / pandas と marketdata（本番の集計関数をそのまま使う）。scipy はプロジェクト方針で禁止。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 本モジュールは**合成点**である（ISSUE-479 Wave2 M-4）。式・素材化・検定は tools.pseudo_vwap の
# 各層が持ち、ここは引数解釈と層の結線・出力だけを行う。sys.path への注入もここ 1 箇所に閉じる。
from tools.pseudo_vwap.data import build_m1, day_paths  # noqa: E402
from tools.pseudo_vwap.measure import measure_forming, measure_period  # noqa: E402
from tools.pseudo_vwap.report import print_table  # noqa: E402
from tools.pseudo_vwap.stats import holm  # noqa: E402

DEFAULT_TFS = ("5m", "15m", "1h")
DEFAULT_WINDOWS = (20, 50, 100)
DEFAULT_HORIZONS = (1, 5, 20)
DEFAULT_PERMS = 999
GATE_RATIO = 0.10  # 測定 1 の事前登録閾値: median(|D|) / median(TR)


def main(argv: "list[str] | None" = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--periods", nargs="+", default=["2024-01-01:2024-12-31", "2026-01-01:2026-07-31"])
    ap.add_argument("--symbol", default="JP225")
    ap.add_argument("--tfs", nargs="+", default=list(DEFAULT_TFS))
    ap.add_argument("--windows", nargs="+", type=int, default=list(DEFAULT_WINDOWS))
    ap.add_argument("--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS))
    ap.add_argument("--perms", type=int, default=DEFAULT_PERMS)
    ap.add_argument("--dev-q", type=float, default=0.10, help="下方乖離エントリーの因果分位")
    ap.add_argument("--dev-horizons", nargs="+", type=int, default=[5, 20])
    ap.add_argument("--band-window", type=int, default=1000, help="乖離率分位の因果窓（本）")
    ap.add_argument("--no-dwell", action="store_true", help="滞在秒加重（測定 1b）を省く")
    ap.add_argument("--confirm-only", action="store_true",
                    help="確定条件（条件付 Δspread→rv）だけを回す")
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out: "dict[str, Any]" = {"gate_ratio": GATE_RATIO, "periods": {}}
    all_rows: "list[dict[str, Any]]" = []
    all_tests: "list[dict[str, Any]]" = []
    all_devs: "list[dict[str, Any]]" = []
    all_uses: "list[dict[str, Any]]" = []
    all_mech: "list[dict[str, Any]]" = []
    forming: "list[dict[str, Any]]" = []

    for spec in args.periods:
        lo, hi = spec.split(":")
        print(f"\n=== 期間 {spec} ===", flush=True)
        m1 = build_m1(lo, hi, args.symbol, with_dwell=not args.no_dwell)
        print(f"M1 {len(m1):,} 本 / {m1.index[0]} .. {m1.index[-1]}", flush=True)
        res = measure_period(
            spec, m1, tuple(args.tfs), tuple(args.windows), tuple(args.horizons),
            args.perms, args.seed, args.dev_q, tuple(args.dev_horizons), args.band_window,
            args.confirm_only,
        )
        out["periods"][spec] = res
        all_rows += res["rows"]
        all_tests += res["tests"]
        all_devs += res["devs"]
        all_uses += res["uses"]
        all_mech += res["mech"]

        paths = day_paths(pd.Timestamp(lo), pd.Timestamp(hi), args.symbol)
        forming.append(measure_forming(paths[len(paths) // 2], args.seed))

    print("\n### 測定 1: 非退化ゲート（D = 疑似VWAP − SMA）")
    print_table(all_rows, ["period", "tf", "n", "bars", "tr_median", "abs_d_median",
                           "d_over_tr", "corr_sma", "abs_twap_diff_median", "twap_over_d"])
    print(f"\n判定基準（事前登録）: d_over_tr >= {GATE_RATIO} で通過")
    worst = min(r["d_over_tr"] for r in all_rows)
    best = max(r["d_over_tr"] for r in all_rows)
    print(f"d_over_tr の範囲: {worst:.4f} .. {best:.4f} → "
          f"{'通過' if worst >= GATE_RATIO else ('一部通過' if best >= GATE_RATIO else '不通過')}")

    print("\n### 測定 2: 依頼原式（価格帯経由）の量子化誤差")
    print_table(all_rows, ["period", "tf", "n", "abs_d_median", "q_err_g10_median",
                           "q_err_g10_over_d", "q_err_fine_median", "q_err_fine_over_d"])

    print("\n### 測定 2b: 既存列だけの近似（新規列なし）で代替できるか")
    print_table(all_rows, ["period", "tf", "n", "abs_d_median", "bar_tp_err_median",
                           "bar_tp_over_d", "bar_oc_err_median", "bar_oc_over_d"])

    print("\n### 測定 3: 情報量（非重複標本・ブロック順列・Holm 補正）")
    print_table([t for t in all_tests if "p" in t],
                ["period", "tf", "n", "h", "n_up", "n_dn", "above_bp", "below_bp",
                 "diff_bp", "null_sd_bp", "p", "p_holm"])
    sig = [t for t in all_tests if t.get("p_holm", 1.0) < 0.05]
    print(f"Holm 後 p<0.05: {len(sig)} 件 / {len([t for t in all_tests if 'p' in t])} 件")

    print("\n### 測定 5: 下方乖離ロング（押し目買い）— 疑似VWAP乖離率 vs SMA乖離率")
    # Holm は集合（set）ごとの族内で補正する。判定の主役は pvwap_only（pv 列を足す価値の直接判定）。
    for name in ("pvwap", "sma", "pvwap_only", "sma_only", "both"):
        fam = [d for d in all_devs if d["set"] == name and "p" in d]
        for d, adj in zip(fam, holm([d["p"] for d in fam])):
            d["p_holm"] = adj
    for name in ("pvwap_only", "sma_only", "both", "pvwap", "sma"):
        fam = [d for d in all_devs if d["set"] == name and "p" in d]
        if not fam:
            continue
        print(f"\n-- 集合 {name}（Holm は本族内 {len(fam)} 件で補正）")
        print_table(fam, ["period", "tf", "n", "h", "n_entries", "mean_bp", "base_bp",
                          "excess_bp", "null_sd_bp", "p", "p_holm"])
        hit = [d for d in fam if d["p_holm"] < 0.05]
        print(f"Holm 後 p<0.05: {len(hit)} 件 / {len(fam)} 件")

    print("\n### 測定 6: 別用法（A: pv 固有成分 / B: セッションアンカーVWAP / C: ボラ代理）")
    for name in sorted({u["use"] for u in all_uses}):
        fam = [u for u in all_uses if u["use"] == name and "p" in u]
        if not fam:
            skipped = [u for u in all_uses if u["use"] == name]
            print(f"\n-- {name}: 全 {len(skipped)} 条件が標本不足")
            continue
        for u, adj in zip(fam, holm([u["p"] for u in fam])):
            u["p_holm"] = adj
        print(f"\n-- {name}（Holm は本族内 {len(fam)} 件で補正）")
        if "diff_bp" in fam[0]:
            print_table(fam, ["period", "tf", "n", "h", "n_top", "n_bot", "top_bp",
                              "bot_bp", "diff_bp", "null_sd_bp", "p", "p_holm"])
        else:
            print_table(fam, ["period", "tf", "n", "h", "n_entries", "mean_bp", "base_bp",
                              "excess_bp", "null_sd_bp", "p", "p_holm"])
        hit = [u for u in fam if u["p_holm"] < 0.05]
        print(f"Holm 後 p<0.05: {len(hit)} 件 / {len(fam)} 件")

    if all_mech:
        print("\n### 測定 7: Δspread 上位群 / 下位群は何が違うバーか（記述統計・中央値）")
        print_table(all_mech, ["period", "tf", "n", "covar", "top", "bot", "ratio"])

    print("\n### 測定 4: 形成中バー（部分窓）での厳密性")
    print_table(forming, ["file", "checks", "max_abs_error"])

    out["forming"] = forming
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\nJSON: {args.json}")


if __name__ == "__main__":
    main()
