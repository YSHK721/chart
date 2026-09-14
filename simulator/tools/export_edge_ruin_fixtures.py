"""export_edge_ruin_fixtures — JS 側検定用の golden fixture を出力する（ISSUE-368 スライス 1）。

目的:
    チャート UI 統合（ISSUE-368）で JS（`indigators/indicator_ui/web/js/domain/edge_ruin_core.js`）が
    実装する Step 1「エッジと破産確率」の数値検定に使う正解データを、権威
    （`simulator/usecase/edge_ruin.py`）から生成する。JS 側は本 JSON と一致することを
    `node --test` で検定する（`.doc/LAYERING_CONVENTIONS.md:28-30`＝権威 Python・JS は
    golden fixture 一致検定）。

出力: simulator/tests/fixtures/edge_ruin/js_golden_cases.json（追跡対象）

決定論:
    `edge_ruin.Mulberry32` は参照実装 HTML :686 の移植で、シードを固定すれば JS と bit 単位で
    一致する（`edge_ruin.py:47-70`）。したがって本生成器は乱数・実データに依存せず、
    いつ再生成しても同一の JSON を出す。

試行数の設計（実測根拠）:
    `SIMS=4000`（参照実装 :598 の既定）は 1 ケース約 15 秒（本機実測 2026-08-20・
    T=250。設計書は約 25 秒と記録）。格子全件をこの試行数で回すと生成にも鮮度検定にも
    分単位の時間がかかるため、**格子は sims=200・参照実装既定の 1 ケースのみ SIMS=4000**
    とする（設計書 出力 3 スライス 1）。

再生成: <venv python> simulator/tools/export_edge_ruin_fixtures.py

起動前提（ISSUE-479 Wave2 2-7 / ISSUE-482）: **venv の python で起動する**。import パスの
解決は台帳（tools/dev_paths.txt）が唯一源であり、venv へは `tools/install_dev_paths.py`
が書く .pth が届ける。本ファイルは実行時に sys.path を書き換えない（解決先が起動位置に
依存しなくなる・ISSUE-279）。起動できることは
`tools/tests/test_cli_entrypoints_resolve_without_pythonpath.py` が実測で固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

from simulator.usecase.edge_ruin import (
    SIMS, EdgeRuinSpec, solve_edge_ruin,
)

_REPO = Path(__file__).resolve().parents[2]

OUT = _REPO / "simulator" / "tests" / "fixtures" / "edge_ruin" / "js_golden_cases.json"

#: 格子の試行数（上記「試行数の設計」）
GRID_SIMS = 200

#: 格子（勝率 × 利益率 × 破産水準 × α × ホライズン）＝ 2×2×2×2×2 = 32 ケース
WIN_RATES = (0.38, 0.55)
PAYOFF_RATIOS = (2.74, 1.2)
RUIN_LEVELS = (0.5, 0.25)
ALPHAS = (0.01, 0.05)
HORIZONS = (50, 250)
#: 格子の口座分割数（参考カード (q/p)^N 専用・MC には影響しない）
GRID_SPLIT_COUNT = 20
#: 格子のシード
GRID_SEED = 1

#: 参照実装 :278-289 の既定値そのまま（唯一 SIMS=4000 で回すケース）
REFERENCE_DEFAULT = EdgeRuinSpec(
    win_rate=0.38, payoff_ratio=2.74, ruin_level=0.5, alpha=0.01,
    horizon=250, split_count=20, seed=1, sims=SIMS,
)

#: MC を伴わない性質（N の効き・EV≤0・別シード）を安く固定する小ケース群
EDGE_CASES = (
    EdgeRuinSpec(0.38, 2.74, 0.5, 0.01, 20, 1, 1, 50),      # N=1（(q/p)^1）
    EdgeRuinSpec(0.38, 2.74, 0.5, 0.01, 20, 40, 1, 50),     # N=40
    EdgeRuinSpec(0.30, 1.2, 0.5, 0.01, 20, 20, 1, 50),      # EV≤0（f* が負）
    EdgeRuinSpec(0.38, 2.74, 0.5, 0.01, 20, 20, 7, 50),     # 別シード（PRNG 同期の検出）
    EdgeRuinSpec(0.38, 2.74, 1.0, 0.01, 20, 20, 1, 50),     # 破産水準の上限 1.0
    EdgeRuinSpec(0.38, 2.74, 0.5, 0.0, 20, 20, 1, 50),      # α=0（制約 f が立たない）
)


def _case_id(spec: EdgeRuinSpec) -> str:
    return (f"p{spec.win_rate}/R{spec.payoff_ratio}/ruin{spec.ruin_level}"
            f"/a{spec.alpha}/T{spec.horizon}/N{spec.split_count}"
            f"/seed{spec.seed}/sims{spec.sims}")


def specs() -> "list[EdgeRuinSpec]":
    """fixture に載せる入力の全列挙（鮮度検定と共有する唯一源）。"""
    out = [REFERENCE_DEFAULT]
    for win_rate in WIN_RATES:
        for payoff_ratio in PAYOFF_RATIOS:
            for ruin_level in RUIN_LEVELS:
                for alpha in ALPHAS:
                    for horizon in HORIZONS:
                        out.append(EdgeRuinSpec(
                            win_rate=win_rate, payoff_ratio=payoff_ratio,
                            ruin_level=ruin_level, alpha=alpha, horizon=horizon,
                            split_count=GRID_SPLIT_COUNT, seed=GRID_SEED, sims=GRID_SIMS,
                        ))
    out.extend(EDGE_CASES)
    return out


def case_payload(spec: EdgeRuinSpec) -> dict:
    """1 ケース分の入力＋権威の出力（JS が突き合わせる全項目）。"""
    result = solve_edge_ruin(spec)
    return {
        "id": _case_id(spec),
        "spec": {
            "win_rate": spec.win_rate,
            "payoff_ratio": spec.payoff_ratio,
            "ruin_level": spec.ruin_level,
            "alpha": spec.alpha,
            "horizon": spec.horizon,
            "split_count": spec.split_count,
            "seed": spec.seed,
            "sims": spec.sims,
        },
        "expected": {
            "loss_rate": result.loss_rate,
            "expected_value": result.expected_value,
            "kelly_fraction": result.kelly_fraction,
            "half_kelly_fraction": result.half_kelly_fraction,
            "constrained_fraction": result.constrained_fraction,
            "ror_at_constrained": result.ror_at_constrained,
            "ror_at_kelly": result.ror_at_kelly,
            "growth_at_kelly": result.growth_at_kelly,
            "growth_at_constrained": result.growth_at_constrained,
            "equal_bet_ruin_reference": result.equal_bet_ruin_reference,
            "f_max": result.f_max,
            "ror_curve": [[f, ror] for f, ror in result.ror_curve],
            "growth_curve": [[f, g] for f, g in result.growth_curve],
        },
    }


def build_payload() -> dict:
    return {
        "authority": "simulator/usecase/edge_ruin.py（solve_edge_ruin・Mulberry32）",
        "source_spec": "integrated_position_sizing_calculator.html Step 1（:582-583, :598-605, :624-644, :686）",
        "tolerance": {
            "closed_form": "厳密一致（許容 0）: expected_value / kelly_fraction /"
                           " half_kelly_fraction / equal_bet_ruin_reference / f_max /"
                           " ror_curve の f 座標",
            "ror": "厳密一致（許容 0）: 同一 PRNG・同一消費順のため bit 一致する",
            "growth": "相対 1e-15: math.log と V8 Math.log に 1 ULP 差の実測がある"
                      "（test_edge_ruin.py:23-26）",
        },
        "cases": [case_payload(spec) for spec in specs()],
    }


def main() -> None:
    payload = build_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(payload['cases'])} ケース → {OUT}")


if __name__ == "__main__":
    main()
