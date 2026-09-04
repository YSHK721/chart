"""Step9: 過去の高 z 受容水準への **naked（形成後初回）再訪** 時の反応（ISSUE-061）。

問い（Step6 Part B の対）:
    Step6 Part B は「乖離 → 新価格で POC* が新規形成される」を実証した（p≈3.5e-6）。
    本ステップはその対、すなわち **古い受容水準は再訪時に S/R として機能するか** を問う。

事前登録（ISSUE-061 の「平易な確定版」・2026-07-12）:
    主検定は **1 本**。z_thr = 3 / L = 60 日 / k = 30 分 / x = 4 行。
    - 事件: 過去 L 日内に形成された高 z セル（z ≥ z_thr）への **形成後初回**接触（naked）。
      2 回目以降は別群（本ステップは naked のみを主検定に用いる）。
    - 反応（主検定の物差し）: **跳ね返り** ＝ 接触後 k 分以内に、接近方向と逆へ x 行以上動く。
    - 比較: 反応の絶対値ではなく、偽水準への同一物差しの反応との **差** のみを勘定する。
      偽水準 A ＝ 同日の低 z セル（本ステップで実装）。
      偽水準 B ＝ Null B サロゲートが偶然作った偽こだわり水準（**未実装**・下記「範囲外」参照）。

範囲外（本ステップでは測らない）:
    ISSUE-061 の「反応 3 物差し」のうち **滞在**（水準近傍の滞在分数）と **減速**（帯の通過所要
    時間）は、`水準近傍` / `帯` の幅が仕様として確定していない。ISSUE-061 自身が
    「**『反応』の操作的定義は依頼者の言葉で確定してから実装する**（仮説文の解釈違い再発防止）」
    と定めているため、推測で幅を決めずに未実装とする。主検定（跳ね返り）は幅の定義を要さず
    事前登録どおり一意に実装できるため、本ステップはそこに限定する。

データ資産（いずれも既存キャッシュ・再計算しない）:
    - `znull`: 日ごと・行ごとの `obs` / `mean` / `var` → `z = (obs − mean) / sqrt(var)`。
    - `mgrid`: 日ごとの分足 close 列（前値補間済み・1 日 ~1,335 分）。
    行 → 価格の写像は **`price = exp(k · grid_w · 1e-4)`**（対数価格 1e-4 格子）。
    実測で 400/400 日において行域が当日の分足レンジを包含することを確認済み。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: 事前登録パラメータ（変更禁止。感度分析は別途 Bonferroni で束ねる）。
Z_THRESHOLD = 3.0
LOOKBACK_DAYS = 60
REACTION_MINUTES = 30
BOUNCE_ROWS = 4

#: 偽水準 A（同日の低 z セル）の上限 z。高 z 側と重ならない水準に採る。
NULL_A_Z_MAX = 0.5

#: 行 → 価格の写像（対数価格の 1e-4 格子）。znull の **セル**（z を定義する単位）はこの格子。
_LOG_UNIT = 1e-4

#: 反応距離の単位となる「行」の本数。Step5/Step6 と同一（日レンジを 40 等分＝1 行が日レンジの 2.5%）。
#: ISSUE-061 の `x = 4 行` は Step6 の `DEPART_ROWS = 4`（コメントに「日レンジの 10%」と明記）と
#: 同じ語彙であり、**znull のセル幅ではない**。セル幅（≒0.9pt @ 9,000）で測ると 4 行 ≒ 3.6pt となり、
#: 30 分あればほぼ常に到達するため検定が飽和する（実測: 本物 72.4% / 偽 72.9% と両群とも高止まり）。
N_ROWS_DAILY = 40


@dataclass(frozen=True)
class DayGrid:
    """1 営業日の z 行グリッドと分足経路。"""

    day: int
    row_price: "np.ndarray"     # (R,) 行中心価格
    z: "np.ndarray"             # (R,) 行ごとの z
    closes: "np.ndarray"        # (M,) 分足 close（前値補間済み）
    cell_width: float           # znull セル幅（z を定義する単位・接触判定に使う）
    row_width: float            # 反応距離の単位（日レンジ / 40・Step5/6 と同一）


def row_prices(kmin: int, n_rows: int, grid_w: float) -> "np.ndarray":
    """行インデックス列 → 価格列（``price = exp(k · grid_w · 1e-4)``）。"""
    k = kmin + np.arange(n_rows, dtype=np.float64)
    return np.exp(k * grid_w * _LOG_UNIT)


def load_day(znull_dir: Path, mgrid_dir: Path, day: int) -> "DayGrid | None":
    """1 日ぶんの z 行グリッドと分足経路を読む。どちらか欠落・空なら None。"""
    zf, mf = znull_dir / f"{day}.npz", mgrid_dir / f"{day}.npz"
    if not zf.exists() or not mf.exists():
        return None
    zd = np.load(zf)
    md = np.load(mf)
    if bool(zd["empty"]) or bool(md["empty"]):
        return None
    obs, mean, var = zd["obs"], zd["mean"], zd["var"]
    if obs.size == 0:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (obs - mean) / np.sqrt(var)
    z = np.where(np.isfinite(z), z, -np.inf)
    grid_w = float(zd["grid_w"])
    prices = row_prices(int(zd["kmin"]), int(obs.size), grid_w)
    if prices.size < 2:
        return None
    cell = float(np.median(np.diff(prices)))
    day_range = float(prices[-1] - prices[0])
    row = day_range / N_ROWS_DAILY
    closes = np.asarray(md["closes"], dtype=np.float64)
    if closes.size < REACTION_MINUTES + 2 or cell <= 0 or row <= 0:
        return None
    return DayGrid(day=day, row_price=prices, z=z, closes=closes,
                   cell_width=cell, row_width=row)


def _first_touch(closes: "np.ndarray", level: float, tol: float) -> "int | None":
    """`level` へ最初に接触した分 index。無ければ None。

    接触は「セル内に入る（``|close − level| <= tol``）」**または**「水準をまたぐ（符号反転）」で
    判定する。セル幅は ≒0.9pt（@9,000）と細いため、近接判定だけでは 1 分で跨いだ再訪を取り
    こぼす（＝事件の系統的欠落）。
    """
    near = np.abs(closes - level) <= tol
    diff = closes - level
    cross = np.zeros(closes.size, dtype=bool)
    cross[1:] = (diff[:-1] > 0) != (diff[1:] > 0)
    hit = np.flatnonzero(near | cross)
    return int(hit[0]) if hit.size else None


def bounced(closes: "np.ndarray", idx: int, level: float,
            row_width: float, cell_width: float) -> "bool | None":
    """接触後 k 分以内に、接近方向と逆へ x 行以上動いたか。

    接近方向は「接触直前の位置が水準の上か下か」で決める（上から来たら反発＝上へ戻る）。
    直前が水準上（`|diff| <= tol`）で方向が定まらない場合は判定不能として None を返す。
    """
    if idx <= 0:
        return None
    tol = cell_width / 2.0
    prev = closes[idx - 1]
    if abs(prev - level) <= tol:
        return None                       # 接近方向が定まらない（既に水準上）
    from_above = prev > level
    end = min(closes.size, idx + REACTION_MINUTES + 1)
    window = closes[idx:end]
    if window.size < 2:
        return None
    need = BOUNCE_ROWS * row_width
    return bool(window.max() - level >= need) if from_above else bool(level - window.min() >= need)


def collect_levels(grid: DayGrid, *, z_min: float, z_max: "float | None" = None) -> "np.ndarray":
    """当日の行のうち z が指定域にあるものの価格列を返す。"""
    sel = grid.z >= z_min if z_max is None else (grid.z >= z_min) & (grid.z <= z_max)
    return grid.row_price[sel]


def run_step9(
    znull_dir: Path,
    mgrid_dir: Path,
    days: "list[int]",
    *,
    z_threshold: float = Z_THRESHOLD,
    lookback: int = LOOKBACK_DAYS,
    max_days: "int | None" = None,
) -> "dict[str, object]":
    """naked 初回接触の跳ね返り率を、本物の高 z 水準と偽水準 A について測る。

    Returns:
        件数と跳ね返り率、および 2 群の差（本物 − 偽水準 A）。
    """
    days = sorted(days)
    if max_days is not None:
        days = days[:max_days]

    grids: "dict[int, DayGrid]" = {}
    for d in days:
        g = load_day(znull_dir, mgrid_dir, d)
        if g is not None:
            grids[d] = g
    usable = sorted(grids)

    #: 形成日ごとの水準（本物 / 偽 A）。
    real_by_day = {d: collect_levels(grids[d], z_min=z_threshold) for d in usable}
    fake_by_day = {d: collect_levels(grids[d], z_min=-np.inf, z_max=NULL_A_Z_MAX) for d in usable}

    #: 形成後に一度でも接触した水準は naked でなくなる（以後は別群）。
    touched_real: "set[tuple[int, float]]" = set()
    touched_fake: "set[tuple[int, float]]" = set()

    per_day: "dict[str, dict[int, tuple[int, int]]]" = {"real": {}, "fake_a": {}}

    def scan(levels_by_day, touched, label):
        n_events = n_bounce = n_undecided = 0
        for i, e in enumerate(usable):
            g = grids[e]
            tol = g.cell_width / 2.0
            lo = max(0, i - lookback)
            for d in usable[lo:i]:                      # 形成日 d < 接触日 e
                for lv in levels_by_day[d]:
                    key = (d, round(float(lv), 6))
                    if key in touched:
                        continue                        # 既訪問＝naked でない
                    idx = _first_touch(g.closes, float(lv), tol)
                    if idx is None:
                        continue
                    touched.add(key)                    # 以後は別群
                    verdict = bounced(g.closes, idx, float(lv), g.row_width, g.cell_width)
                    if verdict is None:
                        n_undecided += 1
                        continue
                    n_events += 1
                    n_bounce += int(verdict)
                    b, t = per_day[label].get(e, (0, 0))
                    per_day[label][e] = (b + int(verdict), t + 1)
        rate = (n_bounce / n_events) if n_events else float("nan")
        return {f"{label}_events": n_events, f"{label}_bounces": n_bounce,
                f"{label}_rate": rate, f"{label}_undecided": n_undecided}

    real = scan(real_by_day, touched_real, "real")
    fake = scan(fake_by_day, touched_fake, "fake_a")

    out: "dict[str, object]" = {
        "n_days_scanned": len(usable),
        "z_threshold": z_threshold,
        "lookback_days": lookback,
        "reaction_minutes": REACTION_MINUTES,
        "bounce_rows": BOUNCE_ROWS,
        **real, **fake,
    }
    r1, n1 = real["real_rate"], real["real_events"]
    r0, n0 = fake["fake_a_rate"], fake["fake_a_events"]
    out["diff_real_minus_fake_a"] = (r1 - r0) if (n1 and n0) else float("nan")
    if n1 and n0:
        p = (real["real_bounces"] + fake["fake_a_bounces"]) / (n1 + n0)
        se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0)) if 0 < p < 1 else float("nan")
        out["z_two_proportion"] = ((r1 - r0) / se) if se and math.isfinite(se) and se > 0 else float("nan")
    else:
        out["z_two_proportion"] = float("nan")

    # 日単位クラスタでの推論（ISSUE-061 の規律）。
    #   事件は同一日に何十件も生じ互いに独立でない。素の 2 標本比率 z は有効標本数を
    #   事件数と見なすため**有意性を大きく過大評価する**。接触日を 1 標本として、
    #   同日内の「本物 − 偽水準A」の跳ね返り率差を対にして評価する。
    days_common = sorted(set(per_day["real"]) & set(per_day["fake_a"]))
    diffs = []
    for e in days_common:
        rb, rt = per_day["real"][e]
        fb, ft = per_day["fake_a"][e]
        if rt >= 3 and ft >= 3:           # 1 日あたり最低件数（率が 0/1 に張り付くのを避ける）
            diffs.append(rb / rt - fb / ft)
    d = np.asarray(diffs, dtype=float)
    out["n_days_paired"] = int(d.size)
    if d.size >= 30:
        mean = float(d.mean())
        se = float(d.std(ddof=1) / math.sqrt(d.size))
        out["day_clustered_mean_diff"] = mean
        out["day_clustered_t"] = (mean / se) if se > 0 else float("nan")
        out["day_clustered_positive_share"] = float((d > 0).mean())
    else:
        out["day_clustered_mean_diff"] = float("nan")
        out["day_clustered_t"] = float("nan")
        out["day_clustered_positive_share"] = float("nan")
    return out
