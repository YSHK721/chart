"""export_split_entry_fixtures — JS 側検定用の golden fixture を出力する（ISSUE-368 スライス 1）。

目的:
    チャート UI 統合（ISSUE-368）で JS（`indigators/indicator_ui/web/js/domain/split_entry_plan.js`）が
    実装する Step 3「分割エントリー — f をロットに変換」の数値検定に使う正解データを、権威
    （`simulator/usecase/split_entry_plan.py`）から生成する。JS 側は本 JSON と一致することを
    `node --test` で検定する（`.doc/LAYERING_CONVENTIONS.md:28-30`）。

出力: simulator/tests/fixtures/split_entry/js_golden_cases.json（追跡対象）
    ケース＝方向 × 分割本数 × 重みパターン × ロット単位 × 建て制約 × 利確有無 の格子＋
    分岐（stop_invalid / round_zeroed / immediate_lc / margin_binds / 合計ロット 0）の境界ケース。

JSON 表現の約束:
    `cap_lot` は「制限なし」を表す無限大を取りうる。標準 JSON に無限大の表現が無いため
    **文字列 "Infinity"** で書き出す（JS 側は `Infinity` へ復元する）。`null` を使うと
    「値が無い」と区別できないため採らない。

決定論: 乱数・実データ非依存。いつ再生成しても同一の JSON を出す。
再生成: <venv python> simulator/tools/export_split_entry_fixtures.py
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import fields
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from simulator.usecase.split_entry_plan import (  # noqa: E402
    SplitEntryPlan, SplitEntrySpec, build_split_entry_plan,
)

OUT = _REPO / "simulator" / "tests" / "fixtures" / "split_entry" / "js_golden_cases.json"

#: 無限大の JSON 表現（上記「JSON 表現の約束」）
INFINITY_TOKEN = "Infinity"

#: 参照実装 :278-289 / :355-362 / :415 の既定値
_BALANCE = 172000.0
_POINT_VALUE = 1.0
_MARGIN_RATE = 0.10
_WIN_RATE = 0.38
_STOP = 58340.0
#: 参照実装 :931 の direct 既定シード（P₀=58700・g=1000）
_ENTRIES = {
    1: (58700.0,),
    2: (58700.0, 59700.0),
    3: (58700.0, 59700.0, 60700.0),
}
_SHORT_ENTRIES = {
    1: (58700.0,),
    2: (58700.0, 57700.0),
    3: (58700.0, 57700.0, 56700.0),
}
_SHORT_STOP = 59060.0
#: ハーフケリー（p=38%, R=2.74 の閉形式・MC 非依存＝決定論）
_HALF_KELLY = 0.07686131386861314


def specs() -> "list[SplitEntrySpec]":
    """fixture に載せる入力の全列挙（鮮度検定と共有する唯一源）。"""
    out: "list[SplitEntrySpec]" = []
    for direction in ("long", "short"):
        entries_by_k = _ENTRIES if direction == "long" else _SHORT_ENTRIES
        stop = _STOP if direction == "long" else _SHORT_STOP
        take = 61500.0 if direction == "long" else 55900.0
        for splits in (1, 2, 3):
            for pattern in ("equal", "linear", "double"):
                for lot_mode in ("int", "dec"):
                    for cap_basis in ("margin", "lc"):
                        for take_price in (None, take):
                            out.append(SplitEntrySpec(
                                direction=direction,
                                entry_prices=entries_by_k[splits],
                                stop_price=stop,
                                fraction=_HALF_KELLY,
                                balance=_BALANCE,
                                point_value=_POINT_VALUE,
                                margin_rate=_MARGIN_RATE,
                                win_rate=_WIN_RATE,
                                take_price=take_price,
                                weight_pattern=pattern,
                                lot_mode=lot_mode,
                                cap_basis=cap_basis,
                            ))
    # 分岐の境界ケース（参照実装と同一条件で立つことを JS 側でも固定する）
    base = dict(entry_prices=_ENTRIES[3], stop_price=_STOP, fraction=_HALF_KELLY,
                balance=_BALANCE, point_value=_POINT_VALUE, margin_rate=_MARGIN_RATE,
                win_rate=_WIN_RATE, direction="long")
    out.extend([
        # custom 重み
        SplitEntrySpec(**base, weight_pattern="custom", custom_weights=(3.0, 1.0, 2.0)),
        # stop_invalid（ロングで損切りが建値より上）
        SplitEntrySpec(**{**base, "entry_prices": _ENTRIES[2], "stop_price": 59000.0}),
        # round_zeroed（整数切り捨てで全建玉 0）
        SplitEntrySpec(**{**base, "fraction": 0.0005}),
        # immediate_lc / margin_binds（使用率 100% 超）
        SplitEntrySpec(**{**base, "fraction": 0.9, "cap_basis": "margin"}),
        SplitEntrySpec(**{**base, "fraction": 0.4}),
        # cap_lot=Infinity（合計ロット 0）
        SplitEntrySpec(**{**base, "entry_prices": _ENTRIES[1], "stop_price": 40000.0}),
        # f=0（賭けない）
        SplitEntrySpec(**{**base, "fraction": 0.0}),
        # 円換算 V≠1
        SplitEntrySpec(**{**base, "entry_prices": _ENTRIES[2], "point_value": 10.0,
                          "balance": 1720000.0, "lot_mode": "dec"}),
        # 証拠金率の別値
        SplitEntrySpec(**{**base, "margin_rate": 0.20}),
    ])
    return out


def _case_id(spec: SplitEntrySpec, index: int) -> str:
    take = "tp" if spec.take_price is not None else "notp"
    return (f"{index:03d}/{spec.direction}/K{spec.splits}/{spec.weight_pattern}"
            f"/{spec.lot_mode}/{spec.cap_basis}/{take}")


def _encode(value):
    if isinstance(value, tuple):
        return [_encode(v) for v in value]
    if isinstance(value, float) and math.isinf(value):
        return INFINITY_TOKEN
    return value


def case_payload(spec: SplitEntrySpec, index: int) -> dict:
    plan: SplitEntryPlan = build_split_entry_plan(spec)
    return {
        "id": _case_id(spec, index),
        "spec": {
            "direction": spec.direction,
            "entry_prices": list(spec.entry_prices),
            "stop_price": spec.stop_price,
            "take_price": spec.take_price,
            "fraction": spec.fraction,
            "balance": spec.balance,
            "point_value": spec.point_value,
            "margin_rate": spec.margin_rate,
            "win_rate": spec.win_rate,
            "weight_pattern": spec.weight_pattern,
            "custom_weights": (list(spec.custom_weights)
                               if spec.custom_weights is not None else None),
            "lot_mode": spec.lot_mode,
            "cap_basis": spec.cap_basis,
        },
        "expected": {f.name: _encode(getattr(plan, f.name)) for f in fields(SplitEntryPlan)},
    }


def build_payload() -> dict:
    return {
        "authority": "simulator/usecase/split_entry_plan.py（build_split_entry_plan）",
        "source_spec": "integrated_position_sizing_calculator.html Step 3"
                       "（:880-888, :956-1031。pmode='direct' 状態＝TBD-1 裁定）",
        "tolerance": {
            "default": "厳密一致（許容 0）",
            "losscut": "losscut_price / losscut_distance と、cap_basis='lc' のときの"
                       " cap_lot / scale / buildable_lot / effective_risk は権威"
                       " account_engine.official_losscut_price 経由（複製禁止）のため"
                       " 参照実装 HTML とは最終桁が異なる。JS↔Python 間は同一手順のため厳密一致する。",
        },
        "infinity_token": INFINITY_TOKEN,
        "cases": [case_payload(spec, i) for i, spec in enumerate(specs())],
    }


def main() -> None:
    payload = build_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(payload['cases'])} ケース → {OUT}")


if __name__ == "__main__":
    main()
