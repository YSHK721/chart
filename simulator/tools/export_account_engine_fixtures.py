"""export_account_engine_fixtures — JS 側検定用の golden fixture を出力する（ISSUE-369 Phase 2）。

目的:
    チャート UI 統合（ISSUE-368）で JS（domain/position_sizing_plan.js 予定）が実装する
    証拠金・ロスカット計算の数値検定に使う正解データを、権威（simulator/usecase/
    account_engine.py の閉形式）から生成する。JS 側は本 JSON と一致することを node --test で
    検定する（LAYERING_CONVENTIONS: 権威 Python・JS は golden fixture 一致検定）。

出力: simulator/tests/fixtures/account_engine/js_golden_cases.json（追跡対象）
    ケース＝方向 × 建玉構成（単一/分割）× 残高 × 証拠金率 の格子。各ケースに
    required_margin / losscut_price / losscut_distance / margin_use を記録。

再生成: <venv python> simulator/tools/export_account_engine_fixtures.py
（決定論＝いつ再生成しても同一。乱数・実データ非依存）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from simulator.usecase.account_engine import (  # noqa: E402
    official_losscut_price, official_required_margin,
)

OUT = _REPO / "simulator" / "tests" / "fixtures" / "account_engine" / "js_golden_cases.json"

#: 建玉構成の格子（(price, units) の列）。単一・等量分割・不等量難平（線形重み相当）。
ENTRY_SETS = {
    "single": [(58700.0, 18.0)],
    "single_small": [(65516.5, 3.0)],
    "split_equal": [(58700.0, 6.0), (58500.0, 6.0), (58300.0, 6.0)],
    "split_linear_nanpin": [(65516.5, 6.0), (65300.0, 8.0), (65000.0, 10.0)],
    "split_two": [(60000.5, 10.0), (59000.0, 8.0)],
}
BALANCES = [172000.0, 500000.0, 1000000.0]
MARGIN_RATES = [0.10, 0.20]
DIRECTIONS = ["long", "short"]


def main() -> None:
    cases = []
    for set_name, entries in ENTRY_SETS.items():
        total_units = sum(u for _, u in entries)
        avg_p = sum(p * u for p, u in entries) / total_units
        for balance in BALANCES:
            for mr in MARGIN_RATES:
                req = official_required_margin(entries, mr)
                for direction in DIRECTIONS:
                    x = official_losscut_price(direction, entries, balance, mr)
                    cases.append({
                        "id": f"{set_name}/{direction}/E{int(balance)}/mr{int(mr * 100)}",
                        "direction": direction,
                        "entries": [{"price": p, "units": u} for p, u in entries],
                        "balance": balance,
                        "margin_rate": mr,
                        "point_value": 1.0,
                        "expected": {
                            "total_units": total_units,
                            "avg_price": avg_p,
                            "required_margin": req,
                            "margin_use": req / balance,
                            "losscut_price": x,
                            "losscut_distance": abs(avg_p - x),
                        },
                    })
    payload = {
        "authority": "simulator/usecase/account_engine.py（official_* 閉形式）",
        "source_spec": "docs/oanda_indices_cfd_about.md §3(2)/§1-2（必要証拠金=約定代金×証拠金率・維持率100%でロスカット）",
        "formula": {
            "required_margin": "sum(units_i * price_i) * point_value * margin_rate",
            "losscut_price_long": "avg_price * (1 + margin_rate) - balance / (total_units * point_value)",
            "losscut_price_short": "avg_price * (1 - margin_rate) + balance / (total_units * point_value)",
        },
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cases)} ケース → {OUT}")


if __name__ == "__main__":
    main()
