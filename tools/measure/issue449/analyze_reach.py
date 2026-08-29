"""ISSUE-449 §9-1 の実測（第 2 版）。第 1 版の偏りを 2 つ是正した。

第 1 版の欠陥:
  1. **標本をエピソード始端に取った**。新しい目標は「価格が直前の目標を通過した直後」に
     現れるので、始端は価格のすぐ近くに偏る（中期の距離が全標本 127 点に対し始端 35 点）。
     結果、到達率 88〜96% は「目標が目の前にある瞬間だけを見た」値だった。
     → **一定間隔の周期標本**に変える（画面を見た任意の瞬間に相当）。
  2. **対照の距離が揃っていなかった**。v±(20〜60) 点にずらしたため、距離の違いが反応の違いに
     混ざる。→ **別の時刻で同じ距離**の価格を対照にする（距離を厳密に揃え、水準性だけを外す）。

定義は第 1 版と同じ（到達＝固定した v へ high/low が触れる。反応＝先に 15 点戻るか進むか）。
"""

# --- 実行時パス（唯一の設定） -------------------------------------------------
#   ISSUE449_DIR : 作業ディレクトリ。export.json を置き、生成物もここへ出す。
#   ISSUE449_API : ライブ計算サーバ。indigators/indicator_ui/serve.sh の内部ポート。
import os as _os
D = _os.environ.get("ISSUE449_DIR", _os.path.dirname(_os.path.abspath(__file__))) + "/"
BASE = _os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")

import json, random, statistics, collections
from array import array

random.seed(20260829)
h = json.load(open(D + 'long.json'))
series, candles = h["series"], h["candles"]
TFO = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]

T = [c["time"] for c in candles]
C = array('d', [float(c["close"]) for c in candles])
HI = array('d', [float(c["high"]) for c in candles])
LO = array('d', [float(c["low"]) for c in candles])
N = len(T)
NAN = float('nan')

grids, rank = [], []
for s in series:
    g = array('d', [NAN]) * N
    ts, vs, j = s["t"], s["v"], -1
    for i in range(N):
        while j + 1 < len(ts) and ts[j + 1] <= T[i]:
            j += 1
        if j >= 0:
            g[i] = vs[j]
    grids.append(g)
    rank.append(TFO.index(s["tf"]))

print(f"1m 標本 {N} 本 / 水準 {len(series)} 本 / {(T[-1]-T[0])/86400:.1f} 日")

STEP = 60                                    # 周期標本の間隔（分）。重なりを避ける
HOR = [("短期(全部)", 0, 120), ("中期(1h 以上)", 3, 480), ("長期(1D 以上)", 5, 2880)]


def nearest(i, rank_min, up):
    p, best = C[i], None
    for k in range(len(grids)):
        if rank[k] < rank_min:
            continue
        v = grids[k][i]
        if v != v:
            continue
        if up and v > p and (best is None or v < best):
            best = v
        elif (not up) and v <= p and (best is None or v > best):
            best = v
    return best


def touch(i0, v, up, K):
    end = min(N, i0 + 1 + K)
    for i in range(i0 + 1, end):
        if (up and HI[i] >= v) or ((not up) and LO[i] <= v):
            return i
    return None


def reaction(it, v, up, X=15, R=240):
    end = min(N, it + 1 + R)
    for i in range(it, end):
        if up:
            if LO[i] <= v - X:
                return "反転"
            if HI[i] >= v + X:
                return "継続"
        else:
            if HI[i] >= v + X:
                return "反転"
            if LO[i] <= v - X:
                return "継続"
    return None


def far_from_levels(i, v, margin=15.0):
    for k in range(len(grids)):
        u = grids[k][i]
        if u == u and abs(u - v) < margin:
            return False
    return True


print(f"\n=== 到達率・到達までの時間（{STEP} 分ごとの周期標本） ===")
print(f"{'地平':<14}{'向き':<4}{'件数':>6}{'距離(中央)':>11}{'到達率':>8}"
      f"{'到達まで(中央)':>14}{'観測窓':>8}")
samples = list(range(0, N - 1, STEP))
store = {}
for name, r, K in HOR:
    for up in (True, False):
        rows = []
        for i in samples:
            v = nearest(i, r, up)
            if v is None:
                continue
            rows.append((i, v, abs(v - C[i]), touch(i, v, up, K)))
        hit = [x for x in rows if x[3] is not None]
        tt = [x[3] - x[0] for x in hit]
        print(f"{name:<14}{'上' if up else '下':<4}{len(rows):>6}"
              f"{statistics.median([x[2] for x in rows]):>10.0f}点"
              f"{100*len(hit)/max(len(rows),1):>7.0f}%"
              f"{(statistics.median(tt) if tt else 0):>11.0f}分{K:>7}分")
        store[(name, up)] = (rows, K)

import math
def ztest(a, na, b, nb):
    if na == 0 or nb == 0: return float('nan')
    pa, pb = a/na, b/nb
    pp = (a+b)/(na+nb)
    se = math.sqrt(pp*(1-pp)*(1/na+1/nb))
    return (pa-pb)/se if se > 0 else float('nan')

for X in (15, 30):
  print(f"\n=== 反応: 到達後、先に {X} 点戻る(反転)か {X} 点進む(継続)か ===")
  print("   対照 = 別の時刻・同じ距離・どの水準からも 15 点以上離れた価格")
  print(f"{'地平':<14}{'向き':<4}{'水準 到達':>9}{'水準 反転率':>12}"
      f"{'対照 到達':>9}{'対照 反転率':>12}{'差':>8}{'z':>7}")
  for name, r, K in HOR:
    for up in (True, False):
        rows, K = store[(name, up)]
        hit = [x for x in rows if x[3] is not None]
        real = collections.Counter(reaction(x[3], x[1], up, X) for x in hit)
        # 対照: 同じ距離 d を別の時刻へ当てる
        ctrl, tried = collections.Counter(), 0
        ctrl_hit = 0
        for _, _, d, _ in rows:
            for _ in range(25):
                j = random.randrange(0, N - 1)
                vp = C[j] + d if up else C[j] - d
                if far_from_levels(j, vp):
                    tried += 1
                    itp = touch(j, vp, up, K)
                    if itp is not None:
                        ctrl_hit += 1
                        ctrl[reaction(itp, vp, up, X)] += 1
                    break
        rr = real["反転"] / max(real["反転"] + real["継続"], 1) * 100
        cr = ctrl["反転"] / max(ctrl["反転"] + ctrl["継続"], 1) * 100
        z = ztest(real["反転"], real["反転"]+real["継続"], ctrl["反転"], ctrl["反転"]+ctrl["継続"])
        print(f"{name:<14}{'上' if up else '下':<4}{len(hit):>9}{rr:>11.0f}%"
              f"{ctrl_hit:>9}{cr:>11.0f}%{rr-cr:>+7.1f}pt{z:>+7.1f}")
