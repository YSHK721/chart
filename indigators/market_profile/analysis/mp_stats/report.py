"""StepResult 集約・打ち切り（censoring）・JSON / Markdown レポート出力。

decision 固定語彙:
  reject / fail_to_reject      … 検定ステップ（Step2c, Step3）
  negligible / non_negligible  … 推定ステップ（Step1）
  inconclusive                 … CI が判定帯を跨ぐ
  skipped                      … 打ち切り・未実装（理由を notes に明記）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DECISIONS = (
    "reject",
    "fail_to_reject",
    "negligible",
    "non_negligible",
    "inconclusive",
    "estimated",   # 推定・定義ステップ（Step5 の POC* 確定など・検定なし）
    "skipped",
)


@dataclass(frozen=True)
class StepResult:
    step: int                    # 1..8（2 は 2-0/2a/2b/2c を内包）
    name: str
    decision: str
    statistics: "dict[str, object]" = field(default_factory=dict)
    variants: "dict[str, dict]" = field(default_factory=dict)
    flags: "tuple[str, ...]" = ()
    notes: str = ""

    def __post_init__(self):
        assert self.decision in DECISIONS, f"unknown decision: {self.decision}"

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "name": self.name,
            "decision": self.decision,
            "statistics": self.statistics,
            "variants": self.variants,
            "flags": list(self.flags),
            "notes": self.notes,
        }


_FUTURE_STEPS = (
    (4, "hurst_variance_ratio"),
    (5, "excess_occupancy_null_b"),
    (6, "va_open_conditional"),
    (7, "spa_multi_rule"),
    (8, "oos_calibration"),
)


def censor_future_steps(results: "list[StepResult]") -> "list[StepResult]":
    """Step3 が fail_to_reject なら 4..8 を skipped で埋める（打ち切り規則の機械化）。"""
    step3 = next((r for r in results if r.step == 3), None)
    censored = step3 is not None and step3.decision == "fail_to_reject"
    reason = (
        "censored: step3 fail_to_reject → MP search terminated"
        if censored
        else "not implemented in this phase (steps 1-3 only)"
    )
    out = list(results)
    for num, name in _FUTURE_STEPS:
        if not any(r.step == num for r in out):
            out.append(StepResult(step=num, name=name, decision="skipped", notes=reason))
    return sorted(out, key=lambda r: r.step)


def build_report(results: "list[StepResult]", meta: dict) -> dict:
    results = censor_future_steps(results)
    step3 = next((r for r in results if r.step == 3), None)
    censoring = {
        "stopped_after": 3 if (step3 and step3.decision == "fail_to_reject") else None,
        "rule": "step3 fail_to_reject → MP search terminated",
    }
    return {
        "meta": meta,
        "steps": [r.to_dict() for r in results],
        "censoring": censoring,
    }


def write_json(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


def write_markdown(report: dict, path: Path) -> None:
    lines = ["# MP (TPO/POC) 情報価値検定レポート", ""]
    meta = report["meta"]
    lines += [
        f"- 期間: {meta.get('period')}  営業日数: {meta.get('n_days')}",
        f"- seed: {meta.get('seed')}  B: {meta.get('B')}  主変種: {meta.get('primary_variant')}",
        f"- 除外ブラケット (2-0): {meta.get('excluded_brackets')}",
        "",
        "依存注記: simulator/adapter/validation/spa.py（定常ブートストラップ・PW ブロック長）、"
        "var_backtests.norm_cdf を read-only 再利用（private import は本分析に閉じる）。",
        "",
    ]
    for s in report["steps"]:
        lines.append(f"## Step {s['step']}: {s['name']} — **{s['decision']}**")
        if s["statistics"]:
            lines.append("")
            lines.append("| 統計量 | 値 |")
            lines.append("|---|---|")
            for k, v in s["statistics"].items():
                lines.append(f"| {k} | {_fmt(v)} |")
        if s["variants"]:
            lines.append("")
            lines.append("| 変種 | 要約 |")
            lines.append("|---|---|")
            for k, v in s["variants"].items():
                summary = ", ".join(f"{kk}={_fmt(vv)}" for kk, vv in v.items())
                lines.append(f"| {k} | {summary} |")
        if s["flags"]:
            lines.append("")
            lines.append(f"flags: {', '.join(s['flags'])}")
        if s["notes"]:
            lines.append("")
            lines.append(s["notes"])
        lines.append("")
    c = report["censoring"]
    if c["stopped_after"]:
        lines.append(f"**打ち切り**: Step {c['stopped_after']} で終了（{c['rule']}）")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
