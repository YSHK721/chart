"""verify — 現行計算機 HTML の静的式と、エンジンの tick 実測を数値で突き合わせる（ISSUE-369）。

検定項目:
    V1. 代数同値: HTML の lcDistCore×mFactor 形式と閉形式 X=(avgP∓E/U)/(1∓mr) が一致する
        （グリッド数値検査。HTML 式の写しは account_engine.html_losscut_price）。
    V2. ロング・ロスカット実測: 実 tick でロスカットを発生させ、トリガー tick の評価価格が
        閉形式 X と一致するか（tick 粒度のギャップを差として報告）。
    V3. ショート・ロスカット実測: 同上（上昇日・ask 評価）。
    V4. mark_price_mode の感度（U1）: mid / trade-side でトリガー価格がどれだけ動くか。
    V5. 必要証拠金: HTML 式（建値ベース）とエンジン実測（時価ベース）の約定 tick 時点差と
        保有中の変動幅。

実行:
    MARKETDATA_DATA_DIR=... <venv python> verify.py
出力: stdout の表 + out/verify_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from account_engine import (  # noqa: E402
    LONG, SHORT, AccountConfig, AccountEngine, EntryOrder, OrderPlan,
    html_losscut_price, html_required_margin,
)
from run_scenario import iter_ticks  # noqa: E402

RESULTS: list[dict] = []


def record(check: str, expected: float, measured: float, tol: float, note: str = "") -> bool:
    ok = abs(measured - expected) <= tol
    RESULTS.append({"check": check, "expected": expected, "measured": measured,
                    "diff": measured - expected, "tol": tol, "ok": ok, "note": note})
    mark = "OK " if ok else "NG "
    print(f"  [{mark}] {check}: 期待 {expected:,.2f} / 実測 {measured:,.2f} "
          f"/ 差 {measured - expected:+,.2f}（許容 {tol:,.2f}）{note and ' — ' + note}")
    return ok


def v1_algebraic_identity() -> None:
    """V1: HTML の 2 段式（lcDistCore×mFactor）と閉形式の同値性（数値グリッド）。"""
    print("V1. 代数同値（HTML lcDistCore×mFactor ⇔ 閉形式）")
    worst = 0.0
    for avg_p in (30000.0, 58700.0, 66000.0):
        for e in (100000.0, 172000.0, 1000000.0):
            for units in (5.0, 25.0, 120.0):
                for mr in (0.05, 0.10, 0.20):
                    u = units  # V=1
                    req = avg_p * units * mr           # HTML: reqMargin（建値ベース）
                    # HTML build() の 2 段式（逐語）
                    lc_core = (e - req) / u
                    for direction, mfac in ((LONG, 1 / (1 - mr)), (SHORT, 1 / (1 + mr))):
                        # HTML build(): lcDist = lcDistCore × mFactor、
                        #   lcPrice = ロング avgP − lcDist ／ ショート avgP + lcDist
                        lc_dist = lc_core * mfac
                        lc_html2 = avg_p - lc_dist if direction == LONG else avg_p + lc_dist
                        closed = html_losscut_price(direction, [(avg_p, units)], e, mr)
                        worst = max(worst, abs(lc_html2 - closed))
    ok = worst <= 1e-6
    print(f"  [{'OK ' if ok else 'NG '}] 全 216 組で最大差 {worst:.3e}"
          f"（{'浮動小数の丸めのみ＝同値' if ok else '同値でない＝式の写しを再確認'}）")
    RESULTS.append({"check": "V1 代数同値", "expected": 0.0, "measured": worst,
                    "diff": worst, "tol": 1e-6, "ok": ok, "note": "グリッド 216 組"})


def _first_losscut(plan: OrderPlan, cfg: AccountConfig, start: str, end: str):
    """エンジンを回し、最初の losscut イベントと約定済み建玉（発生直前）を返す。"""
    engine = AccountEngine(plan, cfg)
    result = engine.run(iter_ticks(start, end))
    entries = [(e.price, e.units) for e in result.events if e.kind == "entry"]
    lc = next((e for e in result.events if e.kind == "losscut"), None)
    return result, entries, lc


def v2_long_losscut() -> None:
    print("V2. ロング・ロスカット実測（2026-08-06・25 単位・E=172,000）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=25.0)])
    cfg = AccountConfig(balance=172000.0)
    result, entries, lc = _first_losscut(plan, cfg, "2026-08-06", "2026-08-06")
    assert lc is not None, "ロスカット未発生（シナリオ設計エラー）"
    x = html_losscut_price(LONG, entries, cfg.balance, cfg.margin_rate)
    # 許容: トリガーは「評価価格が X を割った最初の tick」なので、tick 間ギャップ分だけ
    #   X より下で約定しうる（上には行かない）。許容は観測ギャップ相当の 5pt。
    record("ロスカット価格（bid 実測 vs 閉形式 X）", x, lc.price, 5.0,
           note="実測は X を最初に割った tick の bid（下方向のみ乖離しうる）")
    if lc.price > x + 1e-9:
        print("  [NG ] 実測が X より上＝式が過大予測（モデル不一致）")


def v3_short_losscut() -> None:
    print("V3. ショート・ロスカット実測（2026-08-10・25 単位・E=172,000）")
    plan = OrderPlan(direction=SHORT, entries=[EntryOrder(units=25.0)])
    cfg = AccountConfig(balance=172000.0)
    result, entries, lc = _first_losscut(plan, cfg, "2026-08-10", "2026-08-10")
    assert lc is not None, "ロスカット未発生（シナリオ設計エラー）"
    x = html_losscut_price(SHORT, entries, cfg.balance, cfg.margin_rate)
    record("ロスカット価格（ask 実測 vs 閉形式 X）", x, lc.price, 5.0,
           note="ショートは X を最初に超えた tick の ask（上方向のみ乖離しうる）")


def v4_mark_mode_sensitivity() -> None:
    print("V4. mark_price_mode 感度（U1・ロング 2026-08-06）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=25.0)])
    prices = {}
    for mode in ("mid", "trade-side"):
        cfg = AccountConfig(balance=172000.0, mark_price_mode=mode)
        _, _, lc = _first_losscut(plan, cfg, "2026-08-06", "2026-08-06")
        prices[mode] = lc.price if lc else None
        print(f"  mark={mode:<10} → トリガー {lc.price:,.1f}" if lc else f"  mark={mode}: 未発生")
    if all(v is not None for v in prices.values()):
        d = abs(prices["mid"] - prices["trade-side"])
        print(f"  [情報] mid と trade-side のトリガー差 = {d:,.1f}pt（スプレッド起源・U1 の実測感度）")
        RESULTS.append({"check": "V4 mark_price_mode 感度", "expected": 0.0,
                        "measured": d, "diff": d, "tol": float("inf"), "ok": True,
                        "note": "情報項目（合否なし）"})


def v5_required_margin() -> None:
    print("V5. 必要証拠金（HTML 建値ベース vs エンジン時価ベース・ロング 2026-08-06）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=20.0)],
                     stop_price=65100.0)
    cfg = AccountConfig(balance=172000.0)
    engine = AccountEngine(plan, cfg)
    result = engine.run(iter_ticks("2026-08-06", "2026-08-06"))
    entries = [(e.price, e.units) for e in result.events if e.kind == "entry"]
    html_req = html_required_margin(entries, cfg.margin_rate)
    # 約定 tick 時点のエンジン実測（時価≒建値なので一致するはず・スプレッド半分の差）
    first_req = next(r for r in result.series.required_margin if r > 0)
    record("約定 tick の必要証拠金（HTML 式 vs 実測）", html_req, first_req,
           tol=html_req * 0.001, note="差はスプレッド（mid−ask）起源")
    holding = [r for r in result.series.required_margin if r > 0]
    lo, hi = min(holding), max(holding)
    print(f"  [情報] 保有中の必要証拠金 実測変動幅: ¥{lo:,.0f} 〜 ¥{hi:,.0f}"
          f"（{(hi - lo) / html_req * 100:.1f}% 変動）＝建値固定の HTML 式は発注時スナップショット")
    RESULTS.append({"check": "V5 保有中変動幅", "expected": html_req, "measured": hi,
                    "diff": hi - lo, "tol": float("inf"), "ok": True,
                    "note": f"時価ベースで {((hi - lo) / html_req * 100):.1f}% 変動（情報項目）"})


def main() -> None:
    print("=" * 72)
    print("verify — 現行 HTML 式 vs エンジン tick 実測（ISSUE-369）")
    print("=" * 72)
    v1_algebraic_identity()
    v2_long_losscut()
    v3_short_losscut()
    v4_mark_mode_sensitivity()
    v5_required_margin()
    out = _HERE / "out" / "verify_results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    ng = [r for r in RESULTS if not r["ok"]]
    print("-" * 72)
    print(f"結果: {len(RESULTS)} 検定中 NG {len(ng)} 件 → {out}")
    if ng:
        sys.exit(1)


if __name__ == "__main__":
    main()
