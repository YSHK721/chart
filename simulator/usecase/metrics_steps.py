"""サマリー指標の計算ステップ（入力・途中の値・結果）を組み立てる（2026-09-28 依頼者指示）。

なぜ在るか: 「全ての指標の計算ステップを確認したい」。サマリーの値がどの入力から、どの式で、
どういう途中の値を経て出たかを 1 本のログで追えるようにする。

単一ソース: **最終値は本番と同じ関数**（`metrics_spec` / `mt5_parity`）を呼んで出し、途中の値は
本番と同じ部品（連勝連敗の区切り・DD の配列・HPR の列）で出す。式を写した第 2 の実装は作らない。
各指標の最後に「ログで出した値」と「run の統計」を並べ、一致／不一致を書く。
不一致が出たら、run の統計がログと違う入力から作られている（＝どこかで食い違っている）。

純粋な組み立てだけを持つ（ファイルへ書くのは呼び出し側）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from simulator.usecase import metrics_spec as ms
from simulator.usecase import mt5_parity as mp


@dataclass
class StepSection:
    """指標 1 群ぶんの記録（見出し・手順の行・統計との照合）。"""

    title: str
    lines: "list[str]" = field(default_factory=list)
    #: (指標名, ログで出した値, run の統計の値)
    checks: "list[tuple[str, Any, Any]]" = field(default_factory=list)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        fa, fb = float(a), float(b)
        if math.isnan(fa) and math.isnan(fb):
            return True
        return fa == fb or math.isclose(fa, fb, rel_tol=1e-12, abs_tol=1e-9)
    return a == b


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return repr(v)
    return str(v)


def build_metrics_steps(
    *,
    trades: Sequence[Any],
    balance_curve: Sequence[float],
    equity_curve: Sequence[float],
    initial_deposit: float,
    bar_open_equity: Sequence[float],
    bar_seconds: "float | None",
    stats: Any,
) -> "list[StepSection]":
    """run の入力と統計から、指標ごとの計算ステップを返す。"""
    pnls = ms._pnls(trades)
    sections: "list[StepSection]" = []

    # --- 入力 -------------------------------------------------------------------------
    s = StepSection("0. 入力（run が統計へ渡した値）")
    s.lines += [
        f"初期証拠金 B0 = {_fmt(float(initial_deposit))}",
        f"確定トレード数 = {len(trades)}（各トレードの損益 pnl = (決済−建値)×符号×ロット×契約サイズ + スワップ + 手数料）",
        f"残高の系列（決済ごと）= {len(balance_curve)} 点"
        + (f"（最初 {_fmt(float(balance_curve[0]))} / 最後 {_fmt(float(balance_curve[-1]))}）" if len(balance_curve) else ""),
        f"有効証拠金の系列（評価点ごと）= {len(equity_curve)} 点",
        f"足ごとの有効証拠金（各足の最初の評価点）= {len(bar_open_equity)} 本・足の秒数 = {bar_seconds}",
    ]
    sections.append(s)

    # --- 1. 損益 -----------------------------------------------------------------------
    s = StepSection("1. 損益（Total Net Profit / Gross Profit / Gross Loss / Profit Factor / Expected Payoff / Recovery Factor）")
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = ms.total_net_profit(trades)
    gp = ms.gross_profit(trades)
    gl = ms.gross_loss(trades)
    pf = ms.profit_factor(trades)
    ep = ms.expected_payoff(trades)
    s.lines += [
        f"Total Net Profit = Σpnl（{len(pnls)} 件）= {_fmt(net)}",
        f"Gross Profit = Σ(pnl>0)（{len(wins)} 件）= {_fmt(gp)}",
        f"Gross Loss = Σ(pnl<0)（{len(losses)} 件）= {_fmt(gl)}",
        f"Profit Factor = Gross Profit ÷ |Gross Loss| = {_fmt(gp)} ÷ {_fmt(abs(gl))} = {_fmt(pf)}（Gross Loss = 0 なら ∞）",
        f"Expected Payoff = Total Net Profit ÷ 件数 = {_fmt(net)} ÷ {len(pnls)} = {_fmt(ep)}",
    ]
    has_equity = len(equity_curve) > 0
    if has_equity:
        eq_dd = mp.equity_dd_maximal(equity_curve, initial_deposit)
        rf = mp.recovery_factor_equity(trades, equity_curve, initial_deposit)
        s.lines.append(
            f"Recovery Factor = Total Net Profit ÷ Equity Drawdown Maximal = {_fmt(net)} ÷ {_fmt(eq_dd)} = {_fmt(rf)}"
            "（有効証拠金基準・符号つき・DD = 0 なら ∞）")
    else:
        rf = ms.recovery_factor(trades, balance_curve, initial_deposit)
        s.lines.append(f"Recovery Factor（有効証拠金の系列なし→残高基準）= {_fmt(rf)}")
    s.checks += [
        ("Total Net Profit", net, stats.profit), ("Gross Profit", gp, stats.gross_profit),
        ("Gross Loss", gl, stats.gross_loss), ("Profit Factor", pf, stats.profit_factor),
        ("Expected Payoff", ep, stats.expected_payoff), ("Recovery Factor", rf, stats.recovery_factor),
    ]
    sections.append(s)

    # --- 2. 件数 -----------------------------------------------------------------------
    s = StepSection("2. 件数（MT5 規則: 勝ち = pnl ≥ 0 / 負け = pnl < 0）")
    pt, lt = mp.profit_trades(trades), mp.loss_trades(trades)
    lg, sh = ms.long_trades(trades), ms.short_trades(trades)
    plg, psh = mp.profit_long_trades(trades), mp.profit_short_trades(trades)
    zero = sum(1 for p in pnls if p == 0)
    deals = mp.total_deals(trades)
    opened = sum(1 for t in trades if t.exit_reason != "partial")
    s.lines += [
        f"Total Trades = {len(trades)}",
        f"Profit Trades = #(pnl ≥ 0) = {pt}（うち pnl = 0 が {zero} 件）→ {pt}/{len(trades)}",
        f"Loss Trades = #(pnl < 0) = {lt}",
        f"Long Trades = #(買い) = {lg}・うち pnl ≥ 0 = {plg}",
        f"Short Trades = #(売り) = {sh}・うち pnl ≥ 0 = {psh}",
        f"Total Deals = 決済 out {len(trades)} + 建て in {opened}（部分決済以外で閉じた玉の数）= {deals}",
    ]
    s.checks += [
        ("Total Trades", len(trades), stats.trades), ("Profit Trades", pt, stats.profit_trades),
        ("Loss Trades", lt, stats.loss_trades), ("Long Trades", lg, stats.long_trades),
        ("Short Trades", sh, stats.short_trades), ("Long won", plg, stats.profit_long_trades),
        ("Short won", psh, stats.profit_short_trades), ("Total Deals", deals, stats.deals),
    ]
    sections.append(s)

    # --- 3. 個別トレード ----------------------------------------------------------------
    s = StepSection("3. 個別トレード（Largest / Average profit・loss trade）")
    lpt, llt = ms.largest_profit_trade(trades), ms.largest_loss_trade(trades)
    apt, alt = mp.average_profit_trade(trades), mp.average_loss_trade(trades)
    s.lines += [
        f"Largest profit trade = max(pnl) = {_fmt(lpt)}",
        f"Largest loss trade = min(pnl) = {_fmt(llt)}",
        f"Average profit trade = Gross Profit ÷ Profit Trades(≥0) = {_fmt(gp)} ÷ {pt} = {_fmt(apt)}",
        f"Average loss trade = Gross Loss ÷ Loss Trades(<0) = {_fmt(gl)} ÷ {lt} = {_fmt(alt)}",
    ]
    s.checks += [
        ("Largest profit trade", lpt, stats.max_profit_trade), ("Largest loss trade", llt, stats.max_loss_trade),
        ("Average profit trade", apt, stats.average_profit_trade), ("Average loss trade", alt, stats.average_loss_trade),
    ]
    sections.append(s)

    # --- 4. 連勝・連敗 ------------------------------------------------------------------
    s = StepSection("4. 連勝・連敗（ランの区切り: 勝ち = pnl > 0・pnl = 0 はランに属さず前後を区切る）")
    win_runs, loss_runs = ms._win_runs(trades), ms._loss_runs(trades)
    s.lines += [
        f"勝ちラン {len(win_runs)} 本（トレード計 {sum(len(r) for r in win_runs)} 件）・負けラン {len(loss_runs)} 本（計 {sum(len(r) for r in loss_runs)} 件）",
    ]
    vals = [
        ("Maximum consecutive wins (件数)", ms.max_consecutive_wins_count(trades), stats.max_con_wins, "最長の勝ちランの件数"),
        ("Maximum consecutive wins ($)", ms.max_consecutive_wins_profit(trades), stats.max_con_profit_trades, "最長の勝ちランの利益"),
        ("Maximum consecutive losses (件数)", ms.max_consecutive_losses_count(trades), stats.max_con_losses, "最長の負けランの件数"),
        ("Maximum consecutive losses ($)", ms.max_consecutive_losses_loss(trades), stats.max_con_loss_trades, "最長の負けランの損失"),
        ("Maximal consecutive profit ($)", ms.maximal_consecutive_profit_amount(trades), stats.con_profit_max, "利益が最大の勝ちランの利益"),
        ("Maximal consecutive profit (件数)", ms.maximal_consecutive_profit_count(trades), stats.con_profit_max_trades, "そのランの件数"),
        ("Maximal consecutive loss ($)", ms.maximal_consecutive_loss_amount(trades), stats.con_loss_max, "損失の絶対値が最大の負けランの損失"),
        ("Maximal consecutive loss (件数)", ms.maximal_consecutive_loss_count(trades), stats.con_loss_max_trades, "そのランの件数"),
        ("Average consecutive wins", ms.average_consecutive_wins(trades), stats.profit_trades_avg_con, "勝ちラン内の件数 ÷ 勝ちラン本数"),
        ("Average consecutive losses", ms.average_consecutive_losses(trades), stats.loss_trades_avg_con, "負けラン内の件数 ÷ 負けラン本数"),
    ]
    for name, v, st, how in vals:
        s.lines.append(f"{name} = {how} = {_fmt(v)}")
        s.checks.append((name, v, st))
    sections.append(s)

    # --- 5. ドローダウン ----------------------------------------------------------------
    s = StepSection("5. ドローダウン（系列の先頭に B0 を足し、各点で それまでの最大 − 現在）")
    for label, curve, prefix in (("残高（決済ごと）", balance_curve, "balance"), ("有効証拠金（評価点ごと）", equity_curve, "equity")):
        if not len(curve):
            s.lines.append(f"{label}: 系列なし")
            continue
        full = ms._full_balance(curve, initial_deposit)
        dd_abs, dd_pct = ms._dd_arrays(curve, initial_deposit)
        k_abs, k_pct = int(dd_abs.argmax()), int(dd_pct.argmax())
        peak = np.maximum.accumulate(full)
        s.lines += [
            f"[{label}] 点数 {len(full)}（B0 を含む）・最小 {_fmt(float(full.min()))}",
            f"  Absolute = B0 − 最小 = {_fmt(float(initial_deposit))} − {_fmt(float(full.min()))} = {_fmt(float(initial_deposit - full.min()))}",
            f"  Maximal = 金額 DD の最大 = 位置 {k_abs}（その時点の最大 {_fmt(float(peak[k_abs]))} − 値 {_fmt(float(full[k_abs]))}）"
            f" = {_fmt(float(dd_abs[k_abs]))}（その点の % = {_fmt(float(dd_pct[k_abs]))}）",
            f"  Relative = % DD の最大 = 位置 {k_pct}（{_fmt(float(dd_pct[k_pct]))}%・その点の金額 {_fmt(float(dd_abs[k_pct]))}）",
        ]
    s.checks += [
        ("Balance Drawdown Absolute", ms.balance_dd_absolute(balance_curve, initial_deposit), stats.balance_dd_abs),
        ("Balance Drawdown Maximal", ms.balance_dd_maximal(balance_curve, initial_deposit), stats.balance_dd),
        ("Balance Drawdown Maximal %", ms.balance_dd_maximal_percent(balance_curve, initial_deposit), stats.balance_dd_percent),
        ("Balance Drawdown Relative %", ms.balance_dd_relative_percent(balance_curve, initial_deposit), stats.balance_ddrel_percent),
        ("Balance Drawdown Relative", ms.balance_dd_relative_amount(balance_curve, initial_deposit), stats.balance_dd_relative),
    ]
    if has_equity:
        rel_pct, rel_amt = mp.equity_dd_relative(equity_curve, initial_deposit)
        s.checks += [
            ("Equity Drawdown Absolute", mp.equity_dd_absolute(equity_curve, initial_deposit), stats.equity_dd_abs),
            ("Equity Drawdown Maximal", mp.equity_dd_maximal(equity_curve, initial_deposit), stats.equity_dd_max),
            ("Equity Drawdown Maximal %", mp.equity_dd_maximal_percent(equity_curve, initial_deposit), stats.equity_dd_max_percent),
            ("Equity Drawdown Relative %", rel_pct, stats.equity_ddrel_percent),
            ("Equity Drawdown Relative", rel_amt, stats.equity_dd_relative),
        ]
    sections.append(s)

    # --- 6. AHPR / GHPR -----------------------------------------------------------------
    s = StepSection("6. AHPR / GHPR（HPR_i = B_i ÷ B_{i−1}・直前の残高が 0 以下の点は飛ばす）")
    hpr = ms._hpr_series(balance_curve, initial_deposit)
    neg = sum(1 for h in hpr if h <= 0)
    a, g = ms.ahpr(balance_curve, initial_deposit), ms.ghpr(balance_curve, initial_deposit)
    s.lines += [
        f"HPR の数 = {len(hpr)}（0 以下の比 = {neg} 個）",
        f"AHPR = 平均(HPR) = {_fmt(a)}",
        f"GHPR = (ΠHPR)^(1/{len(hpr)}) = {_fmt(g)}" + ("（0 以下の比を含むため分数乗が定義されない・ISSUE-546）" if neg else ""),
    ]
    s.checks += [("AHPR", a, stats.ahpr), ("GHPR", g, stats.ghpr)]
    sections.append(s)

    # --- 7. Sharpe Ratio ----------------------------------------------------------------
    s = StepSection("7. Sharpe Ratio（MT5 の定義・ISSUE-545: 足ごとの有効証拠金の対数収益・変化の無い足を除く・下限 −5）")
    sr = mp.sharpe_ratio_bar_equity(bar_open_equity, bar_seconds)
    e = np.asarray(bar_open_equity, dtype=float)
    if len(e) >= 2 and bar_seconds:
        prev, cur = e[:-1], e[1:]
        use = (cur != prev) & (prev > 0) & (cur > 0)
        r = np.log(cur[use] / prev[use]) if use.any() else np.array([])
        factor = math.sqrt(86400 / bar_seconds * 252)
        mean = float(r.mean()) if len(r) else 0.0
        sd = float(r.std()) if len(r) else 0.0
        raw = mean / sd * factor if sd else 0.0
        s.lines += [
            f"足 {len(e)} 本 → 隣り合う組 {len(prev)}・うち値が変わった組（両方 > 0）= {int(use.sum())}",
            f"対数収益 r = ln(E_i ÷ E_(i−1))・平均 = {_fmt(mean)}・母標準偏差 = {_fmt(sd)}",
            f"係数 = √(86400 ÷ {bar_seconds} × 252) = {_fmt(factor)}",
            f"平均 ÷ 標準偏差 × 係数 = {_fmt(raw)} → 下限 {mp.SHARPE_FLOOR} で切って {_fmt(sr)}",
        ]
    else:
        s.lines.append(f"足の系列が足りない（足 {len(e)} 本・足の秒数 {bar_seconds}）→ {_fmt(sr)}")
    s.checks.append(("Sharpe Ratio", sr, stats.sharpe_ratio))
    sections.append(s)

    # --- 8. Z-Score ---------------------------------------------------------------------
    s = StepSection("8. Z-Score（Wald-Wolfowitz・勝ち = pnl ≥ 0 の 2 値でランを数える）")
    w, n = mp.profit_trades(trades), len(trades)
    lo = n - w
    runs = mp._z_run_count(trades)
    p = 2 * w * lo
    z = mp.z_score(trades)
    s.lines += [
        f"W = {w}・L = {lo}・N = {n}・R（ラン数）= {runs}・P = 2WL = {p}",
        f"Z = (N×(R−0.5) − P) ÷ √(P×(P−N)÷(N−1)) = {_fmt(z)}（W か L が 0・分母 ≤ 0 なら 0）",
    ]
    s.checks.append(("Z-Score", z, stats.z_score))
    sections.append(s)

    # --- 9. 線形回帰 --------------------------------------------------------------------
    s = StepSection("9. LR Correlation / LR Standard Error（x = 決済番号 0..n・y = B0 を先頭に含む残高）")
    corr, se = mp.balance_linear_regression(balance_curve, initial_deposit)
    y = ms._full_balance(balance_curve, initial_deposit)
    s.lines += [
        f"点数 n = {len(y)}",
        f"LR Correlation = 相関(x, y) = {_fmt(corr)}",
        f"LR Standard Error = √(残差平方和 ÷ (n − 2)) = {_fmt(se)}",
    ]
    s.checks += [("LR Correlation", corr, stats.lr_correlation), ("LR Standard Error", se, stats.lr_standard_error)]
    sections.append(s)
    return sections


def render_metrics_steps(sections: "Sequence[StepSection]") -> str:
    """ログの本文（プレーンテキスト）を返す。末尾に照合の集計を置く。"""
    out: "list[str]" = ["# サマリー指標の計算ステップ", ""]
    total = mismatched = 0
    for sec in sections:
        out.append(f"## {sec.title}")
        out += [f"  {line}" for line in sec.lines]
        for name, got, st in sec.checks:
            total += 1
            ok = _same(got, st)
            mismatched += 0 if ok else 1
            out.append(f"  [{'一致' if ok else '不一致'}] {name}: ログ {_fmt(got)} / run の統計 {_fmt(st)}")
        out.append("")
    out.append(f"## 照合の集計: {total} 項目中 一致 {total - mismatched}・不一致 {mismatched}")
    return "\n".join(out) + "\n"
