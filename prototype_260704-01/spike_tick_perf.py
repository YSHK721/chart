#!/usr/bin/env python3
"""前倒し検出スパイク: MP を forming バー内で 1tick 毎に更新する仕様の性能検証。

命題: 売買戦略組み立てのため MP を tick 解像度で成長させる。参照(prototype_260630-01)は
バー粒度までしか定義しないため、サブバー tick 逐次成長は本スパイクで挙動/性能を前倒し検出する。

検証対象の設計:
  - 基準(base): forming バー開始時点までの累積 dwell 固定グリッド(GRID_W)。forming 中は不変。
  - forming 増分(streaming): 新 tick 到着で「直前 tick の dwell(=次tickまでの経過秒×アクティブ)」を
    固定グリッドへ O(1) 加算。profile = base + forming。
  - per-tick 更新: 表示 bin へ再集計(O(bins)) + POC/VA(O(bins log bins))。

比較: (A) 増分方式 per-tick / (B) 素朴な全再計算 per-tick（forming 全 tick を毎回 dwell 集計）。
"""
import time
import numpy as np
import pandas as pd

GRID_W = 10.0
N_BINS = 60
VA_PCT = 0.70
DAY = "data/marketdata/ticks/2026/07/02/JP225_ticks.parquet"


def load_day(path):
    df = pd.read_parquet(path, columns=["timestamp", "bidPrice", "askPrice"])
    secs = df["timestamp"].dt.tz_localize(None).to_numpy().astype("datetime64[s]").astype("int64")
    mids = ((df["bidPrice"] + df["askPrice"]) / 2.0).to_numpy()
    m = float(np.nanmedian(mids))
    keep = np.abs(mids / m - 1.0) <= 0.3
    secs, mids = secs[keep], mids[keep]
    o = np.argsort(secs, kind="stable")
    return secs[o], mids[o]


def value_area(centers, tpo, va_pct):
    total = tpo.sum()
    if total <= 0:
        return 0, centers[0], centers[0], centers[-1]
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


def reaggregate(fine, kw0, size, price_min, price_max, n_bins):
    """固定グリッド fine[] → 表示 bin(n_bins) へ再集計し POC/VA を算出（参照 compute_profile 準拠）。"""
    binw = (price_max - price_min) / n_bins
    centers_fine = (kw0 + np.arange(size) + 0.5) * GRID_W
    disp = np.clip(((centers_fine - price_min) / binw).astype(int), 0, n_bins - 1)
    tpo = np.zeros(n_bins)
    np.add.at(tpo, disp, fine[:size])
    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    return value_area(centers, tpo, VA_PCT)


def main():
    secs, mids = load_day(DAY)
    n = len(secs)
    print(f"forming 日 tick 数: {n}  価格域: {mids.min():.1f}..{mids.max():.1f}")

    # 価格域（base 含む想定で固定グリッド枠を用意）。ここでは forming 日の域を使う。
    price_min = float(np.floor(mids.min() / GRID_W) * GRID_W)
    price_max = float(np.ceil(mids.max() / GRID_W) * GRID_W)
    kw0 = int(np.floor(price_min / GRID_W))
    size = int(np.floor(price_max / GRID_W)) - kw0 + 1
    k = np.floor(mids / GRID_W).astype(np.int64) - kw0
    k = np.clip(k, 0, size - 1)

    # base（forming 開始前の累積）は forming 中不変。ここでは 0 起点（純 forming 成長を測る）。
    base_fine = np.zeros(size)

    # ---- (A) 増分方式: per-tick で直前 tick の dwell を加算 → 再集計 → POC/VA ----
    fine = base_fine.copy()
    per_tick = np.zeros(n)
    poc_last = val_last = vah_last = 0.0
    for i in range(1, n):
        t0 = time.perf_counter()
        gap = float(secs[i] - secs[i - 1])          # 直前 tick の滞在秒（アクティブ判定は本スパイクでは簡略=全active）
        fine[k[i - 1]] += gap                        # O(1) 加算
        poc_last, poc_p, val_last, vah_last = reaggregate(
            fine, kw0, size, price_min, price_max, N_BINS)
        per_tick[i] = (time.perf_counter() - t0) * 1000.0  # ms
    pt = per_tick[1:]
    print("\n[A] 増分方式 per-tick 更新レイテンシ (ms):")
    print(f"  mean={pt.mean():.4f}  p50={np.percentile(pt,50):.4f} "
          f"p99={np.percentile(pt,99):.4f}  max={pt.max():.4f}")
    print(f"  全 {n} tick 累積処理時間: {pt.sum():.1f} ms  最終 POC={poc_p:.1f} VA=[{val_last:.1f},{vah_last:.1f}]")

    # ---- (B) 素朴な全再計算: forming so-far を毎回 dwell 集計（サンプルで O(n) 悪化を提示）----
    sample_idx = list(range(1000, n, max(1, n // 8)))
    naive_ms = []
    for j in sample_idx:
        t0 = time.perf_counter()
        ss = secs[:j + 1]
        gaps = (ss[1:] - ss[:-1]).astype(float)
        f2 = np.zeros(size)
        np.add.at(f2, k[:j], gaps)                   # forming so-far 全 tick を毎回集計
        reaggregate(f2, kw0, size, price_min, price_max, N_BINS)
        naive_ms.append((time.perf_counter() - t0) * 1000.0)
    print("\n[B] 素朴な全再計算 per-tick（forming so-far 全集計・サンプル）:")
    for j, ms in zip(sample_idx, naive_ms):
        print(f"  tick#{j:>6}: {ms:.3f} ms")
    print(f"  → forming が進むほど O(n) で線形悪化（末尾 {naive_ms[-1]:.1f}ms）。増分方式は不変。")


if __name__ == "__main__":
    main()
