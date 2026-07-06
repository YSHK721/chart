#!/usr/bin/env python3
"""Market Profile 試作（TPO＝時間累積の価格帯ヒートマップ）  JP225 日足

仕様:
  - 価格帯(ビン)ごとに「価格がそこに滞在した時間の累積」=TPO を集計。
    本試作の時間単位 = 表示足(日足)1本。足の[low, high]が跨ぐ各ビンに +1（=その帯で1日値が付いた）。
  - 累積値が多い価格帯ほど赤（青=低→赤=高のヒート）で表現し、視覚的に判断可能にする。
  - POC(Point of Control=最頻価格帯) と バリューエリア(VA=TPOの70%が収まる価格帯)を算出・明示。
  - ローソク足と価格軸(y)を共有して横ヒストグラムを並置＝チャート上から確認できる。

入力 : prototype_260626-01/data.json の timeframe（読み取り専用）。
出力 : prototype_260630-01/out/market_profile.png

設計メモ:
  - ビン N_BINS は窓の価格レンジを等分。bin_size = range/N_BINS。
  - VA は POC から TPO 降順にビンを足し、累積が総TPOの VA_PCT(70%) に達するまで。
  - カラー: 連続ヒート(turbo)。max→赤、min→青。POC は最濃赤。
  - 時間単位は CONFIG で 1D/4h を切替可（4h=より細かい時間累積）。
"""
import json
import os
import datetime as dt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

# ---- CONFIG ----
DISPLAY_TF = "1D"     # ローソク表示の足
TPO_TF = "1D"         # 時間累積の単位足(1D or 4h)。4hで細かい時間分解能
WINDOW_BARS = 120     # 直近何本を対象にするか(表示足ベース)
N_BINS = 60           # 価格ビン
VA_PCT = 0.70         # バリューエリア割合
CMAP = "turbo"        # 青(低)→赤(高) のヒート


def load_tf(tf):
    d = json.load(open(DATA))
    c = d["timeframes"][tf]["candles"]
    return c


def compute_tpo(bars, price_min, price_max, n_bins):
    """各価格ビンに、足の[low,high]が跨ぐごとに +1（時間累積）。"""
    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    tpo = np.zeros(n_bins, dtype=float)
    for b in bars:
        lo = b["low"]; hi = b["high"]
        i0 = max(0, np.searchsorted(edges, lo, side="right") - 1)
        i1 = min(n_bins - 1, np.searchsorted(edges, hi, side="right") - 1)
        tpo[i0:i1 + 1] += 1.0
    return edges, centers, tpo


def value_area(centers, tpo, va_pct):
    """POC から TPO 降順にビンを集め、累積が総TPO×va_pct に達する範囲。"""
    total = tpo.sum()
    poc = int(np.argmax(tpo))
    order = np.argsort(tpo)[::-1]
    acc = 0.0
    chosen = []
    for idx in order:
        chosen.append(idx)
        acc += tpo[idx]
        if acc >= total * va_pct:
            break
    chosen = np.array(chosen)
    return poc, float(centers[poc]), float(centers[chosen].min()), float(centers[chosen].max())


