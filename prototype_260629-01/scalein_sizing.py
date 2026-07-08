#!/usr/bin/env python3
"""下方乖離での買い増し（スケールイン）サイズ配分の検証  JP225 日足

入口: 22EMA下方乖離が -2/-4/-6/-8% を割るごとに 1トランシェ追加（深いほど edge 大）。
出口: 乖離が 0%(EMA)へ回帰したら全決済。回帰しなければ CAP 日で時間決済。
比較する配分スキーム（[-2,-4,-6,-8% の各重み]）:
  single_shallow [1,0,0,0]  浅い1発のみ
  flat           [1,1,1,1]  等量ナンピン
  increasing     [1,2,3,4]  深いほど大きく（edge追随=マルチンゲール寄り）
  decreasing     [4,3,2,1]  深いほど小さく（リスク抑制=逆マルチンゲール）
評価（投下資金=Σ size*entry に対する%）:
  平均リターン / 中央 / 最悪エピソード / 平均最悪含み損(底MTM) / 最悪含み損(テール) / Ret/|最悪DD|
※ 重要caveat: 本データは14年すべて上昇相場で深押しが毎回回復した期間。martingale 寄りが
  好成績でも、回復しない長期ベアでは破産経路。サイズは“最悪が生存可能か”で決める。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SPAN = 22
LEVELS = [-2.0, -4.0, -6.0, -8.0]
CAP = 80  # 回帰しない時の時間決済(営業日)


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return np.array([x["close"] for x in c], float)


def ema(x, span):
    a = 2.0 / (span + 1.0)
    o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def episodes(dev, close):
    """乖離<-2 で開始、>=0 で終了する押し目エピソードを抽出。
    各エピソード: levels毎の初回到達index、exit index、最安index。"""
    n = len(dev)
    warm = SPAN * 3
    eps = []
    i = warm
    while i < n - 1:
        if dev[i] <= LEVELS[0]:
            start = i
            hit = {}
            j = i
            while j < n - 1 and dev[j] < 0 and (j - start) <= CAP:
                for lv in LEVELS:
                    if lv not in hit and dev[j] <= lv:
                        hit[lv] = j
                j += 1
            exit_i = j  # dev>=0 へ回帰 or CAP
            low_i = start + int(np.argmin(close[start:exit_i + 1]))
            eps.append({"start": start, "exit": exit_i, "low": low_i, "hit": hit})
            i = exit_i + 1
        else:
            i += 1
    return eps


def evaluate(eps, close, weights):
    rows = []
    for e in eps:
        tranches = []  # (entry_price, size, entry_idx)
        for lv, w in zip(LEVELS, weights):
            if w > 0 and lv in e["hit"]:
                tranches.append((close[e["hit"][lv]], w, e["hit"][lv]))
        if not tranches:
            continue
        exitp = close[e["exit"]]
        lowp = close[e["low"]]
        cost = sum(s * p for p, s, _ in tranches)
        realized = sum(s * (exitp - p) for p, s, _ in tranches)
        # 最悪含み損: 底時点で既に入っていたトランシェのMTM
        opened = [(p, s) for p, s, idx in tranches if idx <= e["low"]]
        worst = sum(s * (lowp - p) for p, s in opened)
        worst_cost = sum(s * p for p, s in opened) or cost
        rows.append({"ret": realized / cost * 100,
                     "worst_open": worst / worst_cost * 100,
                     "n_tranche": len(tranches)})
    return rows


def run():
    close = load()
    e = ema(close, SPAN)
    dev = (close - e) / e * 100.0
    eps = episodes(dev, close)

    schemes = {
        "single_shallow[1,0,0,0]": [1, 0, 0, 0],
        "flat       [1,1,1,1]": [1, 1, 1, 1],
        "increasing [1,2,3,4]": [1, 2, 3, 4],
        "decreasing [4,3,2,1]": [4, 3, 2, 1],
    }

    print("=" * 92)
    print(f"下方乖離 買い増し サイズ配分の比較  JP225 日足  押し目エピソード数={len(eps)}")
    print(f"トランシェ閾値 {LEVELS}  出口=EMA回帰 or {CAP}日")
    print("=" * 92)
    print(f"{'配分スキーム':>24} {'平均Ret%':>9} {'中央Ret%':>9} {'最悪Ep%':>9} "
          f"{'平均含み損%':>11} {'最悪含み損%':>11} {'Ret/|DD|':>9}")
    print("-" * 92)
    summary = {"episodes": len(eps), "levels": LEVELS, "schemes": {}}
    for name, w in schemes.items():
        rows = evaluate(eps, close, w)
        ret = np.array([r["ret"] for r in rows])
        wo = np.array([r["worst_open"] for r in rows])
        mean_ret = float(ret.mean()); med_ret = float(np.median(ret))
        worst_ep = float(ret.min())
        mean_wo = float(wo.mean()); worst_wo = float(wo.min())
        retdd = mean_ret / abs(worst_wo) if worst_wo < 0 else float("inf")
        print(f"{name:>24} {mean_ret:8.2f} {med_ret:8.2f} {worst_ep:8.2f} "
              f"{mean_wo:10.2f} {worst_wo:10.2f} {retdd:8.2f}")
        summary["schemes"][name] = {
            "n": len(rows), "mean_ret": round(mean_ret, 3), "median_ret": round(med_ret, 3),
            "worst_episode_ret": round(worst_ep, 3), "mean_worst_open": round(mean_wo, 3),
            "worst_worst_open": round(worst_wo, 3), "ret_over_dd": round(retdd, 3)}

    print("-" * 92)
    print("Ret=投下資金あたり実現損益。含み損=底でのMTM(投下資金比)。最悪含み損=テール(生存判定)。")
    print("※全期間が上昇相場のため increasing が好成績でも、回復しないベアでは最悪含み損が破産経路。")
    json.dump(summary, open(os.path.join(OUT, "scalein_sizing.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(summary, schemes)
    print(f"\n出力: {OUT}/scalein_sizing.json , scalein_sizing.png")


def _plot(summary, schemes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names = list(schemes.keys())
    short = [nm.split("[")[0].strip() for nm in names]
    mret = [summary["schemes"][nm]["mean_ret"] for nm in names]
    wopen = [summary["schemes"][nm]["worst_worst_open"] for nm in names]
    retdd = [summary["schemes"][nm]["ret_over_dd"] for nm in names]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
    ax[0].bar(short, mret, color="#69c"); ax[0].set_title("Mean return % (on deployed)")
    ax[1].bar(short, wopen, color="#c66"); ax[1].set_title("Worst open drawdown % (tail/survival)")
    ax[2].bar(short, retdd, color="#6a6"); ax[2].set_title("Ret / |worst DD|")
    for a in ax:
        a.axhline(0, color="#999", lw=0.6); a.tick_params(axis='x', rotation=25, labelsize=8)
    fig.suptitle("Scale-in size allocation on downward-deviation dip-buy — JP225 daily", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "scalein_sizing.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
