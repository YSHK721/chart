"""verify — 正解式（OANDA 公式）とエンジンの tick 実測を数値で突き合わせる（ISSUE-369）。

採点の基準（正解）は docs/oanda_indices_cfd_about.md 由来の公式式
（account_engine.official_required_margin / official_losscut_price）。
修正前の計算機の式（superseded_mark_based_losscut_price）は正解ではなく、
ISSUE-370 の差の記録のためだけに最後で参照する。

検定項目（公式が先・修正前の式は最後）:
    V1. 【公式】ロスカット実測（margin_basis="entry"・既定）: 公式閉形式
        X = avgP(1+mr) − E/U（ロング）/ avgP(1−mr) + E/U（ショート）と実測の一致。
    V2. 【公式】必要証拠金: 公式式（約定代金固定・§3(2)）と約定 tick 実測の一致。
    V3. mark 基準（比較用）の内部整合・ロング: 時価連動モデル自体のシミュレーション＝閉形式。
    V4. mark 基準（比較用）の内部整合・ショート: 同上（上昇日・ask 評価）。
    V5. mark_price_mode の感度（U1）: mid / trade-side でトリガー価格がどれだけ動くか。
    V6. 【記録】修正前の式の代数同値: lcDistCore×mFactor 形式と X=(avgP∓E/U)/(1∓mr) の
        同値性（誤りは実装ではなく前提だったことの記録）。
    V7. 【記録】公式式と修正前の式の差の定量化（ISSUE-370 の根拠数値）。

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

from simulator.usecase.account_engine import (  # noqa: E402
    LONG, SHORT, AccountConfig, AccountEngine, EntryOrder, OrderPlan,
    official_losscut_price, official_required_margin,
    superseded_mark_based_losscut_price,
)
from simulator.tools.run_account_scenario import iter_ticks  # noqa: E402

RESULTS: list[dict] = []


def record(check: str, expected: float, measured: float, tol: float, note: str = "") -> bool:
    ok = abs(measured - expected) <= tol
    RESULTS.append({"check": check, "expected": expected, "measured": measured,
                    "diff": measured - expected, "tol": tol, "ok": ok, "note": note})
    mark = "OK " if ok else "NG "
    print(f"  [{mark}] {check}: 期待 {expected:,.2f} / 実測 {measured:,.2f} "
          f"/ 差 {measured - expected:+,.2f}（許容 {tol:,.2f}）{note and ' — ' + note}")
    return ok


def _first_losscut(plan: OrderPlan, cfg: AccountConfig, start: str, end: str):
    """エンジンを回し、最初の losscut イベントと約定済み建玉（発生直前）を返す。"""
    engine = AccountEngine(plan, cfg)
    result = engine.run(iter_ticks(start, end))
    entries = [(e.price, e.units) for e in result.events if e.kind == "entry"]
    lc = next((e for e in result.events if e.kind == "losscut"), None)
    return result, entries, lc


def v1_official_losscut() -> None:
    """V1:【公式】ロスカット価格。正解式（official_losscut_price）vs 実 tick 実測。"""
    print("V1.【公式】ロスカット実測（entry 基準＝約定代金固定・§3(2)）")
    for direction, day, side in ((LONG, "2026-08-06", "bid"), (SHORT, "2026-08-10", "ask")):
        plan = OrderPlan(direction=direction, entries=[EntryOrder(units=25.0)])
        cfg = AccountConfig(balance=172000.0)   # 既定 margin_basis="entry"
        assert cfg.margin_basis == "entry"
        _, entries, lc = _first_losscut(plan, cfg, day, day)
        assert lc is not None, f"ロスカット未発生（{direction}・シナリオ設計エラー）"
        x = official_losscut_price(direction, entries, cfg.balance, cfg.margin_rate)
        record(f"公式ロスカット価格（{direction}・{side} 実測 vs 閉形式）", x, lc.price, 5.0,
               note="X を最初に跨いだ tick の評価価格（乖離は tick 間ギャップのみ）")


def v2_official_required_margin() -> None:
    """V2:【公式】必要証拠金。正解式（official_required_margin）vs 実 tick 実測。"""
    print("V2.【公式】必要証拠金（約定代金固定・§3(2)・ロング 2026-08-06）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=20.0)], stop_price=65100.0)
    cfg = AccountConfig(balance=172000.0)       # 既定 margin_basis="entry"
    engine = AccountEngine(plan, cfg)
    result = engine.run(iter_ticks("2026-08-06", "2026-08-06"))
    entries = [(e.price, e.units) for e in result.events if e.kind == "entry"]
    req_official = official_required_margin(entries, cfg.margin_rate)
    holding = [r for r in result.series.required_margin if r > 0]
    record("約定 tick の必要証拠金（公式式 vs 実測）", req_official, holding[0], 1e-6,
           note="約定代金固定＝完全一致するはず")
    record("保有中の必要証拠金の変動（公式＝固定なので 0）", 0.0, max(holding) - min(holding), 1e-6,
           note="公式仕様では保有中一定")


def v3_mark_internal_long() -> None:
    """V3: mark 基準（比較用）の内部整合・ロング。時価連動モデル自体のシム＝閉形式一致。"""
    print("V3. mark 基準の内部整合・ロング（比較用・2026-08-06・25 単位・E=172,000）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=25.0)])
    cfg = AccountConfig(balance=172000.0, margin_basis="mark")
    result, entries, lc = _first_losscut(plan, cfg, "2026-08-06", "2026-08-06")
    assert lc is not None, "ロスカット未発生（シナリオ設計エラー）"
    x = superseded_mark_based_losscut_price(LONG, entries, cfg.balance, cfg.margin_rate)
    record("mark 基準ロスカット価格（bid 実測 vs 閉形式）", x, lc.price, 5.0,
           note="時価連動モデルの自己整合のみ（公式仕様ではない）")


def v4_mark_internal_short() -> None:
    """V4: mark 基準（比較用）の内部整合・ショート。"""
    print("V4. mark 基準の内部整合・ショート（比較用・2026-08-10・25 単位・E=172,000）")
    plan = OrderPlan(direction=SHORT, entries=[EntryOrder(units=25.0)])
    cfg = AccountConfig(balance=172000.0, margin_basis="mark")
    result, entries, lc = _first_losscut(plan, cfg, "2026-08-10", "2026-08-10")
    assert lc is not None, "ロスカット未発生（シナリオ設計エラー）"
    x = superseded_mark_based_losscut_price(SHORT, entries, cfg.balance, cfg.margin_rate)
    record("mark 基準ロスカット価格（ask 実測 vs 閉形式）", x, lc.price, 5.0,
           note="時価連動モデルの自己整合のみ（公式仕様ではない）")


def v5_mark_mode_sensitivity() -> None:
    """V5: mark 基準における時価の mid/trade-side 解釈差（U1）。"""
    print("V5. mark_price_mode 感度（U1・mark 基準・ロング 2026-08-06）")
    plan = OrderPlan(direction=LONG, entries=[EntryOrder(units=25.0)])
    prices = {}
    for mode in ("mid", "trade-side"):
        cfg = AccountConfig(balance=172000.0, margin_basis="mark", mark_price_mode=mode)
        _, _, lc = _first_losscut(plan, cfg, "2026-08-06", "2026-08-06")
        prices[mode] = lc.price if lc else None
        print(f"  mark={mode:<10} → トリガー {lc.price:,.1f}" if lc else f"  mark={mode}: 未発生")
    if all(v is not None for v in prices.values()):
        d = abs(prices["mid"] - prices["trade-side"])
        print(f"  [情報] mid と trade-side のトリガー差 = {d:,.1f}pt（スプレッド起源・U1 の実測感度）")
        RESULTS.append({"check": "V5 mark_price_mode 感度", "expected": 0.0,
                        "measured": d, "diff": d, "tol": float("inf"), "ok": True,
                        "note": "情報項目（合否なし）"})


def v6_superseded_identity() -> None:
    """V6:【記録】修正前の式（lcDistCore×mFactor）とその閉形式の同値性。

    誤りは「実装」ではなく「時価連動という前提」だったことの記録（ISSUE-370）。
    """
    print("V6.【記録】修正前の式の代数同値（lcDistCore×mFactor ⇔ 閉形式）")
    worst = 0.0
    for avg_p in (30000.0, 58700.0, 66000.0):
        for e in (100000.0, 172000.0, 1000000.0):
            for units in (5.0, 25.0, 120.0):
                for mr in (0.05, 0.10, 0.20):
                    u = units  # V=1
                    req = avg_p * units * mr
                    lc_core = (e - req) / u
                    for direction, mfac in ((LONG, 1 / (1 - mr)), (SHORT, 1 / (1 + mr))):
                        lc_dist = lc_core * mfac
                        lc_2step = avg_p - lc_dist if direction == LONG else avg_p + lc_dist
                        closed = superseded_mark_based_losscut_price(
                            direction, [(avg_p, units)], e, mr)
                        worst = max(worst, abs(lc_2step - closed))
    ok = worst <= 1e-6
    print(f"  [{'OK ' if ok else 'NG '}] 全 216 組で最大差 {worst:.3e}"
          f"（{'丸めのみ＝同値' if ok else '同値でない＝写しを再確認'}）")
    RESULTS.append({"check": "V6 修正前式の代数同値", "expected": 0.0, "measured": worst,
                    "diff": worst, "tol": 1e-6, "ok": ok, "note": "グリッド 216 組"})


def v7_official_vs_superseded() -> None:
    """V7:【記録】正解式と修正前の式の差の定量化（ISSUE-370 の根拠数値）。"""
    print("V7.【記録】公式式 vs 修正前の式（同一条件のロスカット価格差）")
    cases = [(LONG, 65516.5), (SHORT, 66165.0)]
    for direction, avg_p in cases:
        entries = [(avg_p, 25.0)]
        x_official = official_losscut_price(direction, entries, 172000.0, 0.10)
        x_old = superseded_mark_based_losscut_price(direction, entries, 172000.0, 0.10)
        d = x_official - x_old
        danger = (direction == LONG and x_official > x_old) or \
                 (direction == SHORT and x_official < x_old)
        print(f"  {direction:<5}: 公式 {x_official:,.1f} / 修正前 {x_old:,.1f} / 差 {d:+,.1f}pt"
              f" → {'修正前は危険側（実際より遠く表示）' if danger else '修正前は保守側'}")
        RESULTS.append({"check": f"V7 式差（{direction}）", "expected": x_official,
                        "measured": x_old, "diff": d, "tol": float("inf"), "ok": True,
                        "note": "情報項目（ISSUE-370 の根拠・公式が正）"})


def main() -> None:
    print("=" * 72)
    print("verify — 正解式（OANDA 公式）vs エンジン tick 実測（ISSUE-369）")
    print("=" * 72)
    v1_official_losscut()
    v2_official_required_margin()
    v3_mark_internal_long()
    v4_mark_internal_short()
    v5_mark_mode_sensitivity()
    v6_superseded_identity()
    v7_official_vs_superseded()
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