def run():
    disp = load_tf(DISPLAY_TF)[-WINDOW_BARS:]
    # TPO 用の足を、表示窓の時間範囲に合わせて抽出
    t0, t1 = disp[0]["time"], disp[-1]["time"]
    tpo_bars = [b for b in load_tf(TPO_TF) if t0 <= b["time"] <= t1]

    p_min = min(b["low"] for b in disp)
    p_max = max(b["high"] for b in disp)
    edges, centers, tpo = compute_tpo(tpo_bars, p_min, p_max, N_BINS)
    poc_i, poc, val, vah = value_area(centers, tpo, VA_PCT)

    def ymd(ts):
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()

    print("=" * 70)
    print(f"Market Profile 試作  JP225 {DISPLAY_TF}  窓 {WINDOW_BARS}本 "
          f"({ymd(t0)}〜{ymd(t1)})")
    print("=" * 70)
    print(f"価格レンジ {p_min:,.0f}〜{p_max:,.0f}  ビン {N_BINS}  "
          f"bin幅 {(p_max-p_min)/N_BINS:,.0f}  時間単位={TPO_TF} {len(tpo_bars)}本")
    print(f"POC(最頻価格帯) = {poc:,.0f}  (TPO {int(tpo[poc_i])})")
    print(f"バリューエリア({int(VA_PCT*100)}%) = {val:,.0f} 〜 {vah:,.0f}")
    print(f"現値 = {disp[-1]['close']:,.0f}  → "
          f"{'VA上(割高側)' if disp[-1]['close']>vah else ('VA下(割安側)' if disp[-1]['close']<val else 'VA内(均衡)')}")

    summary = {
        "tf": DISPLAY_TF, "window": WINDOW_BARS, "from": str(ymd(t0)), "to": str(ymd(t1)),
        "price_min": p_min, "price_max": p_max, "n_bins": N_BINS,
        "tpo_tf": TPO_TF, "tpo_units": len(tpo_bars),
        "poc": poc, "va_low": val, "va_high": vah, "last_close": disp[-1]["close"],
        "profile": [{"price": round(float(c), 1), "tpo": int(v)} for c, v in zip(centers, tpo)],
    }
    json.dump(summary, open(os.path.join(OUT, "market_profile.json"), "w"),
              ensure_ascii=False, indent=2)

    _plot(disp, edges, centers, tpo, poc, val, vah)
    print(f"\n出力: {OUT}/market_profile.png , market_profile.json")


def _plot(disp, edges, centers, tpo, poc, val, vah):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors

    fig, (axp, axm) = plt.subplots(
        1, 2, figsize=(14, 7), sharey=True,
        gridspec_kw={"width_ratios": [3, 1], "wspace": 0.02})

    # --- 左: ローソク足 ---
    for i, b in enumerate(disp):
        up = b["close"] >= b["open"]
        col = "#26a69a" if up else "#ef5350"
        axp.plot([i, i], [b["low"], b["high"]], color=col, lw=0.6, zorder=2)
        axp.add_patch(plt.Rectangle(
            (i - 0.3, min(b["open"], b["close"])), 0.6, abs(b["close"] - b["open"]) + 1e-9,
            color=col, zorder=3))
    axp.set_xlim(-1, len(disp))
    # POC / VA ライン
    axp.axhline(poc, color="#d62728", lw=1.6, ls="-", zorder=4, label=f"POC {poc:,.0f}")
    axp.axhline(vah, color="#888", lw=1.0, ls="--", zorder=4, label=f"VAH {vah:,.0f}")
    axp.axhline(val, color="#888", lw=1.0, ls="--", zorder=4, label=f"VAL {val:,.0f}")
    axp.set_title("JP225 candles + Market Profile (TPO heat)")
    axp.set_ylabel("price")
    axp.legend(loc="upper left", fontsize=8)
    # x軸 日付
    idxs = np.linspace(0, len(disp) - 1, 5).astype(int)
    axp.set_xticks(idxs)
    axp.set_xticklabels(
        [dt.datetime.fromtimestamp(int(disp[i]["time"]), dt.timezone.utc).strftime("%y/%m")
         for i in idxs], fontsize=8)

    # --- 右: TPO 横ヒートヒストグラム（多いほど赤）---
    norm = colors.Normalize(vmin=tpo.min(), vmax=tpo.max())
    cmap = matplotlib.colormaps[CMAP]
    h = edges[1] - edges[0]
    for c, v in zip(centers, tpo):
        axm.barh(c, v, height=h * 0.92, color=cmap(norm(v)), edgecolor="none")
    axm.axhline(poc, color="#d62728", lw=1.6)
    axm.axhline(vah, color="#888", lw=1.0, ls="--")
    axm.axhline(val, color="#888", lw=1.0, ls="--")
    axm.set_xlabel("TPO (accumulated time)")
    axm.set_title("time-at-price")
    # VA レンジ薄塗り
    axm.axhspan(val, vah, color="#ffd54f", alpha=0.12, zorder=0)
    axp.axhspan(val, vah, color="#ffd54f", alpha=0.10, zorder=1)

    sm = cm.ScalarMappable(norm=norm, cmap=cmap); sm.set_array([])
    cb = fig.colorbar(sm, ax=axm, fraction=0.06, pad=0.02)
    cb.set_label("low ← TPO → high (red=hot)")

    fig.suptitle("Market Profile prototype — price bands colored by accumulated time (red = most)",
                 fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(os.path.join(OUT, "market_profile.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
