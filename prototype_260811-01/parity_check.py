"""parity_check — 両アクター（OANDA エンジン vs simulator 口座アクター）の実測突合（Phase 2）。

目的:
    同一 tick 列・同一発注に対して、
      (a) prototype の AccountEngine（OANDA 規約・margin_basis="entry"）
      (b) simulator の口座アクター（domain/account.py の Account ＋ usecase/_execution の
          約定規約 ＋ run_backtest と同じ margin 加減算・stop_out 判定）
    を並走させ、tick 粒度で equity・必要証拠金・維持率・強制決済の発動と結末を比較する。

写像（JP225 CFD ↔ simulator の MT5 語彙）:
    contract_size = 1（1 単位 1pt = 1 円 = V）
    leverage      = 10（必要証拠金率 10% = 1/leverage）
    stop_out_level = 100（維持率 100%）
    評価価格: buy 保有 = bid ／ sell 保有 = ask（両者同一規約）

既知の仕様差（突合で数値確認する対象）:
    D1) 発動境界: エンジンは維持率 <= 100%（公式「100%以下」）／simulator は
        margin_level < stop_out_level（run_backtest.py:253,374 の実装）。
    D2) 強制決済の範囲: エンジンは損失最大の建玉から順次・維持率回復で停止（公式 §1-2）／
        simulator の close_and_halt は全建玉決済（MT5 規約）。単一建玉では一致し、
        複数建玉では「1 本で回復するケース」に限り結末が分岐するはず。

実行:
    MARKETDATA_DATA_DIR=... <venv python> parity_check.py
出力: stdout の表 + out/parity_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from simulator.domain.account import Account          # noqa: E402  口座アクター（simulator）
from simulator.domain.order import Order              # noqa: E402
from simulator.usecase._execution import (            # noqa: E402  約定価格規約（simulator）
    close_price_for, fill_market_order,
)

from simulator.usecase.account_engine import (        # noqa: E402  口座アクター（OANDA エンジン）
    LONG, AccountConfig, AccountEngine, EntryOrder, OrderPlan,
)
from simulator.tools.run_account_scenario import iter_ticks  # noqa: E402

CONTRACT_SIZE = 1.0
LEVERAGE = 10.0
STOP_OUT_LEVEL = 100.0

RESULTS: list[dict] = []


def run_simulator_actor(direction: str, entries: list[EntryOrder], ticks, balance: float):
    """simulator の口座アクター＋約定規約で最小ループを回す。

    fill・評価・margin 加減算・stop_out 判定は run_backtest.py と同じ関数・同じ式のみを
    使う（自前の証拠金数式を書かない＝突合対象は simulator 実装そのもの）。
    stop_out 時は close_and_halt（全建玉を決済価格規約で決済・以後停止）。
    """
    side = "buy" if direction == LONG else "sell"
    account = Account(balance=balance, contract_size=CONTRACT_SIZE)
    pending = list(entries)
    series = {"ts": [], "equity": [], "margin": [], "level": []}
    trigger = None
    for ts, bid, ask in ticks:
        # 約定（成行=fill_market_order／指値=有利側到達で指値価格。engine と同じ判定）
        still = []
        for e in pending:
            if e.price is None:
                pos = fill_market_order(Order(side=side, kind="market", volume=e.units, price=None),
                                        bid=bid, ask=ask)
            elif (direction == LONG and ask <= e.price) or (direction != LONG and bid >= e.price):
                pos = fill_market_order(Order(side=side, kind="market", volume=e.units, price=None),
                                        bid=e.price, ask=e.price)
            else:
                still.append(e)
                continue
            account.open_positions.append(pos)
            account.margin += pos.required_margin(LEVERAGE, CONTRACT_SIZE)   # run_backtest.py:325
        pending = still

        account.update_floating_pnl_at(bid=bid, ask=ask)
        level = account.margin_level()
        series["ts"].append(ts)
        series["equity"].append(account.equity)
        series["margin"].append(account.margin)
        series["level"].append(None if level == float("inf") else level)

        if account.open_positions and level < STOP_OUT_LEVEL:   # run_backtest.py:253/374（D1: 厳密未満）
            px = close_price_for(side, bid=bid, ask=ask)
            for pos in list(account.open_positions):
                account.balance += pos.floating_pnl(px, CONTRACT_SIZE)
                account.margin -= pos.required_margin(LEVERAGE, CONTRACT_SIZE)  # run_backtest.py:151
            account.open_positions.clear()
            account.update_floating_pnl_at(bid=bid, ask=ask)
            trigger = {"ts": ts, "price": px}
            break                                              # close_and_halt（D2: 全決済・停止）
    return account, series, trigger


def compare(name: str, direction: str, entries: list[EntryOrder], start: str, end: str,
            balance: float = 172000.0) -> None:
    print(f"── {name}")
    plan = OrderPlan(direction=direction, entries=entries)
    engine = AccountEngine(plan, AccountConfig(balance=balance))
    r = engine.run(iter_ticks(start, end))
    sim_account, sim_series, sim_trigger = run_simulator_actor(
        direction, entries, iter_ticks(start, end), balance)

    # 共通保有区間（両者とも建玉あり＝維持率が数値）で系列差の最大値を測る
    n = min(len(r.series.ts), len(sim_series["ts"]))
    dmax = {"equity": 0.0, "margin": 0.0, "level": 0.0}
    cmp_n = 0
    for i in range(n):
        if r.series.ts[i] != sim_series["ts"][i]:
            break
        er, sr = r.series.margin_ratio[i], sim_series["level"][i]
        if er is None or sr is None:
            continue
        cmp_n += 1
        dmax["equity"] = max(dmax["equity"], abs(r.series.equity[i] - sim_series["equity"][i]))
        dmax["margin"] = max(dmax["margin"], abs(r.series.required_margin[i] - sim_series["margin"][i]))
        dmax["level"] = max(dmax["level"], abs(er * 100.0 - sr))

    eng_lc = [e for e in r.events if e.kind == "losscut"]
    eng_trigger = {"ts": eng_lc[0].ts, "price": eng_lc[0].price} if eng_lc else None
    row = {
        "scenario": name,
        "compared_ticks": cmp_n,
        "max_diff_equity_yen": dmax["equity"],
        "max_diff_margin_yen": dmax["margin"],
        "max_diff_level_pct": dmax["level"],
        "engine_trigger": eng_trigger,
        "simulator_trigger": sim_trigger,
        "engine_final_balance": r.final_balance,
        "simulator_final_balance": sim_account.balance,
        "engine_losscut_closed_units": sum(e.units for e in eng_lc),
    }
    RESULTS.append(row)
    print(f"   比較 tick 数 {cmp_n:,} / 最大差: equity ¥{dmax['equity']:.6f} / "
          f"必要証拠金 ¥{dmax['margin']:.6f} / 維持率 {dmax['level']:.9f}pt")
    if eng_trigger or sim_trigger:
        e_ts = eng_trigger and eng_trigger["ts"]
        s_ts = sim_trigger and sim_trigger["ts"]
        same_tick = e_ts == s_ts
        print(f"   強制決済: engine={eng_trigger} / simulator={sim_trigger} "
              f"→ {'同一 tick・同一価格' if same_tick and abs(eng_trigger['price'] - sim_trigger['price']) < 1e-9 else '相違（下記参照）'}")
    print(f"   最終残高: engine ¥{r.final_balance:,.2f} / simulator ¥{sim_account.balance:,.2f} "
          f"/ 差 ¥{r.final_balance - sim_account.balance:+,.2f}")


def main() -> None:
    print("=" * 72)
    print("parity_check — OANDA エンジン vs simulator 口座アクター（同一 tick 実測）")
    print("=" * 72)
    # 1) 単一建玉ロング → ロスカット（D1 の境界・D2 は単一なので効かない）
    compare("単一ロング 25u → ロスカット（08-06）", LONG, [EntryOrder(units=25.0)],
            "2026-08-06", "2026-08-06")
    # 2) 単一建玉ショート → ロスカット（08-10 上昇日）
    compare("単一ショート 25u → ロスカット（08-10）", "short", [EntryOrder(units=25.0)],
            "2026-08-10", "2026-08-10")
    # 3) 複数建玉（難平 3 本・stop なし・E=164,000）→ ロスカット（D2 の分岐を実測）。
    #    E は「1 本の決済で維持率が回復する」水準に設定（公式 §1-2 の順次決済と MT5 の
    #    close_and_halt が異なる結末を出すはずのケース）。
    compare("難平ロング 6+8+10u E=164,000 → ロスカット（08-06〜07）", LONG,
            [EntryOrder(units=6.0), EntryOrder(units=8.0, price=65300.0),
             EntryOrder(units=10.0, price=65000.0)],
            "2026-08-06", "2026-08-07", balance=164000.0)
    out = _HERE / "out" / "parity_results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print("-" * 72)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
